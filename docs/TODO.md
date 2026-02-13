# 待修复问题

## 1. TP/SL 被手动删除后误判为触发

**文件**: `live/live_position_monitor.py` 第 360-408 行

**现象**: 用户在 Binance App 手动取消 TP 或 SL 条件单后，系统 30s 轮询发现 algo order 不存在，**误判为已触发**，导致：
- 撤销另一侧的保护单
- 标记仓位为 `closed`
- 实际仓位仍在，变成裸奔（无 TP/SL 保护）

**根因**: 系统仅通过 "algo order 是否还在 open 列表中" 来判断触发，无法区分：
- ✅ 订单被交易所执行（真触发）
- ❌ 订单被用户手动取消（假触发）

**相关代码**:
```python
tp_still_open = pos.tp_algo_id is not None and pos.tp_algo_id in algo_ids

if pos.tp_algo_id and not tp_still_open and not pos.tp_triggered:
    # 当前逻辑：直接判定为 "止盈触发"
    pos.tp_triggered = True
    pos.closed = True  # ← 危险：仓位可能还在
```

**修复思路（待确认）**:

发现 algo order 消失时，先检查持仓是否还存在：
- 持仓已平 → 真触发，正常处理
- 持仓还在 → 被手动删除，应该补挂 TP/SL 而非标记关闭

```python
# 伪代码
if tp_disappeared:
    position_still_exists = check_position_risk(symbol)
    if position_still_exists:
        # 被手动删了，补挂
        re_place_tp_order(pos)
        notify("⚠️ TP 被手动取消，已自动补挂")
    else:
        # 真触发
        pos.tp_triggered = True
        cancel_sl()
        pos.closed = True
```

**优先级**: 🔴 高 — 影响资金安全
