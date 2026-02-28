# duo-live 改进说明 - 从 AE Server 移植的特性

本文档记录了从 AE Server Script 移植到 duo-live 的4项关键改进。

## 改进概览

1. ✅ 连续暴涨保护逻辑（12小时判断时的特殊处理）
2. ✅ 平仓前的严格检查机制（取消订单、获取实际持仓）
3. ✅ 分批平仓容错机制
4. ✅ 邮件报警系统

---

## 1. 连续暴涨保护逻辑

### 问题背景
在原有的 duo-live 系统中，12小时动态止盈判断时，如果下跌占比 < 60%，会直接将持仓降为弱势币（止盈10%）。但对于连续2小时卖量暴涨的信号，这种处理过于激进，可能导致过早止盈。

### 改进方案
在 `live/strategy.py` 的 `SurgeShortStrategy.evaluate_position()` 方法中，12小时判断时增加连续暴涨检查：

```python
# 下跌占比 < 60%：检查是否为连续暴涨
is_consecutive = await self._check_consecutive_surge(client, pos)

if is_consecutive:
    # 🔥 连续暴涨保护：保持强势或中等币止盈，不降为弱势币
    if pos.strength == "strong":
        new_tp = config.strong_tp_pct  # 保持33%
    else:
        new_tp = config.medium_tp_pct  # 保持21%
else:
    # 非连续暴涨：正常降为弱势币
    new_tp = config.weak_tp_pct  # 降至10%
```

### 连续暴涨判断逻辑
检查信号小时和建仓小时（信号+1小时）是否都有卖量 >= 10倍昨日平均：

1. 获取昨日平均小时卖量
2. 获取信号小时和建仓小时的K线数据
3. 计算每小时的卖量倍数
4. 如果两个小时都 >= 10倍，判定为连续暴涨

### 影响
- 对于连续暴涨信号，即使12小时后下跌不够强势，也能保持较高的止盈目标
- 避免过早止盈，提高盈利潜力

---

## 2. 平仓前的严格检查机制

### 问题背景
原有的 `_force_close()` 方法直接使用程序记录的持仓数量和方向进行平仓，可能存在以下问题：
- 程序记录与交易所实际持仓不一致
- 未成交的止盈止损订单可能干扰平仓
- 数量精度问题导致平仓失败

### 改进方案
在 `live/live_position_monitor.py` 的 `_force_close()` 方法中增加6个步骤：

#### 步骤1：平仓前取消所有未成交订单
```python
algo_orders = await self.client.get_open_algo_orders(symbol)
for order in algo_orders:
    await self.client.cancel_algo_order(symbol=symbol, algo_id=order.algo_id)
```

#### 步骤2：从交易所获取实际持仓
```python
positions_info = await self.client.get_position_risk(symbol)
actual_amt = float(actual_position.position_amt)
quantity = abs(actual_amt)
is_long_position = actual_amt > 0
```

#### 步骤3：动态获取数量精度并调整
```python
# 根据 LOT_SIZE 的 stepSize 进行精度调整
quantity_adjusted = round(quantity / step_size) * step_size
```

#### 步骤4：根据实际仓位方向决定平仓方向
```python
if is_long_position:
    close_side = 'SELL'  # 做多平仓 = 卖出
else:
    close_side = 'BUY'   # 做空平仓 = 买入
```

#### 步骤5：先尝试 reduceOnly，失败则重试普通市价单
```python
try:
    await self.client.place_market_close(..., reduceOnly=True)
except BinanceAPIError as e:
    if 'ReduceOnly Order is rejected' in str(e):
        await self.client.place_order(..., type="MARKET")
```

#### 步骤6：支持分批平仓（见下一节）

### 影响
- 大幅提高平仓成功率
- 避免因程序记录不准确导致的平仓失败
- 减少因精度问题导致的 API 错误

---

## 3. 分批平仓容错机制

### 问题背景
当保证金不足时，一次性平仓可能失败，导致持仓无法关闭。

### 改进方案
在 `_force_close()` 方法中，捕获 "Margin is insufficient" 错误，自动分批平仓：

```python
except BinanceAPIError as e:
    if 'Margin is insufficient' in str(e):
        # 先平仓一半
        half_quantity = quantity / 2
        await self.client.place_order(..., quantity=str(half_quantity))
        
        # 等待执行
        await asyncio.sleep(0.5)
        
        # 重新获取剩余持仓并平仓
        remaining_quantity = ...  # 从交易所获取
        await self.client.place_order(..., quantity=str(remaining_quantity))
```

### 容错处理
- 如果分批平仓仍然失败，发送紧急通知（Telegram + 邮件）
- 记录详细的错误信息，便于人工干预

### 影响
- 提高极端情况下的平仓成功率
- 避免因保证金不足导致的持仓卡死

---

## 4. 邮件报警系统

### 问题背景
原有系统仅支持 Telegram 通知，对于紧急情况（如平仓失败），可能无法及时触达用户。

### 改进方案
在 `live/notifier.py` 中增加邮件报警功能：

#### 配置环境变量
```bash
export SMTP_EMAIL="your_email@163.com"
export SMTP_PASSWORD="your_authorization_code"
export ALERT_EMAIL="alert_receiver@example.com"
```

#### 新增方法

##### 1. `send_email_alert(subject, message)`
发送普通邮件报警：
```python
await notifier.send_email_alert(
    "每日交易报告",
    "今日盈亏: +50 USDT\n持仓数: 3"
)
```

##### 2. `send_critical_alert(subject, message)`
发送紧急报警（同时 Telegram + 邮件）：
```python
await notifier.send_critical_alert(
    "平仓失败 - 需要人工干预",
    f"{symbol} 所有平仓尝试都失败，请立即检查账户"
)
```

### 使用场景
- ✅ 平仓失败（完全失败或分批失败）
- ✅ 保证金不足
- ✅ 系统异常
- ✅ 每日交易报告（可选）

### 邮件服务器配置
默认使用163邮箱SMTP服务：
- 服务器: smtp.163.com
- 端口: 465 (SSL)
- 需要开启邮箱的"授权码"功能

### 影响
- 提高紧急情况的通知可靠性
- 支持多渠道报警，降低漏报风险
- 便于事后审计和问题排查

---

## 配置说明

### 环境变量
在 `.env` 文件中添加以下配置：

```bash
# Telegram 通知（原有）
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 邮件报警（新增）
SMTP_EMAIL=your_email@163.com
SMTP_PASSWORD=your_authorization_code
ALERT_EMAIL=alert_receiver@example.com
```

### 163邮箱授权码获取步骤
1. 登录163邮箱
2. 设置 → POP3/SMTP/IMAP
3. 开启"POP3/SMTP服务"
4. 获取"授权码"（不是邮箱密码）
5. 将授权码填入 `SMTP_PASSWORD`

---

## 测试建议

### 1. 连续暴涨保护测试
- 创建一个连续2小时卖量暴涨的模拟信号
- 等待12小时后观察止盈是否保持在强势/中等币水平

### 2. 平仓机制测试
- 手动取消止盈止损订单，观察平仓前是否正确取消
- 修改程序记录的持仓数量，观察是否使用交易所实际数量
- 模拟保证金不足场景，观察是否触发分批平仓

### 3. 邮件报警测试
```python
# 测试普通邮件
await notifier.send_email_alert("测试邮件", "这是一封测试邮件")

# 测试紧急报警
await notifier.send_critical_alert("测试紧急报警", "这是一条紧急报警")
```

---

## 注意事项

1. **邮件报警频率**：避免频繁发送邮件，可能被邮件服务器限流
2. **连续暴涨判断**：需要持仓记录中包含信号时间，如果缺失会使用建仓时间往前推1小时估算
3. **分批平仓**：仅在保证金不足时触发，正常情况下仍使用一次性平仓
4. **邮件服务器**：默认使用163邮箱，如需使用其他邮箱，需修改 `notifier.py` 中的SMTP配置

---

## 版本历史

- **v1.0** (2024-02-28): 初始版本，移植4项改进
  - 连续暴涨保护逻辑
  - 平仓前严格检查机制
  - 分批平仓容错机制
  - 邮件报警系统

---

## 相关文件

- `live/strategy.py` - 连续暴涨保护逻辑
- `live/live_position_monitor.py` - 平仓机制改进
- `live/notifier.py` - 邮件报警系统
- `live/live_config.py` - 配置参数

---

## 反馈与改进

如有问题或建议，请提交 Issue 或 Pull Request。
