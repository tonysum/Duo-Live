# duo-live 网络问题排查指南

## 问题现象

系统日志中频繁出现网络错误：

```
[WARNING] live.binance_client: ⚡ 网络错误 /fapi/v1/income (attempt 1/3), 1s 后重试
[WARNING] live.binance_client: ⚡ 网络错误 /fapi/v2/balance (attempt 1/3), 1s 后重试
```

## 原因分析

### 1. 网络连接不稳定
- 服务器到 Binance API (fapi.binance.com) 的网络质量差
- 丢包率高或延迟大

### 2. VPN/代理问题
- VPN 连接不稳定
- 代理服务器响应慢

### 3. Binance API 限流
- 请求频率过高触发限流
- IP 被临时限制

### 4. 超时设置不合理
- 默认超时30秒可能不够
- 网络慢时容易超时

---

## 快速诊断

### 1. 测试网络连接

```bash
# 测试到 Binance API 的连接
ping fapi.binance.com

# 测试 HTTPS 连接
curl -I https://fapi.binance.com/fapi/v1/ping

# 测试延迟
time curl -s https://fapi.binance.com/fapi/v1/time
```

**正常输出**:
```bash
# ping 应该有响应（延迟 < 200ms 较好）
64 bytes from fapi.binance.com: icmp_seq=1 ttl=54 time=50.2 ms

# curl 应该返回 200 OK
HTTP/2 200

# time 应该 < 1秒
real    0m0.234s
```

### 2. 检查系统日志

```bash
# 查看最近的网络错误
grep "网络错误" logs/duo-live.log | tail -20

# 统计错误频率
grep "网络错误" logs/duo-live.log | wc -l

# 查看是否有 IP 封禁
grep "IP 封禁" logs/duo-live.log
```

### 3. 检查 Binance API 状态

访问 [Binance API 状态页](https://www.binance.com/en/support/announcement)，确认 API 是否正常。

---

## 解决方案

### 方案1: 增加超时时间和重试次数

修改 `live/binance_client.py`：

```python
class BinanceFuturesClient:
    # 当前配置
    _MAX_RETRIES = 3
    _RETRY_BACKOFF = [1, 2, 4]  # 指数退避
    
    def __init__(self, timeout: float = 30.0):
        # 增加超时时间
        self.timeout = 60.0  # 改为60秒
```

**或者在初始化时传入**:

```python
# live/trader.py
self.client = BinanceFuturesClient(timeout=60.0)
```

### 方案2: 优化重试策略

修改 `live/binance_client.py` 的重试配置：

```python
class BinanceFuturesClient:
    # 增加重试次数和等待时间
    _MAX_RETRIES = 5  # 从3次增加到5次
    _RETRY_BACKOFF = [2, 4, 8, 16, 32]  # 更长的等待时间
```

### 方案3: 使用更稳定的网络

#### 选项A: 使用专业VPN
- 推荐使用稳定的商业VPN（如 ExpressVPN, NordVPN）
- 选择延迟低的节点（香港、新加坡、日本）

#### 选项B: 使用代理
```bash
# 设置 HTTP 代理
export HTTP_PROXY=http://your-proxy:port
export HTTPS_PROXY=http://your-proxy:port

# 重启 duo-live
pm2 restart duo-live-backend
```

#### 选项C: 更换服务器
- 选择网络质量更好的云服务器
- 推荐：AWS 新加坡、阿里云香港、Vultr 日本

### 方案4: 降低请求频率

修改 `live/live_config.py`：

```python
@dataclass
class LiveTradingConfig:
    # 增加监控间隔（减少请求频率）
    monitor_interval_seconds: int = 60  # 从30秒改为60秒
    
    # 增加扫描间隔
    scan_interval_seconds: int = 3600  # 保持1小时
```

### 方案5: 实现请求队列和限流

创建 `live/rate_limiter.py`：

```python
"""请求限流器 - 避免触发 Binance API 限流"""

import asyncio
import time
from collections import deque

class RateLimiter:
    """令牌桶限流器"""
    
    def __init__(self, max_requests: int = 1200, window: int = 60):
        """
        Args:
            max_requests: 时间窗口内最大请求数
            window: 时间窗口（秒）
        """
        self.max_requests = max_requests
        self.window = window
        self.requests = deque()
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """获取请求许可"""
        async with self._lock:
            now = time.time()
            
            # 清理过期请求
            while self.requests and self.requests[0] < now - self.window:
                self.requests.popleft()
            
            # 如果达到限制，等待
            if len(self.requests) >= self.max_requests:
                wait_time = self.requests[0] + self.window - now
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    return await self.acquire()
            
            # 记录请求
            self.requests.append(now)
```

然后在 `BinanceFuturesClient` 中使用：

```python
class BinanceFuturesClient:
    _rate_limiter = RateLimiter(max_requests=1200, window=60)
    
    async def _request(self, method: str, endpoint: str, ...):
        # 在发送请求前获取许可
        await self._rate_limiter.acquire()
        
        # 原有的请求逻辑
        ...
```

---

## 推荐配置（生产环境）

### 1. 增加超时和重试

```python
# live/binance_client.py
class BinanceFuturesClient:
    _MAX_RETRIES = 5
    _RETRY_BACKOFF = [2, 4, 8, 16, 32]
    
    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout
```

### 2. 降低监控频率

```python
# live/live_config.py
@dataclass
class LiveTradingConfig:
    monitor_interval_seconds: int = 60  # 从30秒改为60秒
```

### 3. 使用稳定网络

- 选择网络质量好的服务器
- 使用稳定的VPN（如果需要）
- 避免使用免费代理

### 4. 监控网络质量

创建监控脚本 `scripts/monitor_network.sh`：

```bash
#!/bin/bash

while true; do
    echo "=== $(date) ==="
    
    # 测试延迟
    ping -c 3 fapi.binance.com | grep "avg"
    
    # 测试 API 响应
    time curl -s https://fapi.binance.com/fapi/v1/time > /dev/null
    
    echo ""
    sleep 60
done
```

运行监控：
```bash
chmod +x scripts/monitor_network.sh
./scripts/monitor_network.sh > logs/network_monitor.log 2>&1 &
```

---

## 临时应急方案

如果网络问题严重，可以临时采取以下措施：

### 1. 停止自动交易

```bash
# 通过 Telegram Bot
/stop

# 或通过 Web Dashboard
# 访问 Settings 页面，关闭自动交易
```

### 2. 手动管理持仓

```bash
# 查看持仓
python -m live positions

# 手动平仓
python -m live close SYMBOL
```

### 3. 降级运行

修改 `live/trader.py`，临时禁用一些非关键功能：

```python
# 注释掉每日报告（减少请求）
# tasks.append(self._daily_pnl_report())

# 增加监控间隔
self.config.monitor_interval_seconds = 120  # 改为2分钟
```

---

## 长期优化建议

### 1. 实现本地缓存

对于不常变化的数据（如交易对信息），使用本地缓存：

```python
# 已实现：exchange_info 缓存1小时
# 可以扩展到其他数据
```

### 2. 实现请求合并

将多个小请求合并为批量请求：

```python
# 例如：批量查询多个交易对的价格
# 使用 /fapi/v1/ticker/price（不带symbol参数）一次获取所有
```

### 3. 使用 WebSocket 替代轮询

对于实时数据，优先使用 WebSocket：

```python
# 已实现：用户数据流（订单更新、持仓更新）
# 可以扩展：市场数据流（价格、K线）
```

### 4. 实现智能降级

网络质量差时自动降级：

```python
class AdaptiveMonitor:
    def __init__(self):
        self.error_count = 0
        self.interval = 30
    
    async def adjust_interval(self):
        if self.error_count > 5:
            self.interval = min(self.interval * 2, 300)  # 最多5分钟
            logger.warning(f"网络质量差，监控间隔调整为 {self.interval}s")
        elif self.error_count == 0:
            self.interval = max(self.interval // 2, 30)  # 最少30秒
```

---

## 常见问题

### Q1: 为什么会频繁出现网络错误？

**A**: 可能原因：
1. 服务器网络质量差
2. VPN 不稳定
3. Binance API 限流
4. 超时设置过短

### Q2: 网络错误会影响交易吗？

**A**: 
- 系统有重试机制（默认3次）
- 短暂的网络错误不会影响交易
- 但频繁错误可能导致：
  - 监控延迟
  - 止盈止损触发延迟
  - 无法及时平仓

### Q3: 如何判断网络问题是否严重？

**A**: 观察以下指标：
- 错误频率：每分钟 > 5次 = 严重
- 重试次数：经常达到3次 = 严重
- 是否有 IP 封禁：有 = 非常严重

### Q4: IP 被封禁怎么办？

**A**:
1. 等待解封（通常2-10分钟）
2. 更换 IP（重启VPN或更换服务器）
3. 降低请求频率
4. 检查是否有其他程序在使用同一 API Key

### Q5: 可以完全避免网络错误吗？

**A**: 不能完全避免，但可以：
- 选择网络质量好的服务器
- 使用稳定的VPN
- 合理配置超时和重试
- 实现请求限流
- 使用 WebSocket 减少 REST 请求

---

## 监控指标

建议监控以下指标：

1. **网络错误率**: 每小时网络错误次数
2. **API 响应时间**: 平均响应时间
3. **重试成功率**: 重试后成功的比例
4. **IP 封禁次数**: 每天被封禁的次数

可以在 Web Dashboard 中添加这些指标的展示。

---

## 总结

网络问题是实盘交易系统的常见挑战，建议：

1. ✅ **短期**: 增加超时和重试次数
2. ✅ **中期**: 优化网络环境（VPN、服务器）
3. ✅ **长期**: 实现智能降级和请求优化

如果问题持续，建议：
- 更换服务器到网络质量更好的地区
- 使用专业的交易专用VPN
- 考虑使用 Binance 的备用 API 域名

---

**需要帮助？** 查看日志文件 `logs/duo-live.log` 获取详细错误信息。
