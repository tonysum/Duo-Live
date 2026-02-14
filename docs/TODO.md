# 待修复问题

## ~~1. TP/SL 被手动删除后误判为触发~~ ✅ 已修复

**状态**: ✅ 已修复 (2026-02-13)

**修复方案**: 当 algo order 消失时，先调用 `get_position_risk(symbol)` 验证持仓是否仍存在：
- 持仓已平 (amt=0) → 真触发，正常处理
- 持仓还在 (amt≠0) → 被手动删除，调用 `_re_place_single_order()` 自动补挂

**修改文件**: `live/live_position_monitor.py`
- `_check_position()` — 增加交易所持仓验证
- `_get_exchange_position_amt()` — 新方法，查询交易所实际持仓
- `_re_place_single_order()` — 新方法，自动补挂单笔 TP/SL

**测试**: `tests/test_tp_sl_detection.py` (12 个测试全部通过)

## 2. 运行时热切换策略 (Hot-Swap Strategy)

**状态**: 💡 待实现

**背景**: 当前 `Strategy` 在 `LiveTrader.__init__` 中一次性绑定，切换策略需要停止整个程序。

**目标**: 在不停止服务的情况下，动态切换到不同的 `Strategy` 实现。

**关键挑战**:
- Scanner 与策略强耦合（`create_scanner` 在启动时绑定），切换需管理 Scanner 生命周期
- 已有持仓的归属：新策略是否接管旧策略开出的仓位
- 需要线程/协程安全的策略引用替换

**可能的最简方案**: 仅对 `filter_entry` 做热切换（新信号筛选），Scanner 和持仓管理保持不变。
