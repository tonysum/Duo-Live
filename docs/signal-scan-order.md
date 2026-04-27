# 信号扫描的漏斗顺序与排序策略

本文档说明 `live/rolling_scanner.py` 中 R24 raw-surge 的候选筛选顺序与 `top_n` 截断时
采用的排序依据，便于将来切换策略时有据可查。

**更完整的「从简到繁」组合思路与 moonshot 对照**：见 [`r24-signal-scan-composition.md`](r24-signal-scan-composition.md)。

## 现行漏斗（与 duo-moonshot paper 对齐）

```
全部 USDT 永续  ──(1) priceChangePercent ≥ raw_min_pct_chg──▶ 涨幅合格集合
                                                            │
                                                            ▼ (2) 按涨幅倒序截 max_sr_probe（防 REST 洪水）
                                                            探测集
                                                            │
                                                            ▼ (3) 逐个算 sell_surge_ratio，保留 sr > raw_min_sell_surge，
                                                            可选 ``raw_min_yavg_sell_volume`` 过滤昨均小时卖额
                                                            卖量合格集合（= 涨幅 ∩ 卖量 [∩ 流动性]）
                                                            │
                                                            ▼ (4) 按 ``candidate_rank_mode`` 取 top_n（``sr`` | ``pct_log_sr`` | ``pct_log_sr_liq``，与 moonshot 一致）
                                                            最终候选
                                                            │
                                                            ▼ (5) select_raw_surge_signals：min_pct_chg / 上市天数 / 可选卖量门
                                                            │
                                                            ▼ (6) 去重 + 冷却 → signal_queue
```

参考实现：`duo-moonshot/moonshot/paper/rolling_scanner.py:RawSurgeScanner.scan`。

## `top_n` 截断时的排序依据

由 ``RollingLiveConfig.candidate_rank_mode``（``data/config.json`` → ``rolling.candidate_rank_mode``）决定：

| 值 | 排序键（降序） |
|---|---|
| ``sr`` | 仅卖量倍数，与 paper ``RawSurgeScanner`` 一致 |
| ``pct_log_sr`` | ``pct_chg × log(1 + sr)`` |
| ``pct_log_sr_liq`` | 上式再乘 ``max(log(1+yavg), 0.2)``（昨均小时卖额） |

### 为什么选 sr 降序

- 与 paper `RawSurgeScanner` 语义对齐，避免实盘与回测/纸盘策略行为发散。
- R24 raw-surge 是做空策略，sr 越大代表当前小时抛压相对昨日均值越强，动量反转的先导信号更硬。
- 涨幅在 Step 1 已作为粗筛门槛（`raw_min_pct_chg`），进入 Step 4 的候选涨幅差异不如 sr 差异有信息量。

### 将来可能调整的方向

| 候选排序 | 动机 | 风险/代价 |
|---|---|---|
| **涨幅 `pct_chg` 降序** | 更简单、直觉上"涨得最猛的先做空"；容易解释 | 忽略抛压结构差异，sr=11 的币可能被 sr=10.5 但涨幅更高的挤出 top_n |
| **复合：`pct_chg × sr`** 或 `log(sr) + α·pct_chg` | 兼顾涨幅与抛压 | 需回测定系数，增加维护成本 |
| **按 `sr / pct_chg` 降序** | 找"涨得不太多但抛压异常高"的反转候选 | 和 raw-surge 语义偏离，不建议 |

### 如何切换

在 ``data/config.json`` 的 ``rolling`` 里设置 ``candidate_rank_mode``（或改
``RollingLiveConfig`` 默认值）。新增模式需在 ``live/rolling_scanner.py`` 的
``_candidate_rank_score`` 中实现。

## 相关配置项

| 字段 | 作用 | 当前值 |
|---|---|---|
| `raw_min_pct_chg` | Step 1 涨幅门槛（百分点） | 10.0 |
| `raw_min_sell_surge` | Step 3 卖量门槛（严格大于） | 10.0 |
| `candidate_rank_mode` | Step 4 截断排序：`sr` 或 `pct_log_sr` | 见仓库 `config.json` |
| `max_sr_probe`（可选，新增） | Step 2 探测集硬上限，防 REST 洪水 | 代码默认 50 |
| `top_n` | Step 4 最终候选数 | 5 |
| `min_pct_chg` | Step 5 涨幅二次门 | 10.0 |
| `min_listed_days` | Step 5 上市天数门 | 10 |

`max_sr_probe` 目前不在 `RollingLiveConfig` 里，`_scan` 通过 `getattr` 读取，默认
50。如需从 `data/config.json` 注入，在 `rolling_config.py` 的 dataclass 里补一个
`max_sr_probe: int = 50` 字段即可。

## 历史背景

- 在 `duo-live` 最初版本里，`top_n` 截断发生在 Step 1 之后（先按涨幅截 top_n，再对这 top_n
  个算 sr）。这与 paper 不等价，尤其 `top_n=1` 时，只要涨幅第一的币 sr 不达标，整轮扫描 0
  信号——2026-04-20 线上就因此 24 小时一单未开。
- 本次（2026-04-20）改为"涨幅合格集合 → 卖量门 → 按 sr 降序截 top_n"，对齐 paper 实现。
