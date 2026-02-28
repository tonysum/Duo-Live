# 重复挂单问题修复

## 问题描述

网络错误可能导致重复挂单：
- API 请求实际成功但返回超时
- 程序认为失败并重试
- 导致同一持仓有多个止盈/止损单

## 修复方案

### 1. **挂单前检查（幂等性保护）** ✅

在 `_re_place_single_order` 方法中添加了检查：

```python
# 挂单前先查询是否已存在
algo_orders = await self.client.get_open_algo_orders(pos.symbol)
existing_orders = [o for o in algo_orders if o.order_type == target_type]

if existing_orders:
    # 订单已存在，更新追踪ID
    pos.tp_algo_id = existing_orders[0].algo_id
    
    # 如果有多个，取消多余的
    if len(existing_orders) > 1:
        for extra in existing_orders[1:]:
            await self.client.cancel_algo_order(symbol, algo_id=extra.algo_id)
    
    return  # 不创建新订单
```

### 2. **定期清理重复订单** ✅

在 `_cancel_orphan_orders` 方法中添加了重复订单检测：

```python
# 检测每个持仓的止盈/止损单数量
tp_orders = [o for o in orders if o.order_type == "TAKE_PROFIT_MARKET"]
sl_orders = [o for o in orders if o.order_type == "STOP_MARKET"]

# 如果有多个，保留第一个，取消其余
if len(tp_orders) > 1:
    for extra in tp_orders[1:]:
        await self.client.cancel_algo_order(symbol, algo_id=extra.algo_id)
```

## 自动修复机制

系统会在以下时机自动检测和清理重复订单：

### 1. **挂单时检查**
- 每次尝试创建止盈/止损单前
- 先查询是否已存在相同类型的订单
- 如果存在，更新追踪ID而不是创建新订单
- 如果有多个，自动取消多余的

### 2. **定期清理**
- 每个监控周期（60秒）
- 检查所有持仓的挂单
- 识别重复的止盈/止损单
- 保留第一个，取消其余

### 3. **日志记录**
```
❌ 检测到重复的止盈单: BTCUSDT (共2个) — 保留第一个，取消其余
🗑️ 已取消重复的止盈单: BTCUSDT (algoId=12345, 触发价=95000.0)
🧹 重复挂单清除完成: 共删除 1 单
```

## 手动清理重复订单

如果需要立即清理所有重复订单：

### 方法1：重启系统（推荐）
```bash
pm2 restart duo-live-backend
```
系统启动后会在第一个监控周期自动清理。

### 方法2：使用 Telegram Bot
```bash
# 查看当前挂单
/orders BTCUSDT

# 手动取消多余的止盈单
/cancel_tp BTCUSDT
```

### 方法3：币安网页
1. 登录币安合约账户
2. 进入"当前委托"
3. 找到重复的止盈/止损单
4. 手动取消多余的订单

## 预防措施

### 1. **网络优化**
已应用的网络优化可以减少超时错误：
- 重试次数：5次
- 重试间隔：指数退避（2,4,8,16,32秒）
- 请求超时：60秒

### 2. **幂等性保护**
所有挂单操作都会先检查是否已存在，避免重复创建。

### 3. **定期清理**
系统每60秒自动检查并清理重复订单。

## 监控和验证

### 查看重复订单检测日志
```bash
# 查看重复订单检测
grep "检测到重复" logs/duo-live.log

# 查看清理记录
grep "已取消重复" logs/duo-live.log

# 查看清理统计
grep "重复挂单清除完成" logs/duo-live.log
```

### 验证订单正常
```bash
# 通过 Telegram
/orders BTCUSDT

# 或查看 Web 界面
http://your-server:3000/positions
```

应该看到每个持仓只有1个止盈单和1个止损单。

## 故障排查

### 问题：仍然出现重复订单

**可能原因**：
1. 系统未重启，旧代码仍在运行
2. 有其他程序在创建订单
3. 手动在币安网页创建了订单

**解决方法**：
```bash
# 1. 重启系统
pm2 restart duo-live-backend

# 2. 查看日志确认清理功能正常
tail -f logs/duo-live.log | grep "重复"

# 3. 停止其他可能创建订单的程序
pm2 list  # 查看所有运行的程序
```

### 问题：清理后又出现重复订单

**可能原因**：
网络仍然不稳定，导致新的重复订单。

**解决方法**：
1. 检查网络质量
   ```bash
   ping -c 100 fapi.binance.com
   ```

2. 考虑更换网络或服务器
   - 推荐：香港、新加坡、日本节点
   - 避免使用免费VPN

3. 进一步增加重试间隔
   ```python
   # live/binance_client.py
   _MAX_RETRIES = 7
   _RETRY_BACKOFF = (3, 6, 12, 24, 48, 96, 192)
   ```

## 技术细节

### 幂等性保护原理

**问题场景**：
```
1. 程序发送创建订单请求
2. 币安收到请求并创建订单
3. 网络超时，程序未收到响应
4. 程序认为失败，重试
5. 创建了第二个订单（重复）
```

**修复方案**：
```
1. 程序发送创建订单请求
2. 网络超时，程序未收到响应
3. 程序先查询是否已存在订单
4. 发现订单已存在，更新追踪ID
5. 不创建新订单（避免重复）
```

### 清理策略

**保留规则**：
- 保留第一个订单（最早创建的）
- 取消其余订单

**原因**：
- 第一个订单通常是正确的
- 避免频繁修改订单
- 减少API请求

## 相关代码

- 幂等性检查：`live/live_position_monitor.py` 第 985-1020 行
- 重复订单清理：`live/live_position_monitor.py` 第 345-390 行
- 网络优化配置：`live/binance_client.py` 第 45-47 行

## 通知示例

当检测到并清理重复订单时，会收到 Telegram 通知：

```
⚠️ 检测到重复挂单
  已自动清除 2 个重复订单
```

## 升级说明

如果你的系统是旧版本：

1. 拉取最新代码
   ```bash
   git pull origin main
   ```

2. 重启系统
   ```bash
   pm2 restart duo-live-backend
   ```

3. 观察日志确认功能正常
   ```bash
   tail -f logs/duo-live.log | grep "重复"
   ```

## 注意事项

1. **自动清理是安全的**：只清理重复订单，不影响正常订单
2. **保留第一个订单**：确保持仓始终有保护
3. **实时通知**：清理时会发送 Telegram 通知
4. **详细日志**：所有操作都有日志记录

---

**重复挂单问题已修复，系统会自动检测和清理重复订单。**
