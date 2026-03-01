# 设计文档 - AE Server 迁移与网络优化

## 简介

本文档描述了从 AE Server Script 迁移到 duo-live 系统的核心交易逻辑改进和网络稳定性优化的详细设计。该功能包含两大类改进：

1. **四项核心交易逻辑改进**：连续暴涨保护、平仓检查机制、分批平仓容错、邮件报警
2. **三项网络稳定性优化**：重试机制、超时配置、监控频率

这些改进旨在提高交易系统的盈利能力、平仓成功率、系统稳定性和报警可靠性。

---

## 概览

### 系统架构

duo-live 是一个基于 Python 的加密货币期货自动交易系统，采用模块化架构：

```
┌─────────────────────────────────────────────────────────────┐
│                      Live Trading System                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐      ┌──────────────┐                     │
│  │   Scanner    │─────▶│   Strategy   │                     │
│  │ (信号扫描)    │      │  (策略引擎)   │                      │
│  └──────────────┘      └──────┬───────┘                     │
│                               │                             │
│                               ▼                             │
│  ┌──────────────┐      ┌──────────────┐                     │
│  │   Executor   │◀─────│   Monitor    │                     │
│  │ (订单执行)    │      │ (持仓监控)    │                      │
│  └──────┬───────┘      └──────┬───────┘                     │
│         │                     │                             │
│         ▼                     ▼                             │
│  ┌──────────────────────────────────┐                       │
│  │      Binance Client              │                       │
│  │  (API客户端 + 网络优化)            │                       │
│  └──────────────┬───────────────────┘                       │
│                 │                                           │
│                 ▼                                           │
│  ┌──────────────────────────────────┐                       │
│  │         Notifier                 │                       │
│  │  (Telegram + Email 报警)          │                       │
│  └──────────────────────────────────┘                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 核心组件

1. **Strategy Engine (策略引擎)**: 负责信号评估和动态止盈调整，包含连续暴涨保护逻辑
2. **Position Monitor (持仓监控)**: 负责持仓监控和平仓执行，包含增强的平仓机制
3. **Binance Client (API客户端)**: 负责与币安交易所通信，包含网络优化
4. **Notifier (通知系统)**: 负责发送报警通知，包含邮件报警功能
5. **Config (配置管理)**: 负责系统配置管理，包含监控频率等参数

---

## 架构设计

### 1. 连续暴涨保护逻辑

#### 设计目标
在12小时动态止盈评估时，识别连续暴涨信号并保持较高的止盈目标，避免过早止盈。

#### 架构组件
- **位置**: `live/strategy.py` - `SurgeShortStrategy` 类
- **触发时机**: 持仓达到12小时且下跌占比 < 60%
- **依赖**: Binance Client (获取历史K线数据)


#### 工作流程

```
持仓达到12小时
    │
    ▼
计算下跌占比
    │
    ├─ >= 60% ──▶ 判定为强势币 (TP 33%)
    │
    └─ < 60% ───▶ 检查连续暴涨
                    │
                    ├─ 是连续暴涨 ──▶ 保持当前止盈
                    │                 (强势币33% / 中等币21%)
                    │
                    └─ 非连续暴涨 ──▶ 降为弱势币 (TP 10%)
```

#### 连续暴涨判断算法

```python
def _check_consecutive_surge(pos):
    # 1. 估算信号时间 = 建仓时间 - 1小时
    signal_time = entry_time - 1 hour
    
    # 2. 获取昨日平均小时卖量
    yesterday_kline = get_klines(symbol, '1d', yesterday)
    avg_hour_sell = yesterday_kline.sell_volume / 24
    
    # 3. 获取信号小时和建仓小时的K线
    klines = get_klines(symbol, '1h', signal_time, entry_time)
    
    # 4. 计算每小时的卖量倍数
    for kline in klines:
        hour_sell = kline.volume - kline.taker_buy_volume
        ratio = hour_sell / avg_hour_sell
    
    # 5. 判断两个小时都 >= 10倍
    return all(ratio >= 10.0 for ratio in ratios)
```

#### 数据流

```
Binance API
    │
    ├─ 获取昨日日K线 ──▶ 计算平均小时卖量
    │
    └─ 获取信号+建仓小时K线 ──▶ 计算卖量倍数 ──▶ 判断连续暴涨
                                                    │
                                                    ▼
                                            更新止盈目标
```

---

### 2. 平仓前严格检查机制

#### 设计目标
在执行强制平仓前进行严格检查，确保使用准确的持仓信息，避免因未成交订单或精度问题导致的失败。

#### 架构组件
- **位置**: `live/live_position_monitor.py` - `_force_close()` 方法
- **触发时机**: 策略决定强制平仓时（超时、止损等）
- **依赖**: Binance Client (查询持仓、取消订单、下单)

#### 工作流程

```
触发强制平仓
    │
    ▼
步骤1: 取消所有未成交订单
    │
    ▼
步骤2: 从交易所获取实际持仓
    │
    ▼
步骤3: 动态获取数量精度 (LOT_SIZE)
    │
    ▼
步骤4: 调整持仓数量到符合精度
    │
    ▼
步骤5: 根据实际方向决定平仓方向
    │   (正数=做多=SELL, 负数=做空=BUY)
    │
    ▼
步骤6: 尝试 reduceOnly 市价单
    │
    ├─ 成功 ──▶ 平仓完成
    │
    └─ 失败 ──▶ 检查错误类型
                │
                ├─ ReduceOnly被拒绝 ──▶ 重试普通市价单
                │
                └─ 保证金不足 ──▶ 触发分批平仓
```


#### 精度调整算法

```python
def adjust_quantity_precision(quantity, step_size):
    # 根据 LOT_SIZE 的 stepSize 调整数量
    if step_size >= 1:
        # 整数精度
        adjusted = round(quantity / step_size) * step_size
        return int(adjusted)
    else:
        # 小数精度
        precision = abs(int(log10(step_size)))
        adjusted = round(quantity / step_size) * step_size
        return round(adjusted, precision)
```

#### 数据流

```
强制平仓请求
    │
    ▼
Binance API: get_open_algo_orders()
    │ (获取未成交订单)
    ▼
Binance API: cancel_algo_order() × N
    │ (取消所有订单)
    ▼
Binance API: get_position_risk()
    │ (获取实际持仓)
    ▼
Binance API: get_exchange_info()
    │ (获取LOT_SIZE规则)
    ▼
本地计算: 精度调整
    │
    ▼
Binance API: place_market_close()
    │ (执行平仓)
    ▼
平仓完成 / 触发分批平仓
```

---

### 3. 分批平仓容错机制

#### 设计目标
当遇到保证金不足错误时，自动分批平仓，提高极端情况下的平仓成功率。

#### 架构组件
- **位置**: `live/live_position_monitor.py` - `_force_close()` 方法（错误处理分支）
- **触发时机**: 平仓时收到 "Margin is insufficient" 错误
- **依赖**: Binance Client, Notifier (紧急报警)

#### 工作流程

```
平仓失败: 保证金不足
    │
    ▼
第一批: 平仓50%持仓
    │
    ▼
等待 500ms
    │
    ▼
重新查询剩余持仓
    │
    ▼
第二批: 平仓所有剩余持仓
    │
    ├─ 成功 ──▶ 平仓完成
    │
    └─ 失败 ──▶ 发送紧急报警
                (Telegram + Email)
```

#### 分批计算

```python
def split_close(total_quantity, step_size):
    # 第一批: 50%
    first_batch = total_quantity * 0.5
    first_batch = adjust_precision(first_batch, step_size)
    
    # 等待第一批执行
    await sleep(0.5)
    
    # 第二批: 从交易所重新获取剩余数量
    remaining = get_actual_position()
    second_batch = adjust_precision(remaining, step_size)
    
    return first_batch, second_batch
```

---

### 4. 邮件报警系统

#### 设计目标
提供邮件报警功能作为 Telegram 的补充通道，提高紧急情况的通知可靠性。

#### 架构组件
- **位置**: `live/notifier.py` - `TelegramNotifier` 类
- **协议**: SMTP over SSL (端口 465)
- **推荐服务**: 163邮箱

#### 类设计

```python
class TelegramNotifier:
    # 原有功能
    async def send(message: str) -> bool
    
    # 新增功能
    async def send_email_alert(subject: str, message: str) -> bool
    async def send_critical_alert(subject: str, message: str)
```


#### 邮件发送流程

```
触发报警
    │
    ├─ 普通报警 ──▶ send_email_alert()
    │                   │
    │                   └─▶ SMTP发送邮件
    │
    └─ 紧急报警 ──▶ send_critical_alert()
                        │
                        ├─▶ Telegram: send()
                        │
                        └─▶ Email: send_email_alert()
```

#### 配置管理

```python
# 环境变量
SMTP_EMAIL = "your_email@163.com"
SMTP_PASSWORD = "authorization_code"  # 授权码，非密码
ALERT_EMAIL = "receiver@example.com"

# 初始化
notifier = TelegramNotifier(
    smtp_email=SMTP_EMAIL,
    smtp_password=SMTP_PASSWORD,
    alert_email=ALERT_EMAIL
)

# 容错设计
if not email_enabled:
    logger.info("邮件报警未配置，跳过邮件")
    # 系统继续运行，不影响核心功能
```

#### 邮件内容格式

```
主题: [duo-live 交易系统] {报警类型}

正文:
duo-live 自动交易系统报警

时间: {timestamp}

{详细信息}

---
此邮件由 duo-live 交易系统自动发送
服务器: {hostname}
```

---

### 5. 网络重试机制优化

#### 设计目标
通过增加重试次数和等待时间，提高网络波动环境下的API请求成功率。

#### 架构组件
- **位置**: `live/binance_client.py` - `_request()` 方法
- **策略**: 指数退避 (Exponential Backoff)
- **适用范围**: 所有 Binance API 请求

#### 重试配置

```python
# 优化前
MAX_RETRIES = 3
RETRY_BACKOFF = (1, 2, 4)  # 总等待: 7秒

# 优化后
MAX_RETRIES = 5
RETRY_BACKOFF = (2, 4, 8, 16, 32)  # 总等待: 62秒
```

#### 重试流程

```
API请求
    │
    ├─ 成功 ──▶ 返回结果
    │
    └─ 失败 ──▶ 检查重试次数
                │
                ├─ < MAX_RETRIES ──▶ 等待 backoff[attempt]
                │                    │
                │                    └─▶ 重新请求
                │
                └─ >= MAX_RETRIES ──▶ 抛出异常
```

#### 错误处理

```python
async def _request(method, endpoint, params):
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.request(...)
            return response
        except NetworkError as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                logger.warning(
                    f"网络错误 {endpoint} "
                    f"(attempt {attempt+1}/{MAX_RETRIES}), "
                    f"{wait}s 后重试: {e}"
                )
                await sleep(wait)
                continue
            raise BinanceConnectionError(str(e))
```

#### IP封禁熔断器

```python
# 全局封禁状态（类级别变量）
_ban_until: float = 0.0  # Unix timestamp

async def _request(...):
    # 检查封禁状态
    if time.time() < _ban_until:
        raise BinanceAPIError(-1003, "IP封禁中")
    
    # 执行请求
    try:
        response = await ...
    except BinanceAPIError as e:
        if e.code == -1003:
            # 解析封禁时间
            _ban_until = parse_ban_time(e.msg)
            logger.error(f"IP封禁至 {_ban_until}")
        raise
```

---

### 6. 超时配置优化

#### 设计目标
增加HTTP请求超时时间，适应网络延迟较高的环境。

#### 架构组件
- **位置**: `live/binance_client.py` - `__init__()` 方法
- **适用范围**: 所有 HTTP 请求


#### 超时配置

```python
class BinanceFuturesClient:
    def __init__(
        self,
        api_key: str = None,
        api_secret: str = None,
        timeout: float = 60.0,  # 优化: 30s → 60s
    ):
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=timeout,
            headers=headers,
        )
```

#### 超时处理

```
HTTP请求
    │
    ├─ 在超时时间内完成 ──▶ 返回结果
    │
    └─ 超过超时时间 ──▶ TimeoutException
                        │
                        └─▶ 触发重试机制
```

---

### 7. 监控频率优化

#### 设计目标
降低持仓监控频率，减少API请求量，降低触发限流的风险。

#### 架构组件
- **位置**: `live/live_config.py` - `LiveTradingConfig` 类
- **影响范围**: Position Monitor 的轮询间隔

#### 配置变更

```python
@dataclass
class LiveTradingConfig:
    # 优化前
    monitor_interval_seconds: int = 30
    
    # 优化后
    monitor_interval_seconds: int = 60
```

#### 监控循环

```python
async def run_forever():
    while running:
        try:
            await check_all_positions()
        except Exception as e:
            logger.error(f"Monitor error: {e}")
        
        # 等待下一个周期
        await asyncio.sleep(monitor_interval_seconds)
```

#### API请求量对比

```
优化前 (30秒间隔):
- 每小时检查次数: 120次
- 每次检查API调用: ~5个
- 每小时总调用: ~600次

优化后 (60秒间隔):
- 每小时检查次数: 60次
- 每次检查API调用: ~5个
- 每小时总调用: ~300次

减少: 50%
```

---

## 组件和接口

### 1. Strategy Engine

#### 接口定义

```python
class Strategy(ABC):
    @abstractmethod
    async def evaluate_position(
        self,
        client: BinanceFuturesClient,
        pos: TrackedPosition,
        config: LiveTradingConfig,
        now: datetime,
    ) -> PositionAction
```

#### SurgeShortStrategy 实现

```python
class SurgeShortStrategy(Strategy):
    async def evaluate_position(...) -> PositionAction:
        # 12小时评估
        if hold_hours >= 12.0:
            pct_drop = await self._calc_5m_drop_ratio(...)
            
            if pct_drop >= 60%:
                # 强势币
                return PositionAction("adjust_tp", new_tp_pct=33)
            else:
                # 检查连续暴涨
                is_consecutive = await self._check_consecutive_surge(...)
                
                if is_consecutive:
                    # 保持当前止盈
                    return PositionAction("hold")
                else:
                    # 降为弱势币
                    return PositionAction("adjust_tp", new_tp_pct=10)
    
    @staticmethod
    async def _check_consecutive_surge(
        client: BinanceFuturesClient,
        pos: TrackedPosition,
    ) -> bool:
        # 连续暴涨判断逻辑
        ...
```

### 2. Position Monitor

#### 接口定义

```python
class LivePositionMonitor:
    async def _force_close(self, pos: TrackedPosition)
    async def _cancel_tp_sl(self, pos: TrackedPosition)
    async def _round_quantity(self, symbol: str, quantity: str) -> str
```

#### 核心方法实现

```python
async def _force_close(self, pos: TrackedPosition):
    # 步骤1: 取消未成交订单
    algo_orders = await self.client.get_open_algo_orders(symbol)
    for order in algo_orders:
        await self.client.cancel_algo_order(symbol, order.algo_id)
    
    # 步骤2: 获取实际持仓
    positions = await self.client.get_position_risk(symbol)
    actual_amt = float(positions[0].position_amt)
    quantity = abs(actual_amt)
    is_long = actual_amt > 0
    
    # 步骤3-4: 精度调整
    quantity = await self._adjust_quantity_precision(symbol, quantity)
    
    # 步骤5: 确定平仓方向
    close_side = 'SELL' if is_long else 'BUY'
    
    # 步骤6: 执行平仓
    try:
        await self.client.place_market_close(
            symbol, close_side, quantity, reduceOnly=True
        )
    except BinanceAPIError as e:
        if 'ReduceOnly Order is rejected' in str(e):
            # 重试普通市价单
            await self.client.place_order(
                symbol, close_side, quantity, type="MARKET"
            )
        elif 'Margin is insufficient' in str(e):
            # 分批平仓
            await self._split_close(symbol, close_side, quantity)
```


### 3. Binance Client

#### 接口定义

```python
class BinanceFuturesClient:
    async def get_klines(
        symbol: str,
        interval: str,
        start_time: int = None,
        end_time: int = None,
        limit: int = None,
    ) -> list[Kline]
    
    async def get_position_risk(symbol: str = None) -> list[PositionRisk]
    
    async def get_open_algo_orders(symbol: str = None) -> list[AlgoOrderResponse]
    
    async def cancel_algo_order(symbol: str, algo_id: int) -> dict
    
    async def place_market_close(
        symbol: str,
        side: str,
        quantity: str,
        position_side: str = "BOTH",
    ) -> OrderResponse
    
    async def get_exchange_info() -> ExchangeInfoResponse
```

#### 重试机制实现

```python
async def _request(
    self,
    method: str,
    endpoint: str,
    params: dict = None,
    signed: bool = False,
) -> Any:
    # 熔断器检查
    if time.time() < self._ban_until:
        raise BinanceAPIError(-1003, "IP封禁中")
    
    # 重试循环
    for attempt in range(self._MAX_RETRIES):
        try:
            # 重新签名（每次重试都需要新的timestamp）
            if signed:
                params = self._sign(params)
            
            # 执行请求
            response = await self.client.request(method, endpoint, params)
            return response.json()
            
        except (ConnectError, TimeoutException, ReadError) as e:
            if attempt < self._MAX_RETRIES - 1:
                wait = self._RETRY_BACKOFF[attempt]
                logger.warning(
                    f"网络错误 {endpoint} "
                    f"(attempt {attempt+1}/{self._MAX_RETRIES}), "
                    f"{wait}s 后重试: {e}"
                )
                await asyncio.sleep(wait)
                continue
            raise BinanceConnectionError(str(e))
```

### 4. Notifier

#### 接口定义

```python
class TelegramNotifier:
    # Telegram 通知
    async def send(self, message: str) -> bool
    
    # 邮件报警
    async def send_email_alert(self, subject: str, message: str) -> bool
    
    # 紧急报警（双通道）
    async def send_critical_alert(self, subject: str, message: str)
    
    # 便捷方法
    async def notify_entry_filled(symbol: str, side: str, price: str)
    async def notify_tp_triggered(symbol: str, side: str, price: str, pnl: str)
    async def notify_sl_triggered(symbol: str, side: str, price: str, pnl: str)
```

#### 邮件发送实现

```python
async def send_email_alert(self, subject: str, message: str) -> bool:
    if not self.email_enabled:
        logger.debug("邮件报警未配置，跳过发送")
        return False
    
    try:
        # 创建邮件
        msg = MIMEMultipart()
        msg['From'] = self.smtp_email
        msg['To'] = self.alert_email
        msg['Subject'] = f"[duo-live 交易系统] {subject}"
        
        # 邮件正文
        body = f"""
duo-live 自动交易系统报警

时间: {datetime.now()}

{message}

---
此邮件由 duo-live 交易系统自动发送
服务器: {socket.gethostname()}
"""
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # 发送邮件
        with smtplib.SMTP_SSL('smtp.163.com', 465, timeout=10) as server:
            server.login(self.smtp_email, self.smtp_password)
            server.send_message(msg)
        
        logger.info(f"✅ 邮件报警已发送: {subject}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 发送邮件报警失败: {e}")
        return False

async def send_critical_alert(self, subject: str, message: str):
    # 同时发送 Telegram 和邮件
    telegram_msg = f"🚨 <b>{subject}</b>\n\n{message}"
    await self.send(telegram_msg)
    await self.send_email_alert(subject, message)
```

---

## 数据模型

### TrackedPosition

```python
@dataclass
class TrackedPosition:
    # 基本信息
    symbol: str
    entry_order_id: int
    side: str  # "SHORT" or "LONG"
    quantity: str
    
    # 状态跟踪
    entry_filled: bool = False
    entry_price: Optional[Decimal] = None
    entry_fill_time: Optional[datetime] = None
    tp_sl_placed: bool = False
    tp_algo_id: Optional[int] = None
    sl_algo_id: Optional[int] = None
    closed: bool = False
    
    # 动态止盈
    current_tp_pct: float = 33.0
    evaluated_2h: bool = False
    evaluated_12h: bool = False
    strength: str = "unknown"  # strong / medium / weak
```

### PositionAction

```python
@dataclass
class PositionAction:
    action: str = "hold"  # hold / close / adjust_tp
    reason: str = ""
    new_tp_pct: float = 0
    new_strength: str = ""
```

### Kline

```python
@dataclass
class Kline:
    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: int
    quote_volume: Decimal
    trades: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal
```


---

## 正确性属性

*属性是一个特征或行为，应该在系统的所有有效执行中保持为真——本质上是关于系统应该做什么的形式化陈述。属性作为人类可读规范和机器可验证正确性保证之间的桥梁。*

### 属性 1: 连续暴涨判断的幂等性

*对于任意* 持仓和K线数据，多次执行连续暴涨检查应该返回相同的结果

**验证需求**: 1.7

### 属性 2: 12小时评估的连续暴涨保护

*对于任意* 持仓达到12小时且下跌占比 < 60%，如果检测到连续暴涨，则止盈目标应保持在强势币（33%）或中等币（21%）水平，不应降为弱势币（10%）

**验证需求**: 1.1, 1.3, 1.4, 1.5

### 属性 3: 连续暴涨定义的正确性

*对于任意* K线数据，当且仅当信号小时和建仓小时的卖量都 >= 昨日平均小时卖量的10倍时，应判定为连续暴涨

**验证需求**: 1.2

### 属性 4: 平仓前订单取消的完整性

*对于任意* 强制平仓操作，在执行平仓前应取消该交易对的所有未成交算法订单

**验证需求**: 2.1

### 属性 5: 平仓使用实际持仓数据

*对于任意* 强制平仓操作，应从交易所获取实际持仓数量和方向，而非使用程序记录的数据

**验证需求**: 2.2

### 属性 6: 持仓数量精度调整的正确性

*对于任意* 持仓数量和LOT_SIZE规则，调整后的数量应符合stepSize精度要求，且不大于原始数量

**验证需求**: 2.3, 2.4

### 属性 7: 平仓方向的正确性

*对于任意* 持仓，当实际持仓数量 > 0（做多）时应使用SELL平仓，当实际持仓数量 < 0（做空）时应使用BUY平仓

**验证需求**: 2.5, 2.6

### 属性 8: 分批平仓的触发条件

*对于任意* 平仓操作，当且仅当收到 "Margin is insufficient" 错误时，应触发分批平仓流程

**验证需求**: 3.1

### 属性 9: 分批平仓的数量分配

*对于任意* 分批平仓操作，第一批应平仓总量的50%，第二批应平仓所有剩余持仓

**验证需求**: 3.2, 3.4, 3.5

### 属性 10: 分批平仓的最终状态

*对于任意* 分批平仓操作，无论分几批，最终持仓状态应为零持仓（与一次性平仓等价）

**验证需求**: 3.8

### 属性 11: 邮件配置的容错性

*对于任意* 邮件报警调用，当邮件配置缺失时，应跳过邮件发送并记录警告日志，但不影响系统运行

**验证需求**: 4.8

### 属性 12: 邮件发送失败的容错性

*对于任意* 邮件发送操作，当发送失败时，应记录错误日志但不抛出异常，不中断主流程

**验证需求**: 4.9

### 属性 13: 紧急报警的双通道发送

*对于任意* 紧急报警，应同时调用 Telegram 和邮件两个通知渠道

**验证需求**: 4.4

### 属性 14: 网络重试次数的正确性

*对于任意* API请求失败，应自动重试最多5次

**验证需求**: 5.1

### 属性 15: 重试间隔的指数退避

*对于任意* 重试序列，重试间隔应依次为 2秒、4秒、8秒、16秒、32秒，总等待时间为62秒

**验证需求**: 5.2, 5.6

### 属性 16: 重试日志的完整性

*对于任意* 网络请求失败，应记录警告日志包含端点路径和当前重试次数

**验证需求**: 5.3

### 属性 17: 重试失败后的异常抛出

*对于任意* API请求，当所有重试都失败后，应抛出异常并记录错误日志

**验证需求**: 5.4

### 属性 18: 重试机制的一致性

*对于任意* API端点，应应用统一的重试机制（相同的重试次数和退避策略）

**验证需求**: 5.5

### 属性 19: 超时配置的一致性

*对于任意* API请求，应应用统一的超时配置（默认60秒）

**验证需求**: 6.4

### 属性 20: 监控间隔的正确性

*对于任意* 持仓检查循环，两次检查之间的时间间隔应为60秒

**验证需求**: 7.1, 7.4

### 属性 21: 监控循环的日志记录

*对于任意* 监控周期，在每次循环开始时应记录日志

**验证需求**: 7.3

### 属性 22: 配置缺失的向后兼容性

*对于任意* 新增配置参数（如邮件配置），当配置缺失时，系统应正常运行核心交易功能

**验证需求**: 8.5

### 属性 23: 启动时的配置验证

*对于任意* 系统启动，应验证必需的环境变量（币安API密钥、Telegram配置），并记录可选配置的状态

**验证需求**: 8.6, 8.7

---

## 错误处理

### 1. 网络错误处理

#### 连接错误
```python
try:
    response = await client.request(...)
except (ConnectError, TimeoutException, ReadError) as e:
    # 自动重试（最多5次）
    if attempt < MAX_RETRIES - 1:
        await asyncio.sleep(RETRY_BACKOFF[attempt])
        continue
    # 所有重试失败
    raise BinanceConnectionError(str(e))
```

#### API错误
```python
try:
    response = await client.request(...)
    data = response.json()
    if data.get('code', 0) < 0:
        raise BinanceAPIError(data['code'], data['msg'])
except BinanceAPIError as e:
    if e.code == -1003:  # IP封禁
        # 设置熔断器
        _ban_until = parse_ban_time(e.msg)
        logger.error(f"IP封禁至 {_ban_until}")
    raise
```

### 2. 平仓错误处理

#### ReduceOnly 被拒绝
```python
try:
    await client.place_market_close(..., reduceOnly=True)
except BinanceAPIError as e:
    if 'ReduceOnly Order is rejected' in str(e):
        # 重试普通市价单
        await client.place_order(..., type="MARKET")
```

#### 保证金不足
```python
try:
    await client.place_market_close(...)
except BinanceAPIError as e:
    if 'Margin is insufficient' in str(e):
        # 触发分批平仓
        await split_close(symbol, quantity)
```

#### 分批平仓失败
```python
try:
    await split_close(...)
except Exception as e:
    # 发送紧急报警
    await notifier.send_critical_alert(
        "平仓失败 - 需要人工干预",
        f"{symbol} 分批平仓仍失败: {e}"
    )
```


### 3. 邮件发送错误处理

#### SMTP连接失败
```python
try:
    with smtplib.SMTP_SSL('smtp.163.com', 465, timeout=10) as server:
        server.login(smtp_email, smtp_password)
        server.send_message(msg)
except Exception as e:
    # 记录错误但不中断主流程
    logger.error(f"发送邮件报警失败: {e}")
    return False
```

#### 配置缺失
```python
async def send_email_alert(subject, message):
    if not self.email_enabled:
        logger.debug("邮件报警未配置，跳过发送")
        return False
    # 继续发送...
```

### 4. 数据验证错误

#### 精度调整失败
```python
try:
    quantity = adjust_quantity_precision(quantity, step_size)
except Exception as e:
    logger.warning(f"获取精度失败: {e}，使用默认精度")
    quantity = round(quantity, 3)
```

#### K线数据不足
```python
async def _calc_5m_drop_ratio(...):
    try:
        klines = await client.get_klines(...)
        if not klines or len(klines) < 2:
            return None  # 数据不足，无法计算
        # 继续计算...
    except Exception as e:
        logger.debug(f"5m drop ratio error: {e}")
        return None
```

---

## 测试策略

### 测试方法

本功能采用**双重测试策略**：

1. **单元测试**: 验证具体示例、边缘情况和错误条件
2. **属性测试**: 验证跨所有输入的通用属性

两者互补，共同确保全面覆盖：
- 单元测试捕获具体的错误
- 属性测试验证一般正确性

### 属性测试配置

**测试库**: 根据语言选择
- Python: `hypothesis`
- JavaScript: `fast-check`
- Java: `jqwik`

**配置要求**:
- 每个属性测试最少运行 100 次迭代（由于随机化）
- 每个测试必须引用设计文档中的属性
- 标签格式: `Feature: ae-server-migration-and-network-optimization, Property {number}: {property_text}`

### 单元测试计划

#### 1. 连续暴涨保护逻辑

**测试用例**:
- 测试连续暴涨判断（两小时都>=10倍）
- 测试非连续暴涨（只有一小时>=10倍）
- 测试12小时评估时的止盈调整
- 测试K线数据不足的边缘情况

**Mock依赖**:
- Binance Client (get_klines)

#### 2. 平仓前严格检查机制

**测试用例**:
- 测试平仓前取消所有未成交订单
- 测试从交易所获取实际持仓
- 测试数量精度调整（整数和小数精度）
- 测试平仓方向判断（做多/做空）
- 测试 reduceOnly 被拒绝后的重试

**Mock依赖**:
- Binance Client (get_open_algo_orders, cancel_algo_order, get_position_risk, get_exchange_info, place_market_close)

#### 3. 分批平仓容错机制

**测试用例**:
- 测试保证金不足时触发分批平仓
- 测试第一批平仓50%
- 测试等待500ms
- 测试第二批平仓剩余持仓
- 测试分批平仓失败后的紧急报警

**Mock依赖**:
- Binance Client (place_order, get_position_risk)
- Notifier (send_critical_alert)

#### 4. 邮件报警系统

**测试用例**:
- 测试邮件发送成功
- 测试邮件配置缺失时跳过发送
- 测试邮件发送失败时的容错
- 测试紧急报警同时发送Telegram和邮件
- 测试SMTP连接使用SSL和正确端口

**Mock依赖**:
- smtplib.SMTP_SSL

#### 5. 网络重试机制

**测试用例**:
- 测试重试次数（最多5次）
- 测试重试间隔（2, 4, 8, 16, 32秒）
- 测试重试日志记录
- 测试所有重试失败后抛出异常
- 测试IP封禁熔断器

**Mock依赖**:
- httpx.AsyncClient

#### 6. 超时配置

**测试用例**:
- 测试默认超时为60秒
- 测试自定义超时
- 测试超时后触发重试

**Mock依赖**:
- httpx.AsyncClient

#### 7. 监控频率

**测试用例**:
- 测试监控间隔为60秒
- 测试每次循环记录日志
- 测试等待到下一个周期

**Mock依赖**:
- asyncio.sleep

### 属性测试计划

#### 属性 1-3: 连续暴涨逻辑

```python
@given(
    klines=st.lists(st.builds(Kline, ...)),
    position=st.builds(TrackedPosition, ...)
)
def test_consecutive_surge_idempotent(klines, position):
    """属性1: 连续暴涨判断的幂等性"""
    result1 = check_consecutive_surge(position, klines)
    result2 = check_consecutive_surge(position, klines)
    assert result1 == result2

@given(
    position=st.builds(TrackedPosition, 
        hold_hours=st.floats(min_value=12.0),
        drop_ratio=st.floats(max_value=0.6)
    ),
    is_consecutive=st.booleans()
)
def test_consecutive_surge_protection(position, is_consecutive):
    """属性2: 12小时评估的连续暴涨保护"""
    action = evaluate_position(position, is_consecutive)
    if is_consecutive:
        assert action.new_tp_pct in [33.0, 21.0]  # 强势或中等
    else:
        assert action.new_tp_pct == 10.0  # 弱势
```

#### 属性 4-7: 平仓机制

```python
@given(
    position=st.builds(TrackedPosition, ...),
    open_orders=st.lists(st.builds(AlgoOrder, ...))
)
def test_force_close_cancels_orders(position, open_orders):
    """属性4: 平仓前订单取消的完整性"""
    cancelled = force_close(position, open_orders)
    assert len(cancelled) == len(open_orders)

@given(
    quantity=st.floats(min_value=0.001, max_value=1000),
    step_size=st.floats(min_value=0.001, max_value=1.0)
)
def test_quantity_precision_adjustment(quantity, step_size):
    """属性6: 持仓数量精度调整的正确性"""
    adjusted = adjust_quantity_precision(quantity, step_size)
    # 检查精度符合要求
    assert (adjusted / step_size) % 1 == 0
    # 检查不大于原始数量
    assert adjusted <= quantity
```


#### 属性 8-10: 分批平仓

```python
@given(
    total_quantity=st.floats(min_value=1.0, max_value=1000),
    step_size=st.floats(min_value=0.001, max_value=1.0)
)
def test_split_close_quantity_distribution(total_quantity, step_size):
    """属性9: 分批平仓的数量分配"""
    first_batch, second_batch = split_close(total_quantity, step_size)
    # 第一批约为50%
    assert abs(first_batch - total_quantity * 0.5) < step_size
    # 两批之和等于总量
    assert abs(first_batch + second_batch - total_quantity) < step_size

@given(
    position=st.builds(TrackedPosition, ...),
    split_batches=st.integers(min_value=1, max_value=5)
)
def test_split_close_final_state(position, split_batches):
    """属性10: 分批平仓的最终状态"""
    final_position = execute_split_close(position, split_batches)
    assert final_position.quantity == 0
```

#### 属性 11-13: 邮件报警

```python
@given(
    email_config=st.one_of(st.none(), st.builds(EmailConfig, ...)),
    alert_message=st.text()
)
def test_email_alert_fault_tolerance(email_config, alert_message):
    """属性11: 邮件配置的容错性"""
    notifier = TelegramNotifier(email_config=email_config)
    # 不应抛出异常
    result = notifier.send_email_alert("Test", alert_message)
    if email_config is None:
        assert result == False
    # 系统应继续运行

@given(
    subject=st.text(),
    message=st.text()
)
def test_critical_alert_dual_channel(subject, message):
    """属性13: 紧急报警的双通道发送"""
    notifier = TelegramNotifier()
    with mock.patch.object(notifier, 'send') as mock_telegram, \
         mock.patch.object(notifier, 'send_email_alert') as mock_email:
        notifier.send_critical_alert(subject, message)
        # 验证两个通道都被调用
        assert mock_telegram.called
        assert mock_email.called
```

#### 属性 14-18: 网络重试

```python
@given(
    endpoint=st.text(),
    failure_count=st.integers(min_value=1, max_value=10)
)
def test_network_retry_count(endpoint, failure_count):
    """属性14: 网络重试次数的正确性"""
    attempts = []
    with mock_network_failure(failure_count):
        try:
            client._request("GET", endpoint)
        except:
            pass
    # 最多重试5次
    assert len(attempts) <= 5

@given(
    endpoint=st.text()
)
def test_retry_backoff_intervals(endpoint):
    """属性15: 重试间隔的指数退避"""
    intervals = []
    with mock_network_failure(5):
        try:
            client._request("GET", endpoint)
        except:
            pass
    # 验证间隔
    expected = [2, 4, 8, 16, 32]
    assert intervals == expected
    assert sum(intervals) == 62
```

#### 属性 19-21: 超时和监控

```python
@given(
    endpoints=st.lists(st.text(), min_size=1, max_size=10)
)
def test_timeout_consistency(endpoints):
    """属性19: 超时配置的一致性"""
    client = BinanceFuturesClient(timeout=60.0)
    timeouts = [get_request_timeout(ep) for ep in endpoints]
    # 所有请求使用相同超时
    assert all(t == 60.0 for t in timeouts)

@given(
    check_count=st.integers(min_value=2, max_value=10)
)
def test_monitor_interval_correctness(check_count):
    """属性20: 监控间隔的正确性"""
    intervals = []
    for _ in range(check_count):
        start = time.time()
        await monitor.check_all()
        await asyncio.sleep(monitor.poll_interval)
        intervals.append(time.time() - start)
    # 每次间隔约为60秒
    assert all(abs(i - 60.0) < 1.0 for i in intervals)
```

### 集成测试

#### 端到端测试场景

1. **连续暴涨保护流程**
   - 创建连续暴涨信号
   - 等待12小时评估
   - 验证止盈保持在高水平

2. **平仓完整流程**
   - 创建持仓
   - 触发强制平仓
   - 验证订单取消、持仓查询、精度调整、平仓执行

3. **分批平仓流程**
   - 模拟保证金不足
   - 验证分批平仓执行
   - 验证紧急报警发送

4. **网络重试流程**
   - 模拟网络波动
   - 验证自动重试
   - 验证最终成功或失败

### 测试环境

#### 单元测试环境
- Python 3.8+
- pytest
- hypothesis (属性测试)
- pytest-asyncio (异步测试)
- pytest-mock (Mock)

#### 集成测试环境
- Binance Testnet (测试网)
- 模拟SMTP服务器
- 模拟Telegram Bot

### 测试数据

#### 生成策略

```python
# K线数据生成器
@st.composite
def kline_strategy(draw):
    return Kline(
        open_time=draw(st.integers(min_value=0)),
        open=draw(st.decimals(min_value=0.01, max_value=100000)),
        high=draw(st.decimals(min_value=0.01, max_value=100000)),
        low=draw(st.decimals(min_value=0.01, max_value=100000)),
        close=draw(st.decimals(min_value=0.01, max_value=100000)),
        volume=draw(st.decimals(min_value=0, max_value=1000000)),
        taker_buy_base_volume=draw(st.decimals(min_value=0, max_value=1000000)),
        ...
    )

# 持仓数据生成器
@st.composite
def position_strategy(draw):
    return TrackedPosition(
        symbol=draw(st.text(alphabet=st.characters(whitelist_categories=('Lu',)), min_size=6, max_size=10)),
        entry_order_id=draw(st.integers(min_value=1)),
        side=draw(st.sampled_from(["LONG", "SHORT"])),
        quantity=draw(st.decimals(min_value=0.001, max_value=1000)),
        entry_price=draw(st.decimals(min_value=0.01, max_value=100000)),
        ...
    )
```

---

## 配置管理

### 环境变量

```bash
# 必需配置（系统无法启动）
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 可选配置（邮件报警）
SMTP_EMAIL=your_email@163.com
SMTP_PASSWORD=your_authorization_code
ALERT_EMAIL=receiver@example.com
```

### 配置文件

**路径**: `data/config.json`

```json
{
  "leverage": 3,
  "max_positions": 6,
  "max_entries_per_day": 2,
  "live_fixed_margin_usdt": 5.0,
  "daily_loss_limit_usdt": 50.0,
  "margin_mode": "fixed",
  "margin_pct": 2.0
}
```

### 配置加载

```python
class LiveTradingConfig:
    @classmethod
    def load_from_file(cls, path: Path = CONFIG_PATH):
        config = cls()  # 使用默认值
        if path.exists():
            data = json.loads(path.read_text())
            # 覆盖默认值
            if "leverage" in data:
                config.leverage = int(data["leverage"])
            # ... 其他配置
        return config
```

### 配置验证

```python
def validate_config():
    # 验证必需配置
    required = ["BINANCE_API_KEY", "BINANCE_API_SECRET", 
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    for key in required:
        if not os.getenv(key):
            raise ValueError(f"Missing required config: {key}")
    
    # 记录可选配置状态
    optional = {
        "SMTP_EMAIL": "邮件报警",
        "SMTP_PASSWORD": "邮件报警",
        "ALERT_EMAIL": "邮件报警",
    }
    for key, feature in optional.items():
        if os.getenv(key):
            logger.info(f"✅ {feature} 已启用")
        else:
            logger.info(f"📵 {feature} 未配置")
```

---

## 部署和运维

### 部署步骤

1. **环境准备**
   ```bash
   # 安装依赖
   pip install -r requirements.txt
   
   # 配置环境变量
   cp .env.example .env
   vim .env
   ```

2. **配置验证**
   ```bash
   # 测试邮件功能
   python tests/test_email_alert.py
   
   # 测试Telegram通知
   python -c "from live.notifier import TelegramNotifier; \
              import asyncio; \
              asyncio.run(TelegramNotifier().send('测试消息'))"
   ```

3. **启动服务**
   ```bash
   # 使用 PM2
   pm2 start ecosystem.config.js
   
   # 或直接运行
   python -m live run
   ```

### 监控指标

#### 1. 网络质量指标

```bash
# 网络错误率
grep "网络错误" logs/duo-live.log | wc -l

# 重试成功率
grep "attempt" logs/duo-live.log | \
  awk '{if ($0 ~ /attempt 1/) total++; if ($0 ~ /成功/) success++} \
       END {print success/total*100"%"}'
```

#### 2. 平仓成功率

```bash
# 平仓成功次数
grep "平仓成功" logs/duo-live.log | wc -l

# 平仓失败次数
grep "平仓失败" logs/duo-live.log | wc -l

# 分批平仓次数
grep "分批平仓" logs/duo-live.log | wc -l
```

#### 3. 报警发送统计

```bash
# Telegram 发送次数
grep "Telegram 发送" logs/duo-live.log | wc -l

# 邮件发送次数
grep "邮件报警已发送" logs/duo-live.log | wc -l

# 紧急报警次数
grep "紧急报警" logs/duo-live.log | wc -l
```


### 日志管理

#### 日志级别

```python
# 生产环境
logging.basicConfig(level=logging.INFO)

# 调试环境
logging.basicConfig(level=logging.DEBUG)
```

#### 关键日志

```python
# 连续暴涨判断
logger.info(f"✅ {symbol} 确认为连续2小时卖量暴涨")
logger.debug(f"❌ {symbol} 非连续确认")

# 平仓流程
logger.info(f"🔄 {symbol} 平仓前取消所有未成交订单")
logger.info(f"📊 {symbol} 从交易所获取实际持仓")
logger.info(f"✅ 市价平仓成功: {symbol}")

# 分批平仓
logger.error(f"❌ {symbol} 保证金不足，尝试分批平仓")
logger.info(f"✅ {symbol} 成功平仓一半仓位")

# 邮件报警
logger.info(f"✅ 邮件报警已发送: {subject}")
logger.error(f"❌ 发送邮件报警失败: {e}")

# 网络重试
logger.warning(f"⚡ 网络错误 {endpoint} (attempt {n}/{MAX}), {wait}s 后重试")
logger.error(f"🚫 Binance IP 封禁！解封时间: {time}")
```

### 故障排查

#### 1. 网络错误频繁

**症状**: 日志中大量 "网络错误" 警告

**排查步骤**:
```bash
# 1. 测试到 Binance 的连接
ping fapi.binance.com

# 2. 检查丢包率和延迟
ping -c 100 fapi.binance.com

# 3. 测试 API 响应
curl -I https://fapi.binance.com/fapi/v1/ping

# 4. 检查 VPN/代理状态
```

**解决方案**:
- 更换 VPN 节点（推荐：香港、新加坡、日本）
- 增加重试次数和等待时间
- 考虑迁移到网络质量更好的服务器

#### 2. 平仓失败

**症状**: 日志中出现 "平仓失败" 错误

**排查步骤**:
```bash
# 1. 检查错误类型
grep "平仓失败" logs/duo-live.log | tail -10

# 2. 检查账户状态
# 登录 Binance 查看持仓和保证金

# 3. 检查是否触发分批平仓
grep "分批平仓" logs/duo-live.log
```

**解决方案**:
- 如果是保证金不足：增加账户余额或降低杠杆
- 如果是精度问题：检查 LOT_SIZE 规则
- 如果是 reduceOnly 被拒绝：系统会自动重试普通市价单

#### 3. 邮件发送失败

**症状**: 日志中出现 "发送邮件报警失败"

**排查步骤**:
```bash
# 1. 检查环境变量
echo $SMTP_EMAIL
echo $SMTP_PASSWORD
echo $ALERT_EMAIL

# 2. 测试邮件功能
python tests/test_email_alert.py

# 3. 检查 SMTP 服务器连接
telnet smtp.163.com 465
```

**解决方案**:
- 确认使用授权码而非邮箱密码
- 检查163邮箱是否开启 SMTP 服务
- 检查是否触发邮件服务器限流（163邮箱每天最多50封）

#### 4. IP 封禁

**症状**: 日志中出现 "Binance IP 封禁"

**排查步骤**:
```bash
# 1. 查看封禁时间
grep "IP 封禁" logs/duo-live.log | tail -1

# 2. 检查 API 请求频率
grep "网络错误" logs/duo-live.log | wc -l
```

**解决方案**:
- 等待封禁时间结束（通常2分钟到2小时）
- 降低监控频率（60秒 → 120秒）
- 检查是否有其他程序也在使用同一IP访问 Binance

---

## 性能优化

### 1. API 请求优化

#### 缓存策略

```python
# Exchange Info 缓存（4小时）
_exchange_info_cache = None
_exchange_info_ts = 0.0
_EXCHANGE_INFO_TTL = 4 * 3600

async def get_exchange_info():
    now = time.time()
    if now - _exchange_info_ts < _EXCHANGE_INFO_TTL:
        return _exchange_info_cache
    # 刷新缓存
    _exchange_info_cache = await client.get_exchange_info()
    _exchange_info_ts = now
    return _exchange_info_cache
```

#### 批量请求

```python
# 批量获取持仓（一次请求获取所有持仓）
positions = await client.get_position_risk()  # 不指定 symbol

# 批量取消订单
for order in orders:
    await client.cancel_algo_order(symbol, order.algo_id)
```

### 2. 监控频率优化

```python
# 根据持仓数量动态调整监控频率
if len(positions) == 0:
    interval = 120  # 无持仓时降低频率
elif len(positions) <= 3:
    interval = 60   # 少量持仓
else:
    interval = 45   # 多持仓时提高频率
```

### 3. 并发控制

```python
# 限制并发请求数量
semaphore = asyncio.Semaphore(3)

async def fetch_with_limit(symbol):
    async with semaphore:
        return await client.get_klines(symbol, ...)

# 并发获取多个交易对的数据
tasks = [fetch_with_limit(s) for s in symbols]
results = await asyncio.gather(*tasks)
```

---

## 安全性

### 1. 敏感信息保护

```python
# 不在日志中记录完整的 API 密钥
logger.info(f"API Key: {api_key[:8]}...")

# 不在日志中记录邮箱密码
logger.info(f"SMTP Email: {smtp_email}")
# 不记录 smtp_password
```

### 2. SSL/TLS 加密

```python
# SMTP 使用 SSL 加密
with smtplib.SMTP_SSL('smtp.163.com', 465) as server:
    server.login(smtp_email, smtp_password)
    server.send_message(msg)

# HTTP 请求使用 HTTPS
BASE_URL = "https://fapi.binance.com"
```

### 3. 环境变量管理

```bash
# .env 文件不应提交到版本控制
echo ".env" >> .gitignore

# 使用 .env.example 作为模板
cp .env.example .env
vim .env
```

### 4. 权限控制

```bash
# 限制配置文件权限
chmod 600 .env
chmod 600 data/config.json

# 限制日志文件权限
chmod 640 logs/*.log
```

---

## 版本信息

- **文档版本**: 1.0
- **创建日期**: 2024-02-28
- **功能版本**: duo-live v2.0
- **工作流类型**: requirements-first
- **规范类型**: feature

---

## 参考文档

### 内部文档
- [需求文档](requirements.md) - 功能需求和验收标准
- [改进说明](../../docs/improvements-from-ae-server.md) - 从 AE Server 移植的详细说明
- [网络优化说明](../../docs/NETWORK_OPTIMIZATION_APPLIED.md) - 网络优化的详细配置

### 外部文档
- [Binance Futures API 文档](https://binance-docs.github.io/apidocs/futures/cn/)
- [SMTP 协议规范](https://tools.ietf.org/html/rfc5321)
- [Hypothesis 文档](https://hypothesis.readthedocs.io/) - Python 属性测试库

---

## 附录

### A. 错误码对照表

| 错误码 | 含义 | 处理方式 |
|--------|------|----------|
| -1003 | IP 封禁 | 触发熔断器，等待解封 |
| -2021 | Order would immediately trigger | 调整触发价格 |
| -4164 | ReduceOnly Order is rejected | 重试普通市价单 |
| -4131 | Margin is insufficient | 触发分批平仓 |

### B. 配置参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| monitor_interval_seconds | int | 60 | 监控间隔（秒） |
| MAX_RETRIES | int | 5 | 最大重试次数 |
| RETRY_BACKOFF | tuple | (2,4,8,16,32) | 重试间隔（秒） |
| timeout | float | 60.0 | HTTP 超时（秒） |
| strong_tp_pct | float | 33.0 | 强势币止盈（%） |
| medium_tp_pct | float | 21.0 | 中等币止盈（%） |
| weak_tp_pct | float | 10.0 | 弱势币止盈（%） |

### C. API 权重消耗

| 端点 | 权重 | 频率 |
|------|------|------|
| get_klines | 5 | 按需 |
| get_position_risk | 5 | 每60秒 |
| get_open_algo_orders | 1 | 每60秒 |
| cancel_algo_order | 1 | 按需 |
| place_order | 1 | 按需 |
| get_exchange_info | 40 | 每4小时 |

**总权重估算**（每小时）:
- 监控循环: 60次 × (5+1) = 360
- K线查询: 按需，约 50
- 其他操作: 约 50
- **总计**: ~460 / 小时

**Binance 限制**: 2400 / 分钟 = 144000 / 小时

**使用率**: 460 / 144000 = 0.32%

---

## 变更历史

| 版本 | 日期 | 变更内容 | 作者 |
|------|------|----------|------|
| 1.0 | 2024-02-28 | 初始版本 | - |

