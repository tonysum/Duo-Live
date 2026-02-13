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
