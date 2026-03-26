# Duo-Live — Binance U 本位合约实盘（R24）

基于 **Binance USDS-M Futures** 的自动化交易：`Rolling R24` 策略按 **24h 涨跌幅** 扫描全市场 USDT 永续，在满足条件时 **做空** 入场；入场后由 `LiveOrderExecutor` 与 `LivePositionMonitor` 管理 TP/SL（条件单）、时间阶梯止盈、追踪止损、加仓与最大持仓天数；并提供 **FastAPI 面板 API**、**Vite + React 看板**、**Telegram 通知 / Bot** 与 **可选邮件告警**。

> 默认策略为 **RollingLiveStrategy（R24）**，不再使用历史上的「卖量暴涨 / SurgeShort」扫描与独立 `risk_filters` 模块。

---

## 文档（`docs/`）

- [Nginx 反向代理部署](docs/nginx-deploy.md)
- [止盈挂单方向说明与日志](docs/FIX_WRONG_TP_ORDERS.md)
- [邮件报警快速配置](docs/QUICK_START_EMAIL.md)

---

## 目录

- [系统特点](#系统特点)
- [架构总览](#架构总览)
- [仓库与模块](#仓库与模块)
- [信号与入场流程](#信号与入场流程)
- [策略与参数（R24）](#策略与参数r24)
- [Web Dashboard](#web-dashboard)
- [Telegram](#telegram)
- [CLI](#cli)
- [配置说明](#配置说明)
- [环境变量](#环境变量)
- [部署](#部署)
- [开发与测试](#开发与测试)
- [依赖](#依赖)

---

## 系统特点

### 信号（R24 扫描）

- 使用 **全市场 `/fapi/v1/ticker/24hr` 单次拉取**，按 **24h 涨幅百分比** 筛选（默认 ≥ `min_pct_chg`），取 **Top N**（`top_n`）。
- 按 **`scan_interval_hours`** 在 UTC 整点附近对齐扫描（启动时会立刻扫一次）。
- 复用 `SurgeSignal`：`surge_ratio` 字段存放 **24h 涨跌幅百分比**（非卖量比）。
- **同币种信号冷却**：`signal_cooldown_hours`；**止损当日** 同一标的可经 `add_sl_cooldown` 屏蔽再入场。

### 交易与风控（基础设施层）

- **自动交易开关**：默认关闭；需 `python -m live run --auto-trade` 或在前端打开，才会对信号真实下单。
- **仓位与资金**：`max_positions`、`max_entries_per_day`、`daily_loss_limit_usdt`；单笔保证金 `live_fixed_margin_usdt` 或 `margin_mode` + `margin_pct`。
- **队列侧批量**：信号先入队，消费端 **合并当前队列 + 等待 10s**，再按涨幅排序、按空余槽位 **串行** 尝试入场。

### 持仓管理（策略 + 监控）

- **R24 时间阶梯止盈**：持仓未满 `tp_hours_threshold` 小时用 `tp_initial`（比例）；之后降为 `tp_reduced`；若已加仓则用 `tp_after_add`。
- **固定止损比例**：`sl_threshold`（由策略写入 `EntryDecision` 的 SL%）。
- **追踪止损**：激活 `trailing_activation_pct`、回弹 `trailing_distance_pct`（可选关闭）。
- **逆势加仓**：可选 `enable_add_position`，价格相对入场涨至 `add_position_threshold` 时触发（详见 `rolling_live_strategy.py`）。
- **最大持仓时间**：`max_hold_days`（按天 ×24 换算为小时与 `evaluate_position` / 监控协同）。
- **REST + WebSocket**：条件单缺失、误撤、方向校验等见 `live_position_monitor.py` 与 [FIX_WRONG_TP_ORDERS.md](docs/FIX_WRONG_TP_ORDERS.md)。

### 其他

- **SQLite**：`TradeStore` 持久化信号事件与实盘平仓记录。
- **内存守护**：约 **500 MB** 警告、**800 MB** 退出（`trader.py`）。
- **Dashboard API**：默认 **`0.0.0.0:8899`**；前端开发见 `web/`（生产默认 `3000`，以 `ecosystem.config.js` / `web/start.sh` 为准）。

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                      LiveTrader                                  │
│  RollingLiveScanner ──signal_queue──► _process_signals()        │
│       (R24, 24h ticker)              (10s 批次 + 槽位 + 自动交易)  │
│                                         │                         │
│                                         ▼                         │
│                    RollingLiveStrategy.filter_entry / open...     │
│                    LiveOrderExecutor + LivePositionMonitor       │
│  BinanceUserStream ──► monitor.handle_order_update (成交流)        │
│  BinanceFuturesClient (REST)                                      │
│  FastAPI (:8899) ◄── create_app(self)  TradeStore (SQLite)      │
└─────────────────────────────────────────────────────────────────┘
         ▲                                    │
         │  HTTP / WS                          │ Telegram / 邮件
    Vite 静态站 :3000 (serve dist)      notifier.py
```

---

## 仓库与模块

```
duo-live/
├── live/
│   ├── __main__.py              # CLI：run / status / order / close …
│   ├── trader.py                # LiveTrader 编排
│   ├── strategy.py              # Strategy ABC，EntryDecision / PositionAction
│   ├── rolling_live_strategy.py # RollingLiveStrategy（R24）
│   ├── rolling_scanner.py       # RollingLiveScanner（24h 涨幅扫描）
│   ├── rolling_config.py        # RollingLiveConfig；从 data/config.json 的 rolling 读取
│   ├── live_config.py           # LiveTradingConfig；data/config.json
│   ├── live_executor.py         # 下单、TP/SL 条件单
│   ├── live_position_monitor.py # 持仓与条件单生命周期
│   ├── ws_stream.py             # 用户数据 WebSocket
│   ├── binance_client.py       # REST 封装
│   ├── binance_models.py
│   ├── api.py                   # Dashboard FastAPI
│   ├── store.py
│   ├── notifier.py
│   ├── telegram_bot.py
│   ├── live_queries.py          # CLI 展示辅助
│   └── models.py                # SurgeSignal 等
├── web/                         # Vite + React 看板（`npm run build` → dist/）
├── tests/
├── data/                        # trades.db、config.json（含可选 rolling 策略块）
├── ecosystem.config.js          # PM2
└── deploy.sh
```

| 模块 | 职责 |
|------|------|
| `RollingLiveScanner` | 24h 涨幅筛选、Top N、冷却、入队 |
| `RollingLiveStrategy` | 主力获利/新币天数/冷却、`evaluate_position` TP·SL·加仓·追踪 |
| `LiveTrader` | 扫描、消费信号、监控、WS、API、Bot、日报 |
| `LivePositionMonitor` | 入场后 TP/SL、补单、方向修正、`recover_positions` |
| `api.py` | `/api/*`、`/ws/live` 等与看板对接 |

---

## 信号与入场流程

1. **扫描**：`RollingLiveScanner` 请求 24hr ticker → 过滤 `min_pct_chg` → 排序取 `top_n` → `SurgeSignal` 入队。  
2. **消费**：`_process_signals` 合并队列 → **等待 10s** → 拉取交易所持仓 → 去掉已持仓品种 → 按 `max_positions` 截断 → **仅当 `auto_trade_enabled`** 时对每条信号调用执行逻辑。  
3. **过滤**：`RollingLiveStrategy.filter_entry`（主力获利区间、上市天数、`TradeStore` 信号冷却等）→ `EntryDecision`（默认 SHORT，TP/SL 百分比来自 `tp_initial`×100、`sl_threshold`×100）。  
4. **下单**：`LiveOrderExecutor` 等路径挂限价单；监控跟踪 `deferred_tp_sl`，成交后挂条件止盈止损。

---

## 策略与参数（R24）

### 代码默认（`RollingLiveConfig`）

| 字段 | 默认 | 含义 |
|------|------|------|
| `top_n` | 1 | 每次扫描取涨幅前 N |
| `min_pct_chg` | 10.0 | 最小 24h 涨幅% |
| `min_listed_days` | 10 | 上市天数过滤 |
| `signal_cooldown_hours` | 24 | 同币种信号间隔 |
| `scan_interval_hours` | 1 | 扫描间隔（小时） |
| `max_hold_days` | 11 | 最大持仓天数 |
| `tp_initial` / `tp_reduced` | 0.34 / 0.14 | 阶梯止盈（比例，非百分比数字） |
| `tp_hours_threshold` | 10 | 满 N 小时后切换为 `tp_reduced` |
| `tp_after_add` | 0.45 | 加仓后止盈比例 |
| `sl_threshold` | 0.44 | 止损比例 |
| `trailing_activation_pct` / `trailing_distance_pct` | 0.16 / 0.09 | 追踪止损 |
| `enable_add_position` / `add_position_threshold` | true / 0.36 | 加仓 |

策略数值（上表中的 `top_n`、`min_pct_chg`、`tp_initial` 等）写在 **`data/config.json`** 的 **`"rolling"`** 对象里；与资金相关的可变项写在同一文件的顶层（`leverage`、`max_positions` 等）。加载逻辑见 `rolling_config.load_rolling_from_config_json` 与 `LiveTradingConfig.load_from_file`。

### `data/config.json`

**顶层**（`LiveTradingConfig`，API/前端可写）：

| 字段 | 默认 | 含义 |
|------|------|------|
| `leverage` | 2 | 杠杆 |
| `max_positions` | 7 | 最大持仓数 |
| `max_entries_per_day` | 2 | 每日开仓次数上限 |
| `live_fixed_margin_usdt` | 5 | 固定保证金（USDT/笔） |
| `daily_loss_limit_usdt` | 50 | 日亏限额（0 不限） |
| `margin_mode` | fixed | fixed / percent |
| `margin_pct` | 2.0 | percent 模式下余额比例 |

`monitor_interval_seconds` 等仅在代码默认值中配置，不写入 JSON。

**`"rolling"`**（可选）：与上表「代码默认（RollingLiveConfig）」同名字段，用于覆盖 R24 扫描与止盈止损参数；缺少的字段沿用代码默认。

### 自定义策略

实现 `Strategy` 的三个抽象方法，在 `__main__.py` 的 `run` 分支中替换 `RollingLiveStrategy` 即可；需自行 `create_scanner` 与 R24 解耦。

---

## Web Dashboard

前端为 **SPA**（React Router 等，见 `web/src`）。开发时 Vite 默认 **`http://0.0.0.0:3000`**。API 基址由环境变量 **`VITE_API_URL`** 指定；未设置时默认 **`http://<当前主机>:8899`**（见 `web/src/lib/api.ts`）。常见路径：`/dashboard`、`/positions`、`/trades`、`/signals`、`/trading`、`/chart`、`/settings`。

---

## Telegram

- **Notifier**：入场、TP/SL、超时、限额、日报等（见 `notifier.py`）。  
- **Bot**：`/status`、`/positions`、`/trades`、`/close` 等（`telegram_bot.py`）。

---

## CLI

```bash
# 启动（自动交易默认关；可先开面板再打开关）
python -m live run
python -m live run --margin 50 --loss-limit 100 --auto-trade

python -m live status
python -m live positions
python -m live close BTCUSDT
python -m live test-notify
```

更多子命令见 `python -m live` 无参数或 `--help` 提示（以 `__main__.py` 为准）。

---

## 配置说明

- **资金与 R24 策略**：同一文件 **`data/config.json`**（顶层 = 资金/杠杆等；`rolling` = 策略参数）；API 保存顶层时会保留 `rolling` 块。  
- **示例环境变量**：`.env.example`。

---

## 环境变量

```bash
BINANCE_API_KEY=
BINANCE_API_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TRADING_PASSWORD=              # 看板手动下单（可选）
SMTP_EMAIL=
SMTP_PASSWORD=
ALERT_EMAIL=
```

邮件说明见 [docs/QUICK_START_EMAIL.md](docs/QUICK_START_EMAIL.md)。

---

## 部署

- **PM2**：`ecosystem.config.js`（后端 `python -m live run`，前端见 `web/start.sh`）。进程「距上次重启」时长还可用 `pm2 list` / `pm2 show duo-live-backend` 查看。  
- **一键脚本**：`./deploy.sh`（`git pull`、依赖、前端构建、PM2 重启）；脚本结束时会写入 **`data/deployed_at.txt`**（UTC 时间），供 `/api/status` 与看板显示「自上次部署起」累计时长（该文件已加入 `.gitignore`，按机保留）。  
- **反向代理**：[docs/nginx-deploy.md](docs/nginx-deploy.md)。  
- 生产需开放 API **8899**、前端 **3000**（或由 Nginx 统一 80/443）。

---

## 开发与测试

```bash
pip install -r requirements.txt   # 或 uv sync / pyproject
cp .env.example .env
python -m pytest tests/ -v
```

主要测试：`test_ws_handler.py`（WS 处理）、`test_tp_sl_detection.py`（TP/SL 与持仓校验）、`test_dynamic_tp.py`（R24 `evaluate_position`）、`test_email_alert.py`。

---

## 依赖

核心 Python：`httpx`、`pydantic`、`python-dotenv`、`rich`、`websockets`、`fastapi`、`uvicorn` 等（见 `pyproject.toml` / `requirements.txt`）。前端：**Vite 6 + React**，见 `web/package.json`；生产可用 `web/start.sh`（`serve dist`）或任意静态托管 `web/dist`。
