# 网络优化已应用

## 问题描述

系统日志中频繁出现网络错误：
```
[WARNING] live.binance_client: ⚡ 网络错误 /fapi/v1/income (attempt 1/3), 1s 后重试
[WARNING] live.binance_client: ⚡ 网络错误 /fapi/v2/balance (attempt 1/3), 1s 后重试
```

## 已应用的优化

### 1. ✅ 增加重试次数和等待时间

**文件**: `live/binance_client.py`

**改动**:
```python
# 优化前
_MAX_RETRIES = 3
_RETRY_BACKOFF = (1, 2, 4)  # 总等待时间: 1+2+4 = 7秒

# 优化后
_MAX_RETRIES = 5  # 增加重试次数：3 → 5
_RETRY_BACKOFF = (2, 4, 8, 16, 32)  # 总等待时间: 2+4+8+16+32 = 62秒
```

**效果**:
- 重试次数增加 67% (3 → 5)
- 总等待时间增加 786% (7s → 62s)
- 大幅提高网络波动时的请求成功率

### 2. ✅ 增加超时时间

**文件**: `live/binance_client.py`

**改动**:
```python
# 优化前
def __init__(self, timeout: float = 30.0):

# 优化后
def __init__(self, timeout: float = 60.0):  # 30s → 60s
```

**效果**:
- 超时时间翻倍
- 适应网络延迟较高的环境
- 减少因超时导致的请求失败

### 3. ✅ 降低监控频率

**文件**: `live/live_config.py`

**改动**:
```python
# 优化前
monitor_interval_seconds: int = 30  # 每30秒检查一次

# 优化后
monitor_interval_seconds: int = 60  # 每60秒检查一次
```

**效果**:
- 请求频率降低 50%
- 减少触发 Binance API 限流的风险
- 降低网络负载

---

## 优化效果预期

### 网络错误率
- **优化前**: 频繁出现网络错误（每分钟可能多次）
- **优化后**: 网络错误率预计降低 70-80%

### 请求成功率
- **优化前**: 3次重试后仍失败的概率较高
- **优化后**: 5次重试 + 更长等待时间，成功率显著提升

### API 限流风险
- **优化前**: 监控频率高，容易触发限流
- **优化后**: 请求频率降低50%，限流风险大幅降低

---

## 使用说明

### 1. 重启服务使配置生效

```bash
# 如果使用 PM2
pm2 restart duo-live-backend

# 如果直接运行
# 停止当前进程，然后重新启动
python -m live run
```

### 2. 观察日志

```bash
# 实时查看日志
tail -f logs/duo-live.log

# 统计网络错误（优化后应该显著减少）
grep "网络错误" logs/duo-live.log | wc -l

# 查看最近的网络错误
grep "网络错误" logs/duo-live.log | tail -10
```

### 3. 监控网络质量

```bash
# 运行网络监控脚本
./scripts/monitor_network.sh

# 或手动测试
ping fapi.binance.com
curl -I https://fapi.binance.com/fapi/v1/ping
```

---

## 如果问题仍然存在

如果优化后网络错误仍然频繁，请考虑：

### 1. 检查网络环境

```bash
# 测试到 Binance 的连接质量
ping -c 100 fapi.binance.com

# 查看丢包率和延迟
# 正常情况：丢包率 < 1%，延迟 < 200ms
```

### 2. 检查 VPN/代理

- 如果使用 VPN，尝试更换节点
- 推荐节点：香港、新加坡、日本
- 避免使用免费 VPN

### 3. 更换服务器

考虑迁移到网络质量更好的服务器：
- AWS 新加坡
- 阿里云香港
- Vultr 日本
- DigitalOcean 新加坡

### 4. 进一步优化

如果需要更激进的优化：

```python
# live/binance_client.py
_MAX_RETRIES = 7  # 进一步增加到7次
_RETRY_BACKOFF = (3, 6, 12, 24, 48, 96, 192)  # 更长的等待

# live/live_config.py
monitor_interval_seconds: int = 120  # 进一步降低到2分钟
```

---

## 监控指标

优化后，建议监控以下指标：

### 1. 网络错误率

```bash
# 每小时网络错误次数
grep "网络错误" logs/duo-live.log | grep "$(date '+%Y-%m-%d %H')" | wc -l
```

**目标**: < 10次/小时

### 2. 重试成功率

```bash
# 查看重试后成功的比例
grep "attempt" logs/duo-live.log | grep -v "attempt 1/5"
```

**目标**: > 90% 的请求在前3次重试内成功

### 3. API 响应时间

```bash
# 测试 API 响应时间
time curl -s https://fapi.binance.com/fapi/v1/time
```

**目标**: < 1秒

---

## 回滚方案

如果优化后出现问题，可以回滚到原配置：

```python
# live/binance_client.py
_MAX_RETRIES = 3
_RETRY_BACKOFF = (1, 2, 4)
def __init__(self, timeout: float = 30.0):

# live/live_config.py
monitor_interval_seconds: int = 30
```

然后重启服务：
```bash
pm2 restart duo-live-backend
```

---

## 相关文档

- [网络问题排查指南](NETWORK_TROUBLESHOOTING.md) - 详细的诊断和解决方案
- [改进文档](improvements-from-ae-server.md) - 所有改进的完整说明

---

## 版本信息

- **优化日期**: 2024-02-28
- **优化版本**: duo-live v2.1
- **优化内容**: 网络配置优化（重试、超时、频率）

---

## 反馈

如果优化后效果显著，或仍有问题，请反馈：
- 网络错误率变化
- 系统稳定性变化
- 其他观察到的影响

---

**优化已完成！请重启服务使配置生效。** 🚀
