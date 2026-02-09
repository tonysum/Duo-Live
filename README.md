# duo-live — Surge Short V2 Trading System

Binance USDS-M Futures 卖量暴涨做空策略。支持模拟交易和实盘交易，自动信号扫描 → 下单 → 止盈止损 → 超时平仓，全流程自动化。

## Quick Start

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 Binance API (实盘需要)
cp .env.example .env
# 编辑 .env 填入 BINANCE_API_KEY、BINANCE_API_SECRET

# 模拟交易
python -m live run

# 实盘交易 (需确认)
python -m live run --live
```

## CLI 命令

### 交易模式

```bash
# 模拟模式 (默认)
python -m live run

# 实盘模式
python -m live run --live

# 自定义保证金和亏损限额
python -m live run --live --margin 50 --loss-limit 100
```

### 手动下单

```bash
# 按数量下单 (做空)
python -m live order ETHUSDT 2500 0.2

# 按保证金下单
python -m live order ETHUSDT 2500 --margin 100

# 做多 + 自定义 TP/SL
python -m live order ETHUSDT 2500 --margin 100 --long --tp 20 --sl 10
```

### 持仓管理

```bash
python -m live positions              # 查看持仓
python -m live orders                 # 查看挂单
python -m live close ETHUSDT          # 市价平仓
python -m live tp ETHUSDT 2300        # 手动挂止盈
python -m live sl ETHUSDT 2600        # 手动挂止损
python -m live cancel ETHUSDT 12345   # 取消单个订单
python -m live cancel-all ETHUSDT     # 取消全部订单
```

### 状态查看（模拟模式）

```bash
python -m live status                 # 资金 & 持仓概览
python -m live trades                 # 历史成交
python -m live signals                # 信号历史
```

### 通知测试

```bash
python -m live test-notify            # 发送测试消息
python -m live test-notify 自定义消息   # 发送自定义消息
```

## 实盘安全机制

| 机制 | 说明 |
|------|------|
| **启动确认** | `run --live` 启动时显示配置摘要，需输入 `yes` 确认 |
| **固定保证金** | 每笔固定 100 USDT（`--margin N` 可调，`0` = 按比例） |
| **每日亏损限额** | 默认 200 USDT（`--loss-limit N` 可调，`0` = 不限） |
| **自动止盈止损** | 入场成交后自动挂 TP/SL 条件单 |
| **超时平仓** | 持仓超 72h 自动市价平仓 |
| **断线恢复** | 重启后自动同步 Binance 持仓，继续监控 |
| **请求重试** | 网络错误自动重试 3 次（1s → 2s → 4s 指数退避） |
| **Telegram 通知** | 入场 / 成交 / TP / SL / 超时 / 亏损限额触发 |

## 环境变量

```bash
# .env
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret

# Telegram 通知 (可选)
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=your_chat_id
```

## 配置参数

核心参数在 `live/live_config.py` 中定义：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `leverage` | 4 | 杠杆倍数 |
| `max_positions` | 10 | 最大持仓数 |
| `live_fixed_margin_usdt` | 100 | 每笔保证金 (USDT) |
| `daily_loss_limit_usdt` | 200 | 每日亏损限额 (USDT) |
| `strong_tp_pct` | 33% | 强势币止盈 |
| `medium_tp_pct` | 21% | 中等币止盈 |
| `weak_tp_pct` | 10% | 弱势币止盈 |
| `stop_loss_pct` | 18% | 止损 |
| `max_hold_hours` | 72 | 最大持仓时间 |
| `surge_threshold` | 10x | 信号触发倍数 |

## 架构

```
duo-live/
├── live/
│   ├── __main__.py              # CLI 入口
│   ├── paper_trader.py          # 主服务编排
│   ├── live_scanner.py          # 信号扫描 (每小时)
│   ├── paper_executor.py        # 模拟订单执行
│   ├── live_executor.py         # 实盘订单执行
│   ├── position_monitor.py      # 模拟持仓监控 (V2 退出逻辑)
│   ├── live_position_monitor.py # 实盘持仓监控 (TP/SL/超时/断线恢复)
│   ├── paper_store.py           # SQLite 持久化 (含实盘交易记录)
│   ├── notifier.py              # Telegram 通知
│   ├── binance_client.py        # Binance Futures REST 客户端
│   ├── binance_models.py        # Pydantic 数据模型
│   ├── live_config.py           # 配置参数
│   ├── live_queries.py          # 查询展示 (orders/positions)
│   ├── models.py                # SurgeSignal 模型
│   └── risk_filters.py          # 风控过滤器
├── data/
│   └── paper_trades.db          # SQLite 数据库 (自动创建)
├── .env                         # 环境变量 (API keys)
├── requirements.txt
└── pyproject.toml
```

## 信号流程

```
每小时扫描 → 发现暴涨信号 → 60s 收集期 → 风控过滤 → 执行下单
                                                  ↓
                                            入场单提交
                                                  ↓
                                        监控成交 (30s 轮询)
                                                  ↓
                                          成交后挂 TP/SL
                                                  ↓
                                    TP 触发 / SL 触发 / 超时平仓
                                                  ↓
                                          Telegram 通知
```

## 数据库

SQLite 存储在 `data/paper_trades.db`，包含以下表：

- `paper_positions` — 模拟持仓
- `paper_trades` — 模拟成交记录
- `paper_equity_snapshots` — 权益快照
- `paper_signal_events` — 信号事件
- `live_trades` — 实盘交易记录 (entry/tp/sl/timeout)
- `paper_state` — 状态键值对

## 依赖

- `httpx` — 异步 HTTP 客户端
- `pydantic` — API 响应验证
- `rich` — 控制台格式化输出
- `python-dotenv` — 环境变量加载
- `sqlite3` — 内置数据库
