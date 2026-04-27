# R24 raw-surge 信号扫描：组合思路（从简到繁）

本文档把 **duo-live** 与 **duo-moonshot** 里 R24 raw-surge 相关的扫描逻辑，整理成一条可逐步加深的路线，便于你在 **moonshot** 的 `r24_raw_surge` / `RawSurgeScanner` 上按阶段迭代。

- **实现参考（live）**：`live/rolling_scanner.py`、`live/raw_surge_signal.py`、`live/sell_surge_binance.py`
- **实现参考（paper）**：`duo-moonshot/moonshot/paper/rolling_scanner.py`、`moonshot/paper/live_feed.py`
- **漏斗与排序字段**：`docs/signal-scan-order.md`（偏 duo-live 配置说明）

---

## 1. 策略在做什么（一句话）

在 **USDT 永续** 全市场里，找 **24h 涨幅已够高**、且 **相对昨日异常放量主动卖** 的币，作为 **做空** 候选；再用 **上市天数、冷却、持仓上限** 等约束控制频率与风险。

---

## 2. 卖量倍数 `sr` 的定义（两仓共用）

对某个 UTC 时刻，取 **一根 1h K** 上的：

\[
\text{主动卖额(quote)} = \text{成交额} - \text{taker 买入额}
\]

再取 **前一 UTC 自然日** 的 **1d K** 上同日主动卖额，除以 24 得到「昨日每小时平均主动卖额」：

\[
sr = \frac{\text{该 1h 主动卖额}}{\text{昨日日均每小时主动卖额}}
\]

**注意（与 paper 对齐）**：扫描时应使用 **上一根已收盘的 1h K**（paper 里为 `prev_hour = hour_floor - 1h`），不要用「当前未收盘小时」的 K，否则同一币在 paper 与 live 上 **sr 数值会差一个量级**，与回测/纸盘不一致。

---

## 3. 从简到繁：六层结构

下列编号 **由简入繁**；每一层可以单独开关或替换，建议在 moonshot 里 **先对齐第 0～2 层**，再考虑 3～5。

### 第 0 层：数据源与标的范围

| 项 | 建议 |
|---|---|
| 涨幅来源 | Binance `/fapi/v1/ticker/24hr` 的 `priceChangePercent`（全市场一次拉取） |
| 标的集合 | `exchangeInfo` 过滤：`USDT` + `PERPETUAL` + `TRADING` |
| 无额外依赖 | 先不要用链上/情绪数据 |

### 第 1 层：涨幅硬门槛（粗筛）

- 条件：`pct_chg >= raw_min_pct_chg`（例如 10 表示 10%）。
- **不要**在这一步就按 `top_n` 截断到「涨幅前 N 名再算 sr」——否则会出现「涨幅第一名的币 sr 不够 → 整轮 0 信号」，而 **涨幅第二、三名可能 sr 很高** 却被丢掉。
- 可选：对「涨幅合格集合」按涨幅排序后，只取前 **K 名做 REST 探测**（防请求风暴），K 与 `top_n` 不是同一概念（例如 K=50，`top_n`=5）。

### 第 2 层：卖量硬门槛（与涨幅求交集）

- 条件：`sr > raw_min_sell_surge`（实现里多为 **严格大于**）。
- 语义：**涨幅够高的集合** ∩ **卖量够异常的集合**。
- 阈值 **10 倍** 在实盘中往往过严；是否放宽应靠 **日志里 sr 分布** 与回测，而不是凭感觉。

### 第 3 层：`top_n` 截断前的排序（组合思路）

仅通过第 1、2 层后，常会剩 **多个** 合格币，需排序再取前 `top_n`。

| 复杂度 | 模式 | 公式/行为 | 适用 |
|--------|------|-----------|------|
| 最简单 | **仅 `sr` 降序** | 抛压倍数最大优先 | 与早期 paper `RawSurgeScanner` 一致；偏「卖压叙事」 |
| 推荐折中 | **`pct × log(1+sr)` 降序** | 同时抬高「涨得多」与「卖得多」；`log` 缓和 sr 极端值 | 减轻「sr 极高但涨幅一般」的插针/爆仓噪声（相对纯 sr） |
| 可选 | **仅 `pct` 降序** | 谁涨得猛优先 | 偏「波动率/做空标的」叙事，可能忽略抛压结构 |
| 可选 | **分层**：先按 pct 取前 M，再按 sr 取 top_n | 强约束「涨幅第一梯队」 | 实现稍繁，调参多 |

**说明**：第 1 层已要求 `pct ≥ 阈值`，第 3 层是在 **合格子集内** 再平衡「涨幅 vs 卖量」，不是重复门槛。

### 第 4 层：`select_raw_surge_signals` 类二次规则

在候选已进入「最终名单」前后，仍可叠加（与 duo-live `raw_surge_signal.py` 一致）：

- `min_pct_chg`、上市日等：与扫描阶段协同（见 duo-live 当前实现）。
- `min_listed_days`：过滤过新标的。

（duo-live 已移除可配置的「二次卖量门控」三键；首道/扫描侧仍用 `raw_min_sell_surge` / `raw_max_sell_surge` 等。）

### 第 5 层：执行与风控（扫描之外）

- 同币种 **冷却**（按小时桶或按日）。
- **SL 后冷却**（可选禁止短时间再进同一币）。
- **持仓数 / 保证金 / 日亏限额**：与扫描解耦，但在「最终是否开仓」处必须统一。

### 第 6 层（进阶，非必须）：结构过滤

若未来要降低「上影插针、空头已爆完」类形态：

- 需要 **1h/4h OHLC** 或 **资金费率、持仓量** 等，复杂度和数据源成本明显上升；建议 **有第 0～5 层稳定收益与日志** 后再做。

---

## 4. moonshot 侧落地对照表

| 主题 | moonshot（paper）典型位置 | 建议 |
|------|---------------------------|------|
| 扫描入口 | `RawSurgeScanner.scan` | 保持与下表行为一致 |
| 涨幅候选 | `LiveFeed.scan_rolling_top_gainers` | `top_n=500` 拉满涨幅合格集再筛 sr（paper 已有注释） |
| `sr` 所用小时 | `sell_surge_ratio_at_hour(symbol, prev_hour)` | 保持 **prev_hour** |
| 截断排序 | 先 `select_signals`，再按 `sell_surge_ratio` 排序截 `top_n` | 若要 **pct×log(1+sr)**，在「截断 top_n」处改排序键，与 duo-live `candidate_rank_mode=pct_log_sr` 对齐 |
| 配置 | `RawSurgeR24Config` / JSON | 增加与 duo-live 对称字段：`candidate_rank_mode`、`max_sr_probe`（若实现探测上限） |

---

## 5. 推荐迭代顺序（给 moonshot 改策略时用）

1. **对齐漏斗顺序**：涨幅合格全集（或 capped 探测集）→ 算 sr → 排序 → `top_n`；不要先 `top_n` 再算 sr。  
2. **对齐 sr 时刻**：统一 **上一根已收盘 1h**。  
3. **对齐排序**：先上 **`pct_log_sr`**，保留配置可切回 **`sr`** 做 A/B。  
4. **再调阈值**：`raw_min_sell_surge`、`raw_min_pct_chg` 基于日志与回测。  
5. **最后** 再考虑第 6 层形态过滤。

---

## 6. 与本文档相关的仓库内链接

- 漏斗步骤与 duo-live 配置字段：`docs/signal-scan-order.md`
- 部署与 WebSocket（看板、日志）：`docs/nginx-deploy.md`
