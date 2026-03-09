# Live Executor & Position Monitor

## `live_executor.py` — 下单执行器

负责**一次性的下单动作**，核心功能：

- **`open_position()`**：开仓入场，只下一个 LIMIT 限价入场单（BUY/SELL）
- **`place_tp_sl()`**：挂出 TP（止盈）和 SL（止损）的 algo 条件单
- 自动处理：精度（价格/数量）、保证金模式（逐仓）、杠杆设置、持仓模式（单向/双向）
- **关键设计**：入场时**不挂 TP/SL**，只返回 `deferred_tp_sl` 参数交给 Monitor

## `live_position_monitor.py` — 持仓监控器

负责**持续的仓位生命周期管理**，后台轮询（120s 间隔），核心功能：

| 阶段 | 功能 |
|---|---|
| **入场后** | 轮询检测入场单是否 FILLED → 成交后自动调用 Executor 挂 TP/SL |
| **持仓中** | 监控 TP/SL 触发状态；TP 触发则取消 SL，SL 触发则取消 TP |
| **动态调仓** | 2h/12h 强度评估，动态调整 TP 百分比 |
| **异常处理** | 检测孤立订单并取消；TP/SL 被手动取消时自动重挂 |
| **重启恢复** | `recover_positions()` 从交易所恢复已有仓位状态 |
| **强平** | `_force_close()` 支持分批市价平仓 |

## 协作流程

```
Trader/Strategy → Executor.open_position()  → 只挂入场限价单
                                              ↓ 返回 deferred_tp_sl
                  Monitor.track()            → 开始跟踪该仓位
                  Monitor._check_position()  → 轮询发现入场已成交
                  → Executor.place_tp_sl()   → 挂出 TP/SL 条件单
                  Monitor 持续监控            → 直到 TP/SL 触发或超时平仓
```

**简单说：Executor 负责"下单"，Monitor 负责"盯盘"。**
