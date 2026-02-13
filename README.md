# Duo-Live — 自动化合约交易系统

Binance USDS-M Futures 自动化交易系统。信号扫描 → 风控过滤 → 自动下单 → 动态止盈止损 → 持仓管理 → Telegram 通知，全流程自动化。

内置 Web Dashboard（Next.js）和 Telegram Bot，支持远程监控和管理。

---

## 目录

- [系统特点](#系统特点)
- [架构总览](#架构总览)
- [核心模块详解](#核心模块详解)
- [信号流程](#信号流程)
- [策略系统](#策略系统)
- [风控过滤器](#风控过滤器)
- [Web Dashboard](#web-dashboard)
- [Telegram 集成](#telegram-集成)
- [CLI 命令](#cli-命令)
- [配置参数](#配置参数)
- [环境变量](#环境变量)
- [部署指南](#部署指南)
- [开发与测试](#开发与测试)
- [依赖](#依赖)

---

## 系统特点

### 全自动交易循环
- 每小时自动扫描全市场 USDT 永续合约
- 检测卖量暴涨信号（sell_volume / yesterday_avg ≥ 阈值）
- 60 秒信号收集窗口去重后批量入场
- 入场后自动挂 TP/SL 条件单

### 智能持仓管理
- **动态止盈**：2h / 12h 评估币种强度，自动调整 TP（强 33% / 中 21% / 弱 10%）
- **超时平仓**：持仓超 72h 自动市价平仓
- **实时监控**：WebSocket + REST 轮询双通道，毫秒级成交检测

### 多层风控
- 7 项独立风控过滤器（Premium 变化、CVD 新低、买卖加速度等）
- 每日亏损限额自动熔断
- 最大持仓数限制
- 每日最大开仓次数限制
- 入场串行化锁，防止并发超限

### 可插拔策略架构
- `Strategy` 抽象基类，支持自定义信号扫描、入场过滤、持仓评估
- 默认实现 `SurgeShortStrategy`，更换策略只需一行代码

### 高可用
- 断线自动重连（WebSocket 24h 主动重连 + 指数退避重试）
- 崩溃恢复：重启后自动同步 Binance 持仓，继续监控
- 内存守护：超 500MB 告警，超 800MB 自动退出
- SQLite 持久化所有信号事件和交易记录

### 远程管理
- Web Dashboard（Next.js）：实时看板、持仓管理、K 线图表、手动下单
- Telegram Bot：/status、/positions、/trades、/close 等命令
- Telegram 通知：入场、成交、止盈、止损、超时、亏损限额全覆盖

---

## 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                         LiveTrader (编排器)                           │
│                                                                      │
│  ┌─────────────┐  signal_queue  ┌────────────────┐                   │
│  │ LiveSurge   │───────────────▶│ Signal Consumer│                   │
│  │ Scanner     │  (asyncio.Q)   │ (60s 收集窗口)  │                   │
│  │ （每小时扫描）│                └───────┬────────┘                   │
│  └─────────────┘                        │                            │
│                                         ▼                            │
│  ┌─────────────┐   filter_entry  ┌─────────────────┐                 │
│  │  Strategy   │◀───────────────│  Entry Logic     │                 │
│  │  (风控+决策) │───────────────▶│  (sizing/order)  │                 │
│  └──────┬──────┘   EntryDecision└────────┬────────┘                  │
│         │                                │                           │
│         │ evaluate_position              ▼                           │
│         │                     ┌──────────────────┐                   │
│         └────────────────────▶│ LivePosition     │                   │
│                               │ Monitor          │                   │
│  ┌─────────────┐              │ (TP/SL/超时管理)  │                   │
│  │ WS Stream   │─────────────▶│                  │                   │
│  │ (实时成交)   │ 成交/触发回调  └──────────────────┘                   │
│  └─────────────┘                                                     │
│                                                                      │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Binance     │  │ LiveOrder│  │ Telegram │  │ TradeStore       │   │
│  │ Client      │  │ Executor │  │ Notifier │  │ (SQLite)         │   │
│  │ (REST API)  │  │ (下单器)  │  │ (通知)    │  │ (信号+交易记录)   │   │
│  └─────────────┘  └──────────┘  └──────────┘  └──────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
        ▲                                              ▲
        │ HTTP :8899                                   │
  ┌─────┴──────────┐                            ┌──────┴─────────┐
  │ FastAPI Server │                            │ Telegram Bot   │
  │ (Dashboard API)│                            │ (命令轮询)      │
  └─────┬──────────┘                            └────────────────┘
        │
  ┌─────┴──────────┐
  │ Next.js Web    │
  │ Dashboard :3000│
  └────────────────┘
```

---

## 核心模块详解

```
duo-live/
├── live/                          # 核心交易模块
│   ├── __main__.py                # CLI 入口 (15+ 子命令)
│   ├── trader.py                  # LiveTrader — 主编排器
│   ├── strategy.py                # Strategy ABC + SurgeShortStrategy
│   ├── live_scanner.py            # LiveSurgeScanner — 信号扫描器
│   ├── live_executor.py           # LiveOrderExecutor — 下单执行器
│   ├── live_position_monitor.py   # LivePositionMonitor — 持仓监控器
│   ├── ws_stream.py               # BinanceUserStream — WebSocket 实时流
│   ├── risk_filters.py            # RiskFilters — 7 项风控过滤器
│   ├── binance_client.py          # BinanceFuturesClient — REST API 客户端
│   ├── binance_models.py          # Pydantic 数据模型
│   ├── live_config.py             # LiveTradingConfig — 配置管理
│   ├── api.py                     # FastAPI Dashboard API (25+ 端点)
│   ├── store.py                   # TradeStore — SQLite 持久化
│   ├── notifier.py                # TelegramNotifier — 推送通知
│   ├── telegram_bot.py            # TelegramBot — 命令交互
│   ├── live_queries.py            # CLI 查询展示工具
│   └── models.py                  # SurgeSignal 模型
├── web/                           # Next.js 前端 Dashboard
│   └── app/
│       ├── dashboard/             # 账户总览页
│       ├── positions/             # 持仓 + 挂单页
│       ├── trades/                # 历史交易页
│       ├── signals/               # 信号历史页
│       ├── trading/               # 手动下单页
│       ├── chart/                 # K 线图表页
│       └── settings/              # 配置管理页
├── tests/                         # 测试文件
├── data/                          # SQLite 数据库 + 配置文件
├── ecosystem.config.js            # PM2 进程管理配置
├── deploy.sh                      # 一键部署脚本
└── run_forever.sh                 # 本地自动重启脚本
```

### 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| **编排器** | `trader.py` | 启动所有子服务，管理生命周期，消费信号队列，串行执行入场 |
| **策略** | `strategy.py` | 可插拔策略接口：信号扫描器创建、入场过滤决策、持仓评估 |
| **扫描器** | `live_scanner.py` | 每小时扫描全市场，检测卖量暴涨信号，去重后推入队列 |
| **执行器** | `live_executor.py` | 下限价单 + 挂 TP/SL 条件单，处理精度和持仓方向 |
| **监控器** | `live_position_monitor.py` | 轮询持仓状态，管理 TP/SL 订单，动态止盈，超时平仓，断线恢复 |
| **WS 流** | `ws_stream.py` | WebSocket 实时接收成交/触发事件，24h 自动重连 + 30min keepalive |
| **风控** | `risk_filters.py` | 7 项独立过滤器管线，任一拒绝即不入场，失败时放行 |
| **API 客户端** | `binance_client.py` | 36+ 方法封装 Binance Futures REST API，HMAC 签名 + 指数退避重试 |
| **Dashboard API** | `api.py` | FastAPI 25+ 端点：状态、持仓、订单、K 线、手动下单、配置管理 |
| **持久化** | `store.py` | SQLite 存储信号事件和交易记录，崩溃恢复用 |
| **通知** | `notifier.py` | Telegram 推送：入场、成交、止盈、止损、超时、日报 |
| **Bot** | `telegram_bot.py` | Telegram 命令交互：/status、/positions、/trades、/close |

---

## 信号流程

```
UTC 整点 + 5s
    │
    ▼
 扫描全市场 (~400 USDT 永续合约)
    │  sell_volume / yesterday_avg_sell ≥ 10x ?
    ▼
 信号推入 asyncio.Queue
    │
    ▼
 60 秒收集窗口 (去重 + 批量)
    │
    ▼
 逐个信号执行入场流程:
    │
    ├─ 基础检查: 持仓数 < max? 今日亏损 < 限额? 已有仓位?
    │
    ├─ strategy.filter_entry():
    │     ├─ 7 项风控过滤器
    │     └─ 返回 EntryDecision (side, tp_pct, sl_pct)
    │
    ├─ 计算仓位大小 (固定保证金 or 百分比模式)
    │
    ├─ 限价下单 (entry_price = signal_price × 1.005)
    │
    ├─ LivePositionMonitor 注册待跟踪持仓
    │     └─ deferred TP/SL (成交后自动挂出)
    │
    └─ Telegram 通知入场

 ┌─────────────────────────────────────────┐
 │        成交后持仓管理循环 (30s)            │
 ├─────────────────────────────────────────┤
 │ 1. WS 检测成交 → 挂 TP/SL 条件单          │
 │ 2. 每 30s 轮询持仓状态                    │
 │ 3. 2h/12h 评估强度 → 动态调整 TP          │
 │ 4. 超 72h → 超时市价平仓                  │
 │ 5. TP/SL 触发 → 清理 + 通知               │
 └─────────────────────────────────────────┘
```

---

## 策略系统

系统采用**策略模式（Strategy Pattern）** 将交易决策与基础设施解耦。

### Strategy 接口

```python
from live.strategy import Strategy, EntryDecision, PositionAction

class Strategy(ABC):
    def create_scanner(self, config, signal_queue, client, console) -> Any:
        """创建信号扫描器"""

    async def filter_entry(self, client, signal, entry_price, ...) -> EntryDecision:
        """入场过滤 → 返回是否入场 + 方向 + TP/SL"""

    async def evaluate_position(self, client, pos, config, now) -> PositionAction:
        """持仓评估 → 返回 hold / close / adjust_tp"""
```

### 默认策略：SurgeShortStrategy

| 方法 | 逻辑 |
|------|------|
| `create_scanner` | 返回 `LiveSurgeScanner`（卖量暴涨检测） |
| `filter_entry` | 运行 7 项风控过滤器 → 返回 SHORT + TP 33% / SL 18% |
| `evaluate_position` | 2h/12h 评估币种强度，动态调整 TP；72h 超时平仓 |

### 切换策略

```python
# live/__main__.py 中只需改一行:
from .strategy import MyNewStrategy
trader = LiveTrader(config=config, strategy=MyNewStrategy())
```

新策略只需继承 `Strategy` 并实现 3 个方法，无需修改 `trader.py` 或 `live_position_monitor.py`。

---

## 风控过滤器

`RiskFilters` 提供 7 项独立过滤器，**全部基于 Binance API 实时数据**，无需外部数据库。

| # | 过滤器 | 检测内容 | 拒绝条件 |
|---|--------|----------|----------|
| 1 | Premium 24h 变化 | 24h 基差变化 | 下降超 -40% |
| 2 | 入场涨幅 | 信号价 → 入场价涨幅 | 超过阈值 |
| 3 | CVD 新低 | 累积量能 Delta | 窗口期内创新低（恐慌卖出衰竭） |
| 4 | 实时 Premium | 当前基差率 | 负基差过大（做空成本高） |
| 5 | 买量加速度 | 近 6h vs 前 18h 买卖比加速 | 落入危险区间 |
| 6 | 连续买量 | N 小时连续买量暴涨 | 持续买压（不利做空） |
| 7 | 买卖比 | 12h 内最大小时买卖比 | 比率落入危险区间 |

所有过滤器**失败时放行（fail-open）**，确保网络问题不会阻止交易。

---

## Web Dashboard

Next.js 前端共 7 个页面，部署在 `:3000` 端口，通过 FastAPI 后端（`:8899`）获取数据。

| 页面 | 路由 | 功能 |
|------|------|------|
| **总览** | `/dashboard` | 余额、今日盈亏、持仓数、浮动盈亏 |
| **持仓** | `/positions` | 当前持仓列表 + 挂单列表，支持一键平仓 |
| **交易** | `/trades` | 历史成交记录（自动配对入场/出场） |
| **信号** | `/signals` | 信号历史 + 实时价格 + 24h 涨跌 |
| **下单** | `/trading` | 手动下单（市价/限价 + TP/SL + 交易密码） |
| **图表** | `/chart` | K 线图表（多周期、缩放、平移） |
| **设置** | `/settings` | 在线修改配置（杠杆、保证金、限额等） |

前端自动适配访问者 hostname，局域网和公网均可访问。

---

## Telegram 集成

### 通知推送（TelegramNotifier）

自动推送以下事件：

| 事件 | 内容 |
|------|------|
| 📋 入场单提交 | 币种、方向、价格、数量、保证金 |
| ✅ 入场成交 | 成交价格 |
| 🎯 TP/SL 挂出 | 止盈/止损价格 |
| 🎯 止盈触发 | 币种、方向 |
| 🛑 止损触发 | 币种、方向 |
| ⏰ 超时平仓 | 币种、持仓时长 |
| 🚨 亏损限额 | 今日盈亏、限额 |
| 📈 每日报告 | 余额、盈亏、持仓数（每 4h） |

### 命令交互（TelegramBot）

| 命令 | 功能 |
|------|------|
| `/status` | 账户余额 + 今日盈亏 |
| `/positions` | 当前持仓详情 |
| `/trades` | 最近交易记录 |
| `/close SYMBOL` | 远程强制平仓 |
| `/help` | 命令列表 |

---

## CLI 命令

### 启动交易

```bash
python -m live run                               # 实盘交易 (默认)
python -m live run --margin 50 --loss-limit 100   # 自定义保证金和亏损限额
python -m live run --auto-trade                   # 启动时自动开启自动交易
```

### 手动下单

```bash
python -m live order ETHUSDT 2500 0.2              # 按数量做空
python -m live order ETHUSDT 2500 --margin 100     # 按保证金做空
python -m live order ETHUSDT 2500 --margin 100 --long --tp 20 --sl 10  # 做多
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

### 状态查看

```bash
python -m live status                 # 资金 & 持仓概览
python -m live trades                 # 历史成交
python -m live signals                # 信号历史
```

### Telegram 通知测试

```bash
python -m live test-notify            # 发送测试消息
python -m live test-notify 自定义消息   # 发送自定义消息
```

---

## 配置参数

核心配置在 `live/live_config.py`，运行时可通过 Web Dashboard Settings 页面在线修改。

### 资金与杠杆

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `leverage` | 3 | 杠杆倍数 |
| `max_positions` | 6 | 最大同时持仓数 |
| `max_entries_per_day` | 2 | 每日最大开仓次数 |
| `live_fixed_margin_usdt` | 5 | 固定保证金模式单笔保证金 (USDT) |
| `daily_loss_limit_usdt` | 50 | 每日亏损限额 (USDT，0=不限) |
| `margin_mode` | `"fixed"` | `"fixed"` 固定金额 / `"percent"` 余额百分比 |
| `margin_pct` | 2.0 | 百分比模式下，使用可用余额的百分比 |

### 策略参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `stop_loss_pct` | 18% | 止损百分比 |
| `strong_tp_pct` | 33% | 强势币止盈 |
| `medium_tp_pct` | 21% | 中等币止盈 |
| `weak_tp_pct` | 10% | 弱势币止盈 |
| `max_hold_hours` | 72 | 最大持仓时间（小时） |
| `surge_threshold` | 10.0 | 信号触发倍数 (sell_vol / avg) |

### 持仓可配置

以下参数可在运行时通过 Web Dashboard 或 API 修改，自动持久化到 `data/config.json`：

`leverage` · `max_positions` · `max_entries_per_day` · `live_fixed_margin_usdt` · `daily_loss_limit_usdt` · `margin_mode` · `margin_pct`

---

## 环境变量

```bash
# .env
BINANCE_API_KEY=your_api_key           # Binance API Key (必需)
BINANCE_API_SECRET=your_api_secret     # Binance API Secret (必需)

TELEGRAM_BOT_TOKEN=123456:ABC-DEF...   # Telegram Bot Token (可选)
TELEGRAM_CHAT_ID=your_chat_id          # Telegram Chat ID (可选)

TRADING_PASSWORD=your_password         # Web Dashboard 手动下单密码 (可选)
```

---

## 部署指南

### 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env

# 启动交易
python -m live run
```

### 云服务器部署

#### 1. 环境准备

```bash
# 上传代码
scp -r duo-live user@server:/opt/duo-live

# Python 虚拟环境
cd /opt/duo-live
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 前端构建
cd web
npm install
npm run build
# standalone 模式需拷贝静态资源
cp -r .next/static .next/standalone/.next/static
[ -d public ] && cp -r public .next/standalone/public
cd ..

# 配置环境变量
cp .env.example .env
# 编辑 .env
```

#### 2. PM2 进程管理

```bash
# 安装 PM2
npm install -g pm2

# 启动所有服务
pm2 start ecosystem.config.js

# 常用命令
pm2 status                    # 查看状态
pm2 logs                      # 查看日志
pm2 logs duo-live-backend     # 只看后端
pm2 restart all               # 重启
pm2 stop all                  # 停止

# 持久化（开机自启）
pm2 save
pm2 startup
```

#### 3. 一键更新部署

```bash
./deploy.sh
# 自动执行: git pull → pip install → npm build → 拷贝 static → pm2 restart
```

#### 4. 防火墙 / 安全组

| 端口 | 用途 |
|------|------|
| `8899` | 后端 FastAPI API |
| `3000` | 前端 Next.js Dashboard |

> 建议使用 Nginx 反向代理统一到 80/443 端口。

---

## 开发与测试

```bash
# 运行测试
python -m pytest tests/ -v

# 测试覆盖
# - test_dynamic_tp.py: 策略评估 (8 tests) + legacy fallback (1) + 辅助函数 (3)
# - test_ws_handler.py: WebSocket 事件处理 (8 tests)
```

### 项目结构约定

- **基础设施**（`trader.py`, `live_position_monitor.py`）：不包含策略逻辑，只负责编排和执行
- **策略决策**（`strategy.py`）：所有交易决策集中在此，通过 `Strategy` 接口注入
- **风控过滤**（`risk_filters.py`）：由策略调用，独立于基础设施
- **数据访问**（`binance_client.py`）：统一的 API 层，所有模块共享

---

## 依赖

### 运行时

| 包 | 用途 |
|----|------|
| `httpx` | 异步 HTTP 客户端（Binance API + Telegram） |
| `pydantic` | API 响应模型验证 |
| `rich` | 控制台格式化输出 |
| `python-dotenv` | 环境变量加载 |
| `websockets` | Binance WebSocket 实时流 |
| `fastapi` | Dashboard REST API |
| `uvicorn` | ASGI 服务器 |

### 前端

| 包 | 用途 |
|----|------|
| `next` | React SSR 框架 |
| `lucide-react` | 图标库 |

### 开发

| 包 | 用途 |
|----|------|
| `pytest` | 测试框架 |
| `pytest-asyncio` | 异步测试支持 |
