"""Live position monitor — deferred TP/SL after entry fill.

Flow:
  1. Executor places entry LIMIT order only
  2. Monitor polls for entry FILLED → then places TP/SL algo orders
  3. Monitor tracks TP/SL triggers → auto-cancels the other
  4. Max hold time → force market close

Usage:
    monitor = LivePositionMonitor(client, executor, config)
    monitor.track(symbol, entry_order_id, side, quantity, deferred_tp_sl)
    await monitor.run_forever()
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional

from .binance_client import BinanceFuturesClient, BinanceAPIError
from .live_config import LiveTradingConfig

logger = logging.getLogger(__name__)

# TYPE_CHECKING imports to avoid circular dependency
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .strategy import Strategy


@dataclass
class TrackedPosition:
    """State for a live position being monitored."""

    symbol: str
    entry_order_id: int
    side: str  # "SHORT" or "LONG"
    quantity: str

    # Deferred TP/SL parameters (from executor)
    deferred_tp_sl: dict[str, str]

    # State tracking
    entry_filled: bool = False
    entry_price: Optional[Decimal] = None
    entry_fill_time: Optional[datetime] = None  # when entry was filled
    tp_sl_placed: bool = False
    tp_algo_id: Optional[int] = None
    sl_algo_id: Optional[int] = None
    tp_triggered: bool = False
    sl_triggered: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed: bool = False

    # Dynamic TP (V2 strength evaluation)
    current_tp_pct: float = 33.0  # current take-profit percentage
    current_sl_pct: float = 18.0  # current stop-loss percentage
    evaluated_2h: bool = False
    evaluated_12h: bool = False
    strength: str = "unknown"  # strong / medium / weak

    # Re-place failure counters (stop retrying after MAX_REPLACE_ATTEMPTS)
    tp_fail_count: int = 0
    sl_fail_count: int = 0

    # Rolling strategy fields
    lowest_price: Optional[Decimal] = None    # 追踪止损: 持仓期间最低价
    has_added_position: bool = False           # 是否已加仓

    # Multi-strategy: logical owner (persisted in position_attribution)
    strategy_id: str = ""


class LivePositionMonitor:
    """Monitor live positions with deferred TP/SL placement.

    TP/SL algo orders are placed ONLY after the entry order fills,
    matching Binance App behavior and preventing -2021 errors.
    """

    # exchange_info refresh interval (4 hours)
    _EXCHANGE_INFO_TTL: int = 4 * 3600

    def __init__(
        self,
        client: BinanceFuturesClient,
        executor,  # LiveOrderExecutor (avoids circular import)
        config: Optional[LiveTradingConfig] = None,
        poll_interval: int = 120,
        notifier=None,  # TelegramNotifier (optional)
        store=None,     # TradeStore (optional, for live trade recording)
        strategy: "Strategy | None" = None,
        strategy_registry: dict[str, "Strategy"] | None = None,
        on_sl_triggered=None,  # S: callable(symbol, strategy_id) on SL exit
        quota_manager=None,  # QuotaManager (optional, for multi-strategy quota)
    ):
        self.client = client
        self.executor = executor
        self.config = config or LiveTradingConfig()
        self.poll_interval = poll_interval
        self.notifier = notifier
        self.store = store
        self.on_sl_triggered = on_sl_triggered  # S
        self.quota_manager = quota_manager  # Multi-strategy quota manager
        self.strategy = strategy
        if strategy_registry is not None:
            self._strategy_registry: dict[str, Any] = dict(strategy_registry)
        else:
            from .strategy import strategy_registry_key

            k = strategy_registry_key(strategy)
            self._strategy_registry = {k: strategy} if strategy else {}
        self._default_strategy = strategy
        # Shortcut to primary rolling config (fallback when per-position unknown)
        self._rc = getattr(strategy, 'config', None) if strategy else None
        self._positions: dict[str, TrackedPosition] = {}  # symbol → position
        self._running = False
        self._poll_count: int = 0  # incremented each _check_all; used for periodic cleanup

        # exchange_info cache: {symbol: tick_size (Decimal)}
        # Refreshed every _EXCHANGE_INFO_TTL seconds to avoid per-poll API spam
        self._tick_cache: dict[str, Any] = {}  # symbol → Decimal tick_size or int price_precision
        self._tick_cache_ts: float = 0.0  # epoch seconds of last full refresh

    def _resolve_eval_strategy(self, pos: TrackedPosition) -> Any:
        """Pick Strategy instance for this position (multi-strategy)."""
        sid = (pos.strategy_id or "").strip()
        if sid and sid in self._strategy_registry:
            return self._strategy_registry[sid]
        if self.store and not sid:
            got = self.store.get_position_attribution(pos.symbol, pos.side)
            if got:
                pos.strategy_id = got
                if got in self._strategy_registry:
                    return self._strategy_registry[got]
        return self._default_strategy

    def _rolling_cfg_for_position(self, pos: TrackedPosition) -> Any:
        """RollingLiveConfig (or None) for TP/SL % fallbacks for this position."""
        ev = self._resolve_eval_strategy(pos)
        return getattr(ev, "config", None) if ev else self._rc

    def _clear_closed_position_store(self, pos: TrackedPosition) -> None:
        """清理已关闭持仓的存储状态，并更新配额"""
        if not self.store:
            return
        try:
            self.store.delete_position_state(pos.symbol)
        except Exception as e:
            logger.debug("delete_position_state: %s", e)
        try:
            self.store.delete_position_attribution(pos.symbol, pos.side)
        except Exception as e:
            logger.debug("delete_position_attribution: %s", e)
        
        # ── 更新策略配额：减少持仓计数 ──
        if self.quota_manager and pos.strategy_id:
            quota = self.quota_manager.get_quota(pos.strategy_id)
            if quota:
                quota.decrement_position()
                logger.info(
                    f"[{pos.strategy_id}] 配额更新: 持仓数 {quota.current_positions}/{quota.max_positions}"
                )

    def track(
        self,
        symbol: str,
        entry_order_id: int,
        side: str,
        quantity: str,
        deferred_tp_sl: dict[str, str],
        tp_pct: float = 33.0,
        sl_pct: float = 18.0,
        strategy_id: str = "",
    ):
        """Start tracking a new position (entry pending, TP/SL deferred)."""
        pos = TrackedPosition(
            symbol=symbol,
            entry_order_id=entry_order_id,
            side=side,
            quantity=quantity,
            deferred_tp_sl=deferred_tp_sl,
            current_tp_pct=tp_pct,
            current_sl_pct=sl_pct,
            strategy_id=strategy_id or "",
        )
        self._positions[symbol] = pos
        logger.info(
            "📌 开始追踪: %s %s entry=%s (TP/SL 待入场成交后挂出)",
            symbol, side, entry_order_id,
        )

    async def recover_positions(self):
        """Recover tracked positions from exchange state after restart.

        For each non-zero position on the account:
          - Creates a TrackedPosition with entry_filled=True
          - Matches existing algo orders (TP/SL) by symbol + side
        """
        try:
            all_positions = await self.client.get_position_risk()
        except Exception as e:
            logger.error("恢复持仓失败 (无法获取持仓): %s", e)
            return

        recovered = 0
        for pos_risk in all_positions:
            amt = float(pos_risk.position_amt)
            if amt == 0:
                continue

            symbol = pos_risk.symbol
            is_long = amt > 0
            side = "LONG" if is_long else "SHORT"
            qty = str(abs(amt))
            close_side = "SELL" if is_long else "BUY"

            # Already tracking this symbol? Skip.
            if symbol in self._positions:
                continue

            # Find existing algo orders (TP/SL) for this symbol
            tp_algo_id = None
            sl_algo_id = None
            try:
                algo_orders = await self.client.get_open_algo_orders(symbol)
                for ao in algo_orders:
                    # Detect type from clientAlgoId prefix or order type
                    algo_id_str = str(getattr(ao, 'client_algo_id', '') or '')
                    if algo_id_str.startswith("tp_"):
                        tp_algo_id = ao.algo_id
                    elif algo_id_str.startswith("sl_"):
                        sl_algo_id = ao.algo_id
                    elif hasattr(ao, 'algo_type') or hasattr(ao, 'type'):
                        # Fallback: guess from order structure
                        raw = getattr(ao, '_raw', {})
                        if not tp_algo_id:
                            tp_algo_id = ao.algo_id
                        elif not sl_algo_id:
                            sl_algo_id = ao.algo_id
            except Exception as e:
                logger.debug("获取 %s algo orders 失败: %s", symbol, e)

            tracked = TrackedPosition(
                symbol=symbol,
                entry_order_id=0,  # Unknown after restart
                side=side,
                quantity=qty,
                deferred_tp_sl={},  # Not needed — already filled
                entry_filled=True,
                entry_price=pos_risk.entry_price,
                tp_sl_placed=tp_algo_id is not None or sl_algo_id is not None,
                tp_algo_id=tp_algo_id,
                sl_algo_id=sl_algo_id,
            )

            # ── Restore TP/strength state from DB (prevents strong_tp_pct hard-reset) ──
            if self.store:
                saved = self.store.get_position_state(symbol)
                if saved:
                    tracked.current_tp_pct = saved["current_tp_pct"]
                    tracked.strength = saved["strength"]
                    tracked.evaluated_2h = saved["evaluated_2h"]
                    tracked.evaluated_12h = saved["evaluated_12h"]
                    logger.info(
                        "🔄 已恢复 TP 状态: %s tp_pct=%s%% strength=%s",
                        symbol, tracked.current_tp_pct, tracked.strength,
                    )

            if self.store:
                tid = self.store.get_position_attribution(symbol, side)
                if tid:
                    tracked.strategy_id = tid

            self._positions[symbol] = tracked
            recovered += 1

            tp_info = f"tp={tp_algo_id}" if tp_algo_id else "无TP"
            sl_info = f"sl={sl_algo_id}" if sl_algo_id else "无SL"
            logger.info(
                "🔄 恢复持仓: %s %s qty=%s entry=%s [%s, %s]",
                symbol, side, qty, pos_risk.entry_price, tp_info, sl_info,
            )

            # ── Auto-place TP/SL if missing (crash between fill and TP/SL) ──
            if not tracked.tp_sl_placed and pos_risk.entry_price:
                try:
                    entry_p = Decimal(str(pos_risk.entry_price))
                    # Use persisted tp_pct if available, otherwise fall back to strong_tp_pct
                    tp_pct = tracked.current_tp_pct
                    _rc = self._rolling_cfg_for_position(tracked)
                    sl_pct = _rc.sl_threshold * 100 if _rc else 44.0

                    if side == "SHORT":
                        tp_price = entry_p * (1 - Decimal(str(tp_pct)) / 100)
                        sl_price = entry_p * (1 + Decimal(str(sl_pct)) / 100)
                        close_side_order = "BUY"
                    else:
                        tp_price = entry_p * (1 + Decimal(str(tp_pct)) / 100)
                        sl_price = entry_p * (1 - Decimal(str(sl_pct)) / 100)
                        close_side_order = "SELL"

                    # Round to exchange precision
                    tp_price = await self._round_trigger_price(symbol, tp_price)
                    sl_price = await self._round_trigger_price(symbol, sl_price)

                    is_hedge = await self.client.get_position_mode()
                    pos_side = side if is_hedge else "BOTH"

                    rounded_qty = await self._round_quantity(symbol, qty)
                    tp_sl_result = await self.executor.place_tp_sl(
                        symbol=symbol,
                        close_side=close_side_order,
                        pos_side=pos_side,
                        tp_price=str(tp_price),
                        sl_price=str(sl_price),
                        quantity=rounded_qty,
                        order_prefix=f"rc_{symbol[:6].lower()}",
                    )
                    if tp_sl_result.get("tp_order"):
                        tracked.tp_algo_id = tp_sl_result["tp_order"].algo_id
                    if tp_sl_result.get("sl_order"):
                        tracked.sl_algo_id = tp_sl_result["sl_order"].algo_id
                    tracked.tp_sl_placed = True
                    logger.info(
                        "🔄 自动补挂 TP/SL: %s tp=%s sl=%s",
                        symbol, tracked.tp_algo_id, tracked.sl_algo_id,
                    )
                    if self.notifier:
                        await self.notifier.send(
                            f"🔄 <b>恢复补挂 TP/SL</b>\n"
                            f"  {symbol} {side}\n"
                            f"  止盈: {tp_price}\n"
                            f"  止损: {sl_price}"
                        )
                except Exception as e:
                    logger.error("❌ 恢复时补挂 TP/SL 失败 %s: %s", symbol, e)

        if recovered > 0:
            logger.info("🔄 共恢复 %d 个持仓", recovered)
            if self.notifier:
                await self.notifier.send(
                    f"🔄 <b>断线恢复</b>\n  已恢复 {recovered} 个持仓监控"
                )
        else:
            logger.info("🔄 无持仓需要恢复")

        # Initial orphan cleanup at startup (covers orders left from previous session)
        try:
            await self._cancel_orphan_orders()
        except Exception as e:
            logger.warning("启动时孤立挂单清理失败: %s", e)

    async def run_forever(self):
        """Main monitoring loop."""
        self._running = True
        logger.info("🔍 LivePositionMonitor started (interval=%ds)", self.poll_interval)
        while self._running:
            try:
                await self._check_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Monitor error: %s", e, exc_info=True)
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False

    async def _check_all(self):
        """Check all tracked positions."""
        self._poll_count += 1
        closed_symbols = []
        for symbol, pos in self._positions.items():
            if pos.closed:
                closed_symbols.append(symbol)
                continue
            try:
                await self._check_position(pos)
            except Exception as e:
                logger.warning("Check failed for %s: %s", symbol, e)

        for sym in closed_symbols:
            del self._positions[sym]

        # Every 10 poll cycles (~20 min at default interval) clean up orphan orders
        if self._poll_count % 10 == 1:  # start at cycle 1, not 0
            try:
                await self._cancel_orphan_orders()
            except Exception as e:
                logger.warning("Orphan order cleanup error: %s", e)

    async def _cancel_orphan_orders(self):
        """Cancel algo orders that have no corresponding open position.

        An order is considered orphaned when:
        - Its symbol has no open position on the exchange, AND
        - It is not tracked by the monitor (i.e. not a pending entry order)
        
        Also detects and removes duplicate TP/SL orders for the same position.
        """
        try:
            algo_orders = await self.client.get_open_algo_orders()
        except Exception as e:
            logger.debug("Orphan check: failed to fetch algo orders: %s", e)
            return

        if not algo_orders:
            return

        try:
            all_pos = await self.client.get_position_risk()
            open_symbols = {p.symbol for p in all_pos if float(p.position_amt) != 0}
        except Exception as e:
            logger.debug("Orphan check: failed to fetch positions: %s", e)
            return

        # Symbols actively tracked by the monitor (may not have exchange position yet)
        tracked_symbols = set(self._positions.keys())

        # ── 1. Check for duplicate TP/SL orders ──
        from collections import defaultdict
        orders_by_symbol = defaultdict(list)
        for ao in algo_orders:
            if ao.symbol in open_symbols or ao.symbol in tracked_symbols:
                orders_by_symbol[ao.symbol].append(ao)
        
        duplicates_cancelled = 0
        for sym, orders in orders_by_symbol.items():
            # Group by order type
            tp_orders = [o for o in orders if o.order_type == "TAKE_PROFIT_MARKET"]
            sl_orders = [o for o in orders if o.order_type == "STOP_MARKET"]
            
            # If multiple TP orders exist, keep only the first one
            if len(tp_orders) > 1:
                logger.error(
                    "❌ 检测到重复的止盈单: %s (共%d个) — 保留第一个，取消其余",
                    sym, len(tp_orders)
                )
                for extra in tp_orders[1:]:
                    try:
                        await self.client.cancel_algo_order(sym, algo_id=extra.algo_id)
                        duplicates_cancelled += 1
                        logger.info(
                            "🗑️ 已取消重复的止盈单: %s (algoId=%s, 触发价=%s)",
                            sym, extra.algo_id, extra.trigger_price
                        )
                    except Exception as e:
                        logger.warning("取消重复止盈单失败: %s", e)
                
                # Update tracked position with the kept order
                if sym in self._positions:
                    self._positions[sym].tp_algo_id = tp_orders[0].algo_id
            
            # If multiple SL orders exist, keep only the first one
            if len(sl_orders) > 1:
                logger.error(
                    "❌ 检测到重复的止损单: %s (共%d个) — 保留第一个，取消其余",
                    sym, len(sl_orders)
                )
                for extra in sl_orders[1:]:
                    try:
                        await self.client.cancel_algo_order(sym, algo_id=extra.algo_id)
                        duplicates_cancelled += 1
                        logger.info(
                            "🗑️ 已取消重复的止损单: %s (algoId=%s, 触发价=%s)",
                            sym, extra.algo_id, extra.trigger_price
                        )
                    except Exception as e:
                        logger.warning("取消重复止损单失败: %s", e)
                
                # Update tracked position with the kept order
                if sym in self._positions:
                    self._positions[sym].sl_algo_id = sl_orders[0].algo_id
        
        if duplicates_cancelled:
            logger.warning("🧹 重复挂单清除完成: 共删除 %d 单", duplicates_cancelled)
            if self.notifier:
                await self.notifier.send(
                    f"⚠️ <b>检测到重复挂单</b>\n"
                    f"  已自动清除 {duplicates_cancelled} 个重复订单"
                )

        # ── 2. Cancel orphaned orders ──
        orphans_cancelled = 0
        for ao in algo_orders:
            sym = ao.symbol
            if sym in open_symbols or sym in tracked_symbols:
                continue  # has a live or in-flight position, not orphaned

            # This algo order has no corresponding position — cancel it
            try:
                await self.client.cancel_algo_order(sym, algo_id=ao.algo_id)
                orphans_cancelled += 1
                logger.warning(
                    "🗑️ 删除孤立挂单: %s algoId=%s type=%s triggerPrice=%s",
                    sym, ao.algo_id, ao.order_type, ao.trigger_price,
                )
                if self.notifier:
                    await self.notifier.send(
                        f"⚠️ <b>废弃挂单已清除</b>\n"
                        f"  {sym} {ao.order_type} 触发价={ao.trigger_price}\n"
                        f"  (algoId={ao.algo_id})"
                    )
            except Exception as e:
                logger.warning("删除孤立挂单失败 %s algoId=%s: %s", sym, ao.algo_id, e)

        if orphans_cancelled:
            logger.info("🧹 废弃挂单清除完成: 共删除 %d 单", orphans_cancelled)
        else:
            logger.debug("🧹 当前无孤立挂单")

    async def _check_position(self, pos: TrackedPosition):
        """Check a single position for order fills and triggers."""
        now = datetime.now(timezone.utc)

        # ── 1. Check entry order fill ──────────────────────────────────
        if not pos.entry_filled:
            try:
                order = await self.client.query_order(
                    pos.symbol, order_id=pos.entry_order_id
                )
                if order.status == "FILLED":
                    pos.entry_filled = True
                    pos.entry_price = Decimal(str(order.avg_price or order.price))
                    pos.entry_fill_time = now
                    logger.info(
                        "✅ 入场单成交: %s %s @ %s",
                        pos.symbol, pos.side, pos.entry_price,
                    )
                    if self.notifier:
                        await self.notifier.notify_entry_filled(
                            pos.symbol, pos.side, str(pos.entry_price),
                        )
                    self._record_live_trade(
                        pos, event="entry",
                        entry_price=str(pos.entry_price),
                        order_id=str(pos.entry_order_id),
                    )
                    # Place deferred TP/SL now
                    await self._place_deferred_tp_sl(pos)

                elif order.status in ("CANCELED", "EXPIRED", "REJECTED"):
                    logger.warning(
                        "⚠️ 入场单 %s: %s — 停止追踪",
                        order.status, pos.symbol,
                    )
                    pos.closed = True
                    return
            except BinanceAPIError as e:
                logger.debug("Query entry order failed: %s", e)

        # Skip further checks if entry not yet filled
        if not pos.entry_filled:
            return

        # ── 1.1. Retry deferred TP/SL if initial placement failed ──────
        if not pos.tp_sl_placed:
            logger.warning(
                "⚠️ TP/SL 未挂出，重试: %s", pos.symbol,
            )
            await self._place_deferred_tp_sl(pos)
            if not pos.tp_sl_placed:
                return  # still failed, skip remaining checks

        # ── 1.5. Strategy-based position evaluation ─────────────
        eval_strat = self._resolve_eval_strategy(pos)
        if eval_strat:
            from .strategy import PositionAction
            action: PositionAction = await eval_strat.evaluate_position(
                client=self.client,
                pos=pos,
                config=self.config,
                now=now,
            )
            if action.action == "close":
                logger.warning(
                    "⏰ 策略平仓: %s — %s", pos.symbol, action.reason,
                )
                if self.notifier:
                    if action.reason == "max_hold_time":
                        _rc = self._rolling_cfg_for_position(pos)
                        await self.notifier.notify_timeout_close(
                            pos.symbol, _rc.max_hold_days * 24 if _rc else 264,
                        )
                    else:
                        await self.notifier.send(
                            f"⚠️ <b>策略平仓</b>\n  {pos.symbol}: {action.reason}"
                        )
                self._record_live_trade(
                    pos, event=action.reason or "strategy_close",
                    entry_price=str(pos.entry_price or ""),
                )
                await self._force_close(pos)
                return

            elif action.action == "adjust_tp":
                pos.current_tp_pct = action.new_tp_pct
                if action.new_strength:
                    pos.strength = action.new_strength
                await self._replace_tp_order(pos)
                return

            elif action.action == "add_position":
                # Add to existing position (加仓)
                await self._handle_add_position(pos, action)
                return

            # action == "hold" → fall through

        # ── 2. Check TP/SL algo order status ──────────────────────────
        try:
            algo_orders = await self.client.get_open_algo_orders(pos.symbol)
            algo_ids = {o.algo_id for o in algo_orders}

            # ── 2.1. Verify TP order direction is correct ──────────────
            if pos.tp_algo_id and pos.tp_algo_id in algo_ids:
                tp_order = next((o for o in algo_orders if o.algo_id == pos.tp_algo_id), None)
                if tp_order:
                    # Get actual position direction from exchange
                    try:
                        actual_qty = await self._get_exchange_position_amt(pos.symbol)
                        if actual_qty != 0:
                            is_long = actual_qty > 0
                            correct_side = "SELL" if is_long else "BUY"
                            
                            if tp_order.side != correct_side:
                                logger.error(
                                    "❌ 检测到止盈单方向错误: %s (algoId=%s)\n"
                                    "   持仓方向: %s, 止盈单方向: %s, 应该是: %s\n"
                                    "   自动取消并重新创建正确的止盈单",
                                    pos.symbol, pos.tp_algo_id,
                                    "LONG" if is_long else "SHORT",
                                    tp_order.side, correct_side
                                )
                                
                                # Cancel wrong TP order
                                try:
                                    await self.client.cancel_algo_order(pos.symbol, algo_id=pos.tp_algo_id)
                                    logger.info("🗑️ 已取消错误的止盈单: %s", pos.tp_algo_id)
                                    pos.tp_algo_id = None
                                    
                                    # Update position side if needed
                                    actual_side = "LONG" if is_long else "SHORT"
                                    if pos.side != actual_side:
                                        logger.warning(
                                            "⚠️ 更正持仓方向: %s → %s",
                                            pos.side, actual_side
                                        )
                                        pos.side = actual_side
                                    
                                    # Update quantity
                                    pos.quantity = str(abs(actual_qty))
                                    
                                    # Re-place with correct direction
                                    await self._re_place_single_order(pos, "tp")
                                    
                                    if self.notifier:
                                        await self.notifier.send(
                                            f"⚠️ <b>止盈单方向错误已修复</b>\n"
                                            f"  {pos.symbol}\n"
                                            f"  持仓: {actual_side}\n"
                                            f"  已重新创建正确的止盈单"
                                        )
                                except Exception as e:
                                    logger.error("❌ 修复错误止盈单失败: %s", e)
                    except Exception as e:
                        logger.warning("验证止盈单方向时出错: %s", e)

            tp_still_open = pos.tp_algo_id is not None and pos.tp_algo_id in algo_ids
            sl_still_open = pos.sl_algo_id is not None and pos.sl_algo_id in algo_ids

            if pos.tp_algo_id and not tp_still_open and not pos.tp_triggered:
                # TP disappeared — verify via exchange position
                exchange_amt = await self._get_exchange_position_amt(pos.symbol)
                if exchange_amt == 0:
                    # Real trigger — position is closed on exchange
                    pos.tp_triggered = True
                    logger.info("🎯 止盈触发: %s (algoId=%s)", pos.symbol, pos.tp_algo_id)
                    if self.notifier:
                        # REST path: we don't have the exact exit price, use TP target as estimate
                        tp_price_str = ""
                        if pos.entry_price and pos.current_tp_pct:
                            is_long = pos.side == "LONG"
                            mult = (1 + pos.current_tp_pct / 100) if is_long else (1 - pos.current_tp_pct / 100)
                            tp_price_str = str(round(float(pos.entry_price) * mult, 6))
                        await self.notifier.notify_tp_triggered(
                            pos.symbol, pos.side, price=tp_price_str
                        )
                    self._record_live_trade(
                        pos, event="tp",
                        entry_price=str(pos.entry_price or ""),
                        algo_id=str(pos.tp_algo_id),
                    )
                    if sl_still_open:
                        try:
                            await self.client.cancel_algo_order(
                                pos.symbol, algo_id=pos.sl_algo_id
                            )
                            logger.info("🗑️ 已撤销止损单: %s", pos.sl_algo_id)
                        except Exception as e:
                            logger.warning("撤销止损单失败: %s", e)
                    pos.closed = True
                    self._clear_closed_position_store(pos)
                else:
                    # Manually cancelled — auto re-place TP
                    logger.warning(
                        "⚠️ 止盈单被手动取消: %s (algoId=%s) — 自动补挂",
                        pos.symbol, pos.tp_algo_id,
                    )
                    await self._re_place_single_order(pos, "tp")

            if pos.sl_algo_id and not sl_still_open and not pos.sl_triggered:
                # SL disappeared — verify via exchange position
                exchange_amt = await self._get_exchange_position_amt(pos.symbol)
                if exchange_amt == 0:
                    # Real trigger — position is closed on exchange
                    pos.sl_triggered = True
                    logger.info("🛑 止损触发: %s (algoId=%s)", pos.symbol, pos.sl_algo_id)
                    if self.notifier:
                        # REST path: SL exit price unknown, omit
                        await self.notifier.notify_sl_triggered(
                            pos.symbol, pos.side
                        )
                    # S: notify scanner to block same-day re-entry
                    if self.on_sl_triggered:
                        self.on_sl_triggered(
                            pos.symbol, pos.strategy_id or "",
                        )
                    self._record_live_trade(
                        pos, event="sl",
                        entry_price=str(pos.entry_price or ""),
                        algo_id=str(pos.sl_algo_id),
                    )
                    if tp_still_open:
                        try:
                            await self.client.cancel_algo_order(
                                pos.symbol, algo_id=pos.tp_algo_id
                            )
                            logger.info("🗑️ 已撤销止盈单: %s", pos.tp_algo_id)
                        except Exception as e:
                            logger.warning("撤销止盈单失败: %s", e)
                    pos.closed = True
                    self._clear_closed_position_store(pos)
                else:
                    # Manually cancelled — auto re-place SL
                    logger.warning(
                        "⚠️ 止损单被手动取消: %s (algoId=%s) — 自动补挂",
                        pos.symbol, pos.sl_algo_id,
                    )
                    await self._re_place_single_order(pos, "sl")

        except Exception as e:
            logger.debug("Algo order check failed: %s", e)

        # ── 2.5. Fallback: re-place if algo ID lost (e.g. failed replacement) ──
        if not pos.closed and pos.tp_sl_placed:
            if pos.tp_algo_id is None and not pos.tp_triggered:
                logger.warning(
                    "⚠️ 检测到止盈单丢失 (algo_id=None): %s — 自动补挂",
                    pos.symbol,
                )
                await self._re_place_single_order(pos, "tp")
            if pos.sl_algo_id is None and not pos.sl_triggered:
                logger.warning(
                    "⚠️ 检测到止损单丢失 (algo_id=None): %s — 自动补挂",
                    pos.symbol,
                )
                await self._re_place_single_order(pos, "sl")

        # ── 3. Max hold time enforcement (legacy fallback, strategy handles this) ──
        if not pos.closed and pos.entry_filled and not self._default_strategy:
            hold_hours = (now - pos.created_at).total_seconds() / 3600
            _rc = self._rolling_cfg_for_position(pos)
            _max_hold_h = _rc.max_hold_days * 24 if _rc else 264
            if hold_hours >= _max_hold_h:
                logger.warning(
                    "⏰ 持仓超时 (%dh): %s — 市价平仓",
                    _max_hold_h, pos.symbol,
                )
                if self.notifier:
                    await self.notifier.notify_timeout_close(
                        pos.symbol, _max_hold_h,
                    )
                self._record_live_trade(
                    pos, event="timeout",
                    entry_price=str(pos.entry_price or ""),
                )
                await self._force_close(pos)

    async def _place_deferred_tp_sl(self, pos: TrackedPosition):
        """Place TP/SL algo orders after entry fill."""
        params = pos.deferred_tp_sl
        try:
            tp_sl_result = await self.executor.place_tp_sl(
                symbol=params["symbol"],
                close_side=params["close_side"],
                pos_side=params["pos_side"],
                tp_price=params["tp_price"],
                sl_price=params["sl_price"],
                quantity=params["quantity"],
                order_prefix=params["order_prefix"],
            )
            if tp_sl_result.get("tp_order"):
                pos.tp_algo_id = tp_sl_result["tp_order"].algo_id
            if tp_sl_result.get("sl_order"):
                pos.sl_algo_id = tp_sl_result["sl_order"].algo_id
            pos.tp_sl_placed = True
            logger.info(
                "🎯 TP/SL 已挂出: %s tp=%s sl=%s",
                pos.symbol, pos.tp_algo_id, pos.sl_algo_id,
            )
            if self.notifier:
                await self.notifier.notify_tp_sl_placed(
                    pos.symbol, params["tp_price"], params["sl_price"],
                )
        except Exception as e:
            logger.error("❌ 挂出 TP/SL 失败 %s: %s", pos.symbol, e)

    async def _force_close(self, pos: TrackedPosition):
        """Force close a position with a market order and cancel TP/SL.
        
        🔧 改进（从 AE Server 移植）：
        1. 平仓前先取消所有未成交订单
        2. 从交易所获取实际持仓数量和方向（避免程序记录不准确）
        3. 动态获取数量精度并调整
        4. 根据实际仓位方向决定平仓买卖方向
        5. 支持分批平仓（保证金不足时）
        """
        symbol = pos.symbol
        
        # 🔧 步骤1：平仓前先取消所有未成交的止盈止损订单
        logger.info(f"🔄 {symbol} 平仓前取消所有未成交订单...")
        cancelled_orders = []  # 记录被取消的订单
        try:
            algo_orders = await self.client.get_open_algo_orders(symbol)
            if algo_orders:
                logger.info(f"📋 {symbol} 找到 {len(algo_orders)} 个未成交订单，准备取消")
                for order in algo_orders:
                    order_type = order.order_type
                    order_id = order.algo_id
                    trigger_price = order.trigger_price
                    
                    try:
                        await self.client.cancel_algo_order(
                            symbol=symbol,
                            algo_id=order_id
                        )
                        cancelled_orders.append({
                            'type': order_type,
                            'id': order_id,
                            'price': trigger_price
                        })
                        logger.info(f"✅ {symbol} 已取消订单: {order_type} (ID: {order_id}, 价格: {trigger_price})")
                    except Exception as cancel_error:
                        logger.error(f"❌ {symbol} 取消订单失败 (ID: {order_id}): {cancel_error}")
            else:
                logger.info(f"✅ {symbol} 没有未成交订单")
        except Exception as cancel_all_error:
            logger.error(f"❌ {symbol} 查询/取消订单失败: {cancel_all_error}")
        
        # 🔧 步骤2：从交易所获取实际持仓数量和方向（避免程序记录不准确）
        try:
            positions_info = await self.client.get_position_risk(symbol)
            actual_position = None
            for p in positions_info:
                if p.symbol == symbol:
                    actual_position = p
                    break

            if actual_position:
                actual_amt = float(actual_position.position_amt)
                quantity = abs(actual_amt)  # 取绝对值作为平仓数量
                is_long_position = actual_amt > 0  # 正数=做多，负数=做空

                logger.info(f"📊 {symbol} 从交易所获取实际持仓: 数量={actual_amt} (方向={'做多' if is_long_position else '做空'}, 记录数量: {pos.quantity})")
            else:
                quantity = float(pos.quantity)
                is_long_position = pos.side == "LONG"  # 使用程序记录的方向
                logger.warning(f"⚠️ {symbol} 无法获取实际持仓，使用程序记录数量: {quantity} (方向: {pos.side})")
        except Exception as get_position_error:
            quantity = float(pos.quantity)
            is_long_position = pos.side == "LONG"
            logger.warning(f"⚠️ {symbol} 获取实际持仓失败: {get_position_error}，使用程序记录数量: {quantity} (方向: {pos.side})")

        # 🔧 步骤3：动态获取数量精度并调整（使用round而非int，避免丢失）
        try:
            info = await self.client.get_exchange_info()
            symbol_info = None
            for s in info.symbols:
                if s.symbol == symbol:
                    symbol_info = s
                    break

            if symbol_info:
                # 查找 LOT_SIZE 过滤器
                step_size = None
                for f in symbol_info.filters:
                    if f.filter_type.value == "LOT_SIZE" and f.step_size is not None:
                        step_size = float(f.step_size)
                        break
                
                if step_size:
                    # 根据stepSize精度调整（使用round四舍五入，而非int向下截断）
                    if step_size >= 1:
                        quantity_adjusted = round(quantity / step_size) * step_size
                        quantity_adjusted = int(quantity_adjusted)
                        qty_precision = 0
                    else:
                        import math
                        qty_precision = abs(int(math.log10(step_size)))
                        # 四舍五入到stepSize的整数倍
                        quantity_adjusted = round(quantity / step_size) * step_size
                        quantity_adjusted = round(quantity_adjusted, qty_precision)

                    logger.info(f"📏 {symbol} 数量精度调整: {quantity} → {quantity_adjusted} (stepSize={step_size})")
                    quantity = quantity_adjusted
                else:
                    quantity = round(quantity, 3)
            else:
                quantity = round(quantity, 3)
        except Exception as precision_error:
            logger.warning(f"⚠️ {symbol} 获取精度失败: {precision_error}，使用默认精度")
            quantity = round(quantity, 3)

        # 🔧 步骤4：根据实际仓位方向决定平仓买卖方向
        if is_long_position:
            close_side = 'SELL'  # 做多平仓 = 卖出
            logger.info(f"🔄 {symbol} 检测到做多仓位，将使用SELL订单平仓")
        else:
            close_side = 'BUY'   # 做空平仓 = 买入
            logger.info(f"🔄 {symbol} 检测到做空仓位，将使用BUY订单平仓")

        # 🔧 步骤5：执行平仓（先尝试带reduceOnly，如果失败则重试不带reduceOnly）
        try:
            is_hedge = await self.client.get_position_mode()
            ps = pos.side if is_hedge else "BOTH"
            
            # 先尝试带reduceOnly
            try:
                await self.client.place_market_close(
                    symbol=symbol,
                    side=close_side,
                    quantity=str(quantity),
                    position_side=ps,
                )
                logger.info("✅ 市价平仓成功: %s", symbol)
            except BinanceAPIError as reduce_error:
                if 'ReduceOnly Order is rejected' in str(reduce_error):
                    logger.warning(f"⚠️ {symbol} reduceOnly平仓被拒绝，尝试普通市价单")
                    # 重试：不带reduceOnly
                    await self.client.place_order(
                        symbol=symbol,
                        side=close_side,
                        positionSide=ps,
                        type="MARKET",
                        quantity=str(quantity),
                    )
                    logger.info("✅ 市价平仓成功（普通市价单）: %s", symbol)
                elif 'Margin is insufficient' in str(reduce_error):
                    # 🔧 步骤6：支持分批平仓（保证金不足时）
                    logger.error(f"❌ {symbol} 保证金不足，尝试分批平仓")
                    half_quantity = quantity / 2
                    
                    # 对分批数量也进行精度调整
                    if 'step_size' in locals() and step_size:
                        half_quantity_adjusted = round(half_quantity / step_size) * step_size
                        if step_size >= 1:
                            half_quantity_adjusted = int(half_quantity_adjusted)
                        else:
                            half_quantity_adjusted = round(half_quantity_adjusted, qty_precision)
                        half_quantity = half_quantity_adjusted
                        logger.info(f"📏 {symbol} 分批数量精度调整: {half_quantity}")
                    else:
                        half_quantity = round(half_quantity, 3)

                    # 平仓一半
                    await self.client.place_order(
                        symbol=symbol,
                        side=close_side,
                        positionSide=ps,
                        type="MARKET",
                        quantity=str(half_quantity),
                    )
                    logger.info(f"✅ {symbol} 成功平仓一半仓位 ({half_quantity})，等待再次尝试")

                    # 等待订单执行
                    await asyncio.sleep(0.5)

                    # 重新获取实际剩余持仓数量
                    try:
                        positions_info = await self.client.get_position_risk(symbol)
                        actual_position = None
                        for p in positions_info:
                            if p.symbol == symbol:
                                actual_position = p
                                break

                        if actual_position:
                            remaining_amt = float(actual_position.position_amt)
                            remaining_quantity = abs(remaining_amt)

                            # 对剩余数量也进行精度调整
                            if 'step_size' in locals() and step_size and remaining_quantity > 0:
                                remaining_adjusted = round(remaining_quantity / step_size) * step_size
                                if step_size >= 1:
                                    remaining_adjusted = int(remaining_adjusted)
                                else:
                                    remaining_adjusted = round(remaining_adjusted, qty_precision)
                                remaining_quantity = remaining_adjusted

                            logger.info(f"📊 {symbol} 重新获取剩余持仓: {remaining_quantity}")

                            if remaining_quantity > 0:
                                # 平仓剩余仓位
                                await self.client.place_order(
                                    symbol=symbol,
                                    side=close_side,
                                    positionSide=ps,
                                    type="MARKET",
                                    quantity=str(remaining_quantity),
                                )
                                logger.info(f"✅ {symbol} 成功平仓剩余仓位 ({remaining_quantity})")
                            else:
                                logger.info(f"✅ {symbol} 所有仓位已平仓完毕")
                        else:
                            logger.warning(f"⚠️ {symbol} 无法获取剩余持仓信息，可能已全部平仓")

                    except Exception as remaining_error:
                        logger.error(f"❌ {symbol} 平仓剩余仓位失败: {remaining_error}")
                        # 发送紧急通知（同时 Telegram + 邮件）
                        if self.notifier:
                            await self.notifier.send_critical_alert(
                                "平仓失败 - 需要人工干预",
                                f"{symbol} 分批平仓仍失败，请立即检查账户状态并手动平仓\n"
                                f"已平仓: {half_quantity}\n"
                                f"剩余仓位: 未知\n"
                                f"错误信息: {remaining_error}"
                            )
                else:
                    raise reduce_error
                
        except Exception as e:
            logger.error("❌ 市价平仓失败 %s: %s", symbol, e)
            # 发送紧急通知（同时 Telegram + 邮件）
            if self.notifier:
                await self.notifier.send_critical_alert(
                    "平仓完全失败 - 紧急",
                    f"{symbol} 所有平仓尝试都失败，请立即检查账户并手动平仓\n"
                    f"建仓价格: {pos.entry_price}\n"
                    f"持仓数量: {quantity}\n"
                    f"杠杆: {self.config.leverage}x\n"
                    f"最后错误: {e}"
                )

        # 取消剩余的TP/SL订单（如果还有）
        await self._cancel_tp_sl(pos)
        
        pos.closed = True
        self._clear_closed_position_store(pos)
        
        # 记录平仓摘要（包含订单取消详情）
        if cancelled_orders:
            cancelled_str = "\n".join([f"  - {co['type']}: ID {co['id']}, 价格 {co['price']}" for co in cancelled_orders])
            logger.info(f"📋 {symbol} 平仓完成，已取消订单:\n{cancelled_str}")
        else:
            logger.info(f"📋 {symbol} 平仓完成（无未成交订单）")

    async def _cancel_tp_sl(self, pos: TrackedPosition):
        """Cancel both TP and SL algo orders."""
        for label, algo_id in [("止盈", pos.tp_algo_id), ("止损", pos.sl_algo_id)]:
            if algo_id is None:
                continue
            try:
                await self.client.cancel_algo_order(pos.symbol, algo_id=algo_id)
                logger.info("🗑️ 已撤销%s单: %s", label, algo_id)
            except Exception:
                pass
    # ------------------------------------------------------------------
    # Position verification & auto re-place helpers
    # ------------------------------------------------------------------

    async def _get_exchange_position_amt(self, symbol: str) -> float:
        """Check actual position amount on exchange. 
        
        Returns signed position amount:
        - Positive = LONG position
        - Negative = SHORT position  
        - Zero = No position
        """
        try:
            positions = await self.client.get_position_risk(symbol)
            for p in positions:
                if p.symbol == symbol:
                    return float(p.position_amt)
        except Exception as e:
            logger.warning("查询持仓失败 %s: %s — 保守视为仓位存在", symbol, e)
            return 1.0  # Fail-safe: assume position exists to avoid false close
        return 0.0

    MAX_REPLACE_ATTEMPTS: int = 10

    async def _re_place_single_order(
        self, pos: TrackedPosition, order_type: str,
    ) -> None:
        """Re-place a single TP or SL order that was manually cancelled.

        Args:
            pos: The tracked position.
            order_type: "tp" or "sl".
        """
        if not pos.entry_price:
            return

        # Guard: stop retrying after too many failures to avoid exchange ban
        fail_count = pos.tp_fail_count if order_type == "tp" else pos.sl_fail_count
        label_str = "止盈" if order_type == "tp" else "止损"
        if fail_count >= self.MAX_REPLACE_ATTEMPTS:
            if fail_count == self.MAX_REPLACE_ATTEMPTS:
                # Log once at exactly the limit, then silence
                logger.error(
                    "🚫 %s %s单补挂已失败 %d 次，停止重试以防封禁 — 请手动处理!",
                    pos.symbol, label_str, fail_count,
                )
                if self.notifier:
                    await self.notifier.send(
                        f"🚫 <b>停止自动补挂</b>\n"
                        f"  {pos.symbol} {label_str}单连续失败 {fail_count} 次\n"
                        f"  请立即手动设置{label_str}!"
                    )
                # Increment past the limit so this block only fires once
                if order_type == "tp":
                    pos.tp_fail_count += 1
                else:
                    pos.sl_fail_count += 1
            return

        # ── Check if order already exists (prevent duplicate orders) ──
        try:
            algo_orders = await self.client.get_open_algo_orders(pos.symbol)
            target_type = "TAKE_PROFIT_MARKET" if order_type == "tp" else "STOP_MARKET"
            existing_orders = [o for o in algo_orders if o.order_type == target_type]
            
            if existing_orders:
                # Order already exists, update our tracking
                existing = existing_orders[0]
                if order_type == "tp":
                    if pos.tp_algo_id != existing.algo_id:
                        logger.warning(
                            "⚠️ 发现已存在的%s单: %s (algoId=%s) — 更新追踪ID",
                            label_str, pos.symbol, existing.algo_id
                        )
                        pos.tp_algo_id = existing.algo_id
                        pos.tp_fail_count = 0
                    else:
                        logger.debug("%s单已存在: %s (algoId=%s)", label_str, pos.symbol, existing.algo_id)
                else:
                    if pos.sl_algo_id != existing.algo_id:
                        logger.warning(
                            "⚠️ 发现已存在的%s单: %s (algoId=%s) — 更新追踪ID",
                            label_str, pos.symbol, existing.algo_id
                        )
                        pos.sl_algo_id = existing.algo_id
                        pos.sl_fail_count = 0
                    else:
                        logger.debug("%s单已存在: %s (algoId=%s)", label_str, pos.symbol, existing.algo_id)
                
                # If multiple orders exist, cancel extras
                if len(existing_orders) > 1:
                    logger.error(
                        "❌ 检测到重复的%s单: %s (共%d个) — 取消多余订单",
                        label_str, pos.symbol, len(existing_orders)
                    )
                    for extra in existing_orders[1:]:
                        try:
                            await self.client.cancel_algo_order(pos.symbol, algo_id=extra.algo_id)
                            logger.info("🗑️ 已取消重复的%s单: %s", label_str, extra.algo_id)
                        except Exception as e:
                            logger.warning("取消重复订单失败: %s", e)
                
                return  # Order exists, no need to create
        except Exception as e:
            logger.warning("检查已存在订单时出错: %s — 继续创建新订单", e)

        is_long = pos.side == "LONG"
        close_side = "SELL" if is_long else "BUY"

        if order_type == "tp":
            pct = Decimal(str(pos.current_tp_pct))
            if is_long:
                price = pos.entry_price * (1 + pct / 100)
            else:
                price = pos.entry_price * (1 - pct / 100)
            algo_type = "TAKE_PROFIT_MARKET"
            prefix = "tp"
            label = "止盈"
        else:
            _rc = self._rolling_cfg_for_position(pos)
            pct = Decimal(str(_rc.sl_threshold * 100 if _rc else 44.0))
            if is_long:
                price = pos.entry_price * (1 - pct / 100)
            else:
                price = pos.entry_price * (1 + pct / 100)
            algo_type = "STOP_MARKET"
            prefix = "sl"
            label = "止损"

        # Round trigger price
        try:
            price = await self._round_trigger_price(pos.symbol, price)
        except Exception as e:
            logger.error("补挂%s获取精度失败: %s — 使用 pricePrecision 兜底", label, e)
            try:
                price_prec, _ = await self.executor._get_precision(pos.symbol)
                price = self.executor._round_price(price, price_prec)
            except Exception:
                price = price.quantize(Decimal("1e-4"), rounding=ROUND_DOWN)

        try:
            is_hedge = await self.client.get_position_mode()
            ps = pos.side if is_hedge else "BOTH"

            order_prefix = uuid.uuid4().hex[:8]

            rounded_qty = await self._round_quantity(pos.symbol, pos.quantity)
            new_order = await self.client.place_algo_order(
                symbol=pos.symbol,
                side=close_side,
                positionSide=ps,
                type=algo_type,
                triggerPrice=str(price),
                quantity=rounded_qty,
                reduceOnly="true",
                priceProtect="true",
                workingType="CONTRACT_PRICE",
                clientAlgoId=f"{prefix}_{order_prefix}",
            )

            if order_type == "tp":
                pos.tp_algo_id = new_order.algo_id
                pos.tp_fail_count = 0  # reset on success
            else:
                pos.sl_algo_id = new_order.algo_id
                pos.sl_fail_count = 0  # reset on success

            logger.info(
                "✅ 自动补挂%s单: %s @ %s (algoId=%s)",
                label, pos.symbol, price, new_order.algo_id,
            )
            if self.notifier:
                await self.notifier.send(
                    f"⚠️ <b>{label}单被手动取消，已自动补挂</b>\n"
                    f"  {pos.symbol} {pos.side}\n"
                    f"  {label}: {price}"
                )
        except Exception as e:
            if order_type == "tp":
                pos.tp_fail_count += 1
                fail_count = pos.tp_fail_count
            else:
                pos.sl_fail_count += 1
                fail_count = pos.sl_fail_count
            logger.error(
                "❌ 自动补挂%s单失败 %s (第%d次): %s — 仓位可能无%s保护!",
                label, pos.symbol, fail_count, e, label,
            )
            if self.notifier:
                await self.notifier.send(
                    f"🚨 <b>严重警告</b>\n"
                    f"  {pos.symbol} {label}单补挂失败 (第{fail_count}次)\n"
                    f"  请立即手动设置{label}!"
                )

    # ------------------------------------------------------------------
    # Price rounding helper (tickSize-based)
    # ------------------------------------------------------------------

    async def _get_cached_exchange_info(self) -> None:
        """Refresh the tick_size cache if TTL has expired.

        Populates self._tick_cache[symbol] with a dict:
            {'tick_size': Decimal, 'price_precision': int,
             'step_size': Decimal, 'qty_precision': int}
        Runs once at startup and every _EXCHANGE_INFO_TTL seconds.
        """
        import time
        now = time.monotonic()
        if now - self._tick_cache_ts < self._EXCHANGE_INFO_TTL and self._tick_cache:
            return  # cache still valid

        try:
            info = await self.client.get_exchange_info()
            new_cache: dict[str, Any] = {}
            for s in info.symbols:
                entry: dict[str, Any] = {
                    "price_precision": s.price_precision,
                    "qty_precision": s.quantity_precision,
                    "tick_size": None,
                    "step_size": None,
                }
                for f in s.filters:
                    if (
                        f.filter_type.value == "PRICE_FILTER"
                        and f.tick_size is not None
                        and f.tick_size > 0
                    ):
                        entry["tick_size"] = f.tick_size
                    elif (
                        f.filter_type.value == "LOT_SIZE"
                        and f.step_size is not None
                        and f.step_size > 0
                    ):
                        entry["step_size"] = f.step_size
                new_cache[s.symbol] = entry
            self._tick_cache = new_cache
            self._tick_cache_ts = now
            logger.debug("exchange_info cache refreshed (%d symbols)", len(new_cache))
        except Exception as e:
            logger.warning("exchange_info cache refresh failed: %s — using stale cache", e)

    async def _round_trigger_price(self, symbol: str, price: Decimal) -> Decimal:
        """Round a trigger price to the symbol's tickSize from PRICE_FILTER.

        Uses a 4-hour in-memory cache to avoid per-poll full exchange_info requests.
        Falls back to pricePrecision if tickSize is not available.
        The result is always normalized to strip trailing zeros, which prevents
        Binance -1111 "Precision is over the maximum" errors.
        """
        await self._get_cached_exchange_info()

        entry = self._tick_cache.get(symbol)
        if entry:
            tick = entry.get("tick_size")
            if tick is not None:
                # Round down to nearest tick
                rounded = (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick
                tick_normalized = tick.normalize()
                _sign, _digits, tick_exp = tick_normalized.as_tuple()
                if tick_exp < 0:
                    result = rounded.quantize(Decimal(10) ** tick_exp, rounding=ROUND_DOWN)
                else:
                    result = rounded.quantize(Decimal("1"), rounding=ROUND_DOWN)
                logger.debug(
                    "_round_trigger_price %s: %s → %s (tick=%s, cached)",
                    symbol, price, result, tick,
                )
                return result
            # Fallback: cached pricePrecision
            prec = entry.get("price_precision", 8)
            rounded = self.executor._round_price(price, prec)
            logger.debug(
                "_round_trigger_price %s: %s → %s (pricePrecision=%d, cached)",
                symbol, price, rounded, prec,
            )
            return rounded

        # Last resort: use executor's precision cache
        price_prec, _ = await self.executor._get_precision(symbol)
        return self.executor._round_price(price, price_prec)

    async def _round_quantity(self, symbol: str, quantity: str) -> str:
        """Round a quantity string to the symbol's LOT_SIZE stepSize.

        Prevents Binance -1111 "Precision is over the maximum" errors
        when re-placing TP/SL orders with a raw quantity value.
        """
        await self._get_cached_exchange_info()

        entry = self._tick_cache.get(symbol)
        if entry:
            step = entry.get("step_size")
            if step is not None:
                qty = Decimal(quantity)
                rounded = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
                step_normalized = step.normalize()
                _sign, _digits, step_exp = step_normalized.as_tuple()
                if step_exp < 0:
                    result = rounded.quantize(Decimal(10) ** step_exp, rounding=ROUND_DOWN)
                else:
                    result = rounded.quantize(Decimal("1"), rounding=ROUND_DOWN)
                logger.debug(
                    "_round_quantity %s: %s → %s (step=%s)",
                    symbol, quantity, result, step,
                )
                return str(result)

        # Last resort: use executor's precision cache
        _, qty_prec = await self.executor._get_precision(symbol)
        return str(self.executor._round_qty(Decimal(quantity), qty_prec))




    async def _handle_add_position(self, pos: TrackedPosition, action):
        """Handle add-position action: place additional market order + update TP/SL."""
        from .strategy import PositionAction

        symbol = pos.symbol
        logger.info("📈 加仓执行: %s (当前仓位 %s %s)", symbol, pos.side, pos.quantity)

        try:
            # ── 1. Get current price for market order ──────────────
            ticker = await self.client.get_ticker_price(symbol)
            current_price = ticker.price

            # ── 2. Calculate add quantity (same as original or * multiplier) ──
            original_qty = Decimal(pos.quantity)
            # multiplier is stored in rolling_config; default 1.0x
            add_qty = original_qty  # 1:1 ratio

            # Round to exchange precision
            rounded_qty = await self._round_quantity(symbol, str(add_qty))

            # ── 3. Place market order (same direction) ─────────────
            is_hedge = await self.client.get_position_mode()
            ps = pos.side if is_hedge else "BOTH"
            entry_side = "SELL" if pos.side == "SHORT" else "BUY"

            order = await self.client.place_order(
                symbol=symbol,
                side=entry_side,
                positionSide=ps,
                type="MARKET",
                quantity=str(rounded_qty),
            )
            logger.info(
                "✅ 加仓市价单已成交: %s %s qty=%s @ ~%s (orderId=%s)",
                symbol, pos.side, rounded_qty, current_price, order.order_id,
            )

            # ── 4. Mark position as added ──────────────────────────
            pos.has_added_position = True
            new_total_qty = original_qty + Decimal(str(rounded_qty))
            pos.quantity = str(new_total_qty)

            # ── 5. Update TP to tp_after_add ───────────────────────
            pos.current_tp_pct = action.new_tp_pct  # tp_after_add * 100
            pos.strength = "added"
            await self._replace_tp_order(pos)

            # ── 6. Update SL with new total quantity ───────────────
            await self._replace_sl_order_for_new_qty(pos)

            # ── 7. Record trade ────────────────────────────────────
            self._record_live_trade(
                pos, event="add_position",
                entry_price=str(current_price),
            )

            # ── 8. Notify ──────────────────────────────────────────
            if self.notifier:
                await self.notifier.send(
                    f"📈 <b>加仓</b>\n"
                    f"  {symbol} {pos.side}\n"
                    f"  加仓价: {current_price}\n"
                    f"  加仓量: {rounded_qty}\n"
                    f"  总仓位: {new_total_qty}\n"
                    f"  新TP: {pos.current_tp_pct:.0f}%"
                )

        except Exception as e:
            logger.error("❌ 加仓失败: %s — %s", symbol, e, exc_info=True)
            if self.notifier:
                await self.notifier.send(f"❌ 加仓失败: {symbol}: {e}")

    async def _replace_sl_order_for_new_qty(self, pos: TrackedPosition):
        """Cancel and re-place SL order with updated quantity after add-position."""
        if not pos.entry_price or not pos.sl_algo_id:
            return
        try:
            await self.client.cancel_algo_order(pos.symbol, algo_id=pos.sl_algo_id)
            logger.info("🗑️ 旧止损单已撤销 (加仓后更新): %s", pos.symbol)
        except Exception as e:
            logger.warning("撤销旧止损单失败: %s", e)
            return

        pos.sl_algo_id = None

        try:
            actual_qty = await self._get_exchange_position_amt(pos.symbol)
            if actual_qty == 0:
                return
            is_long = actual_qty > 0
            close_side = "SELL" if is_long else "BUY"

            is_hedge = await self.client.get_position_mode()
            ps = pos.side if is_hedge else "BOTH"

            # Recalculate SL price from entry (use stored SL% from strategy)
            sl_pct = pos.current_sl_pct
            sl_mult = (
                Decimal("1") - Decimal(str(sl_pct)) / Decimal("100")
                if is_long
                else Decimal("1") + Decimal(str(sl_pct)) / Decimal("100")
            )
            new_sl_price = pos.entry_price * sl_mult
            new_sl_price = await self._round_trigger_price(pos.symbol, new_sl_price)

            rounded_qty = await self._round_quantity(pos.symbol, str(abs(actual_qty)))
            order_prefix = uuid.uuid4().hex[:8]

            sl_order = await self.client.place_algo_order(
                symbol=pos.symbol,
                side=close_side,
                positionSide=ps,
                type="STOP_MARKET",
                triggerPrice=str(new_sl_price),
                quantity=str(rounded_qty),
                reduceOnly="true",
                priceProtect="true",
                workingType="CONTRACT_PRICE",
                clientAlgoId=f"sl_{order_prefix}",
            )
            pos.sl_algo_id = sl_order.algo_id
            logger.info(
                "✅ 新止损单已挂出 (加仓后): %s SL@ %s qty=%s (algoId=%s)",
                pos.symbol, new_sl_price, rounded_qty, sl_order.algo_id,
            )
        except Exception as e:
            logger.error("❌ 加仓后重挂止损单失败: %s — %s", pos.symbol, e)

    async def _replace_tp_order(self, pos: TrackedPosition):
        """Cancel old TP and place a new one with updated tp_pct."""
        if not pos.entry_price or not pos.tp_algo_id:
            return

        old_tp_algo_id = pos.tp_algo_id

        # Cancel old TP
        try:
            await self.client.cancel_algo_order(pos.symbol, algo_id=pos.tp_algo_id)
            logger.info("🗑️ 旧止盈单已撤销: %s algoId=%s", pos.symbol, pos.tp_algo_id)
        except Exception as e:
            logger.warning("撤销旧止盈单失败: %s", e)
            return

        # Immediately clear stale algo ID so poll loop won't misdetect as triggered
        pos.tp_algo_id = None

        # Get actual position quantity and direction from exchange (in case of partial close)
        try:
            actual_qty = await self._get_exchange_position_amt(pos.symbol)
            if actual_qty == 0:
                logger.warning("⚠️ %s 实际持仓为0，跳过止盈单替换", pos.symbol)
                return
            
            # Determine actual position direction from exchange
            is_long = actual_qty > 0
            actual_side = "LONG" if is_long else "SHORT"
            
            # Update tracked quantity and side to match reality
            pos.quantity = str(abs(actual_qty))
            if pos.side != actual_side:
                logger.warning(
                    "⚠️ %s 持仓方向不一致: 记录=%s, 实际=%s, 已更正",
                    pos.symbol, pos.side, actual_side
                )
                pos.side = actual_side
            
            logger.debug("📊 %s 实际持仓: %s %s", pos.symbol, actual_side, pos.quantity)
        except Exception as e:
            logger.warning("获取实际持仓数量失败，使用记录的数量和方向: %s", e)
            # Continue with tracked quantity and side as fallback
            is_long = pos.side == "LONG"

        # Calculate new TP price (is_long already determined from actual position above)
        tp_mult = (
            Decimal("1") + Decimal(str(pos.current_tp_pct)) / Decimal("100")
            if is_long
            else Decimal("1") - Decimal(str(pos.current_tp_pct)) / Decimal("100")
        )
        new_tp_price = pos.entry_price * tp_mult

        # Round to correct tick size via PRICE_FILTER.tickSize
        try:
            new_tp_price = await self._round_trigger_price(pos.symbol, new_tp_price)
        except Exception as e:
            logger.error("获取精度失败, 无法安全计算新止盈价: %s", e)
            # Re-place old TP instead of risking an unrounded price
            await self._restore_tp_order(pos, old_tp_algo_id)
            return
        new_tp_str = str(new_tp_price)

        # Place new TP
        close_side = "SELL" if is_long else "BUY"
        try:
            is_hedge = await self.client.get_position_mode()
            ps = pos.side if is_hedge else "BOTH"

            order_prefix = uuid.uuid4().hex[:8]

            rounded_qty = await self._round_quantity(pos.symbol, pos.quantity)
            tp_order = await self.client.place_algo_order(
                symbol=pos.symbol,
                side=close_side,
                positionSide=ps,
                type="TAKE_PROFIT_MARKET",
                triggerPrice=new_tp_str,
                quantity=rounded_qty,
                reduceOnly="true",
                priceProtect="true",
                workingType="CONTRACT_PRICE",
                clientAlgoId=f"tp_{order_prefix}",
            )
            pos.tp_algo_id = tp_order.algo_id
            logger.info(
                "✅ 新止盈单已挂出: %s TP=%s%% @ %s (algoId=%s)",
                pos.symbol, pos.current_tp_pct, new_tp_str, tp_order.algo_id,
            )
            if self.notifier:
                await self.notifier.send(
                    f"📊 <b>动态 TP 调整</b>\n"
                    f"  {pos.symbol} {pos.strength}\n"
                    f"  新止盈: {new_tp_str} ({pos.current_tp_pct}%)"
                )
        except Exception as e:
            logger.error("❌ 新止盈单失败 %s: %s (尝试恢复旧止盈)", pos.symbol, e)
            # Try to restore the old TP order to avoid leaving position unprotected
            await self._restore_tp_order(pos, old_tp_algo_id)

    async def _restore_tp_order(
        self, pos: TrackedPosition, old_algo_id: int | None
    ):
        """Attempt to re-place a TP order after a failed replacement.

        Uses the *original* TP percentage (strong_tp_pct from config) so we
        don't lose all TP protection when the dynamic adjustment fails.
        """
        if not pos.entry_price:
            return

        # Get actual position quantity and direction from exchange (in case of partial close)
        try:
            actual_qty = await self._get_exchange_position_amt(pos.symbol)
            if actual_qty == 0:
                logger.warning("⚠️ %s 实际持仓为0，跳过止盈单恢复", pos.symbol)
                return
            
            # Determine actual position direction from exchange
            is_long = actual_qty > 0
            actual_side = "LONG" if is_long else "SHORT"
            
            # Update tracked quantity and side to match reality
            pos.quantity = str(abs(actual_qty))
            if pos.side != actual_side:
                logger.warning(
                    "⚠️ %s 恢复时持仓方向不一致: 记录=%s, 实际=%s, 已更正",
                    pos.symbol, pos.side, actual_side
                )
                pos.side = actual_side
            
            logger.debug("📊 %s 恢复时实际持仓: %s %s", pos.symbol, actual_side, pos.quantity)
        except Exception as e:
            logger.warning("获取实际持仓数量失败，使用记录的数量和方向: %s", e)
            # Continue with tracked quantity and side as fallback
            is_long = pos.side == "LONG"
        # Fall back to the original TP percentage from config
        _rc = self._rolling_cfg_for_position(pos)
        fallback_pct = _rc.tp_initial * 100 if _rc else 34.0
        tp_mult = (
            Decimal("1") + Decimal(str(fallback_pct)) / Decimal("100")
            if is_long
            else Decimal("1") - Decimal(str(fallback_pct)) / Decimal("100")
        )
        restore_price = pos.entry_price * tp_mult

        try:
            restore_price = await self._round_trigger_price(pos.symbol, restore_price)
        except Exception as e:
            logger.error("恢复止盈时获取精度失败: %s — 使用 pricePrecision 兜底", e)
            try:
                price_prec, _ = await self.executor._get_precision(pos.symbol)
                restore_price = self.executor._round_price(restore_price, price_prec)
            except Exception:
                restore_price = restore_price.quantize(Decimal("1e-4"), rounding=ROUND_DOWN)

        close_side = "SELL" if is_long else "BUY"
        try:
            is_hedge = await self.client.get_position_mode()
            ps = pos.side if is_hedge else "BOTH"

            order_prefix = uuid.uuid4().hex[:8]

            rounded_qty = await self._round_quantity(pos.symbol, pos.quantity)
            tp_order = await self.client.place_algo_order(
                symbol=pos.symbol,
                side=close_side,
                positionSide=ps,
                type="TAKE_PROFIT_MARKET",
                triggerPrice=str(restore_price),
                quantity=rounded_qty,
                reduceOnly="true",
                priceProtect="true",
                workingType="CONTRACT_PRICE",
                clientAlgoId=f"tp_{order_prefix}",
            )
            pos.tp_algo_id = tp_order.algo_id
            pos.current_tp_pct = fallback_pct
            logger.info(
                "🔄 恢复止盈单: %s TP=%s%% @ %s (algoId=%s)",
                pos.symbol, fallback_pct, restore_price, tp_order.algo_id,
            )
        except Exception as e2:
            logger.error(
                "❌ 恢复止盈单也失败 %s: %s — 仓位无止盈保护!",
                pos.symbol, e2,
            )
            if self.notifier:
                await self.notifier.send(
                    f"🚨 <b>严重警告</b>\n"
                    f"  {pos.symbol} 止盈单替换失败且无法恢复\n"
                    f"  请手动检查并设置止盈!"
                )


    def _record_live_trade(self, pos: TrackedPosition, event: str, **kwargs):
        """Record a live trade event to the store."""
        if not self.store:
            return
        try:
            from .store import LiveTrade
            trade = LiveTrade(
                symbol=pos.symbol,
                side=pos.side,
                event=event,
                entry_price=kwargs.get("entry_price", str(pos.entry_price or "")),
                exit_price=kwargs.get("exit_price", ""),
                quantity=pos.quantity,
                margin_usdt=kwargs.get("margin_usdt", ""),
                leverage=self.config.leverage,
                pnl_usdt=kwargs.get("pnl_usdt", ""),
                pnl_pct=kwargs.get("pnl_pct", ""),
                order_id=kwargs.get("order_id", ""),
                algo_id=kwargs.get("algo_id", ""),
            )
            self.store.save_live_trade(trade)
            logger.debug("Recorded live trade: %s %s %s", pos.symbol, event, pos.side)
        except Exception as e:
            logger.warning("Failed to record live trade: %s", e)

    @property
    def tracked_count(self) -> int:
        return sum(1 for p in self._positions.values() if not p.closed)

    # ------------------------------------------------------------------
    # WebSocket event handler
    # ------------------------------------------------------------------

    async def handle_order_update(self, event: dict):
        """Handle ORDER_TRADE_UPDATE from WebSocket user data stream.

        Binance event fields used:
          o.s   = symbol
          o.i   = orderId
          o.c   = clientOrderId
          o.S   = side (BUY/SELL)
          o.o   = order type (may differ from original for conditional)
          o.ot  = original order type (TAKE_PROFIT_MARKET, STOP_MARKET, etc.)
          o.ps  = position side (LONG/SHORT)
          o.x   = execution type (NEW, TRADE, CANCELED, EXPIRED, CALCULATED)
          o.X   = order status (NEW, PARTIALLY_FILLED, FILLED, CANCELED)
          o.ap  = average price
          o.rp  = realized PnL for this trade
          o.n   = commission amount
          o.N   = commission asset
        """
        order = event.get("o", {})
        symbol = order.get("s", "")
        exec_type = order.get("x", "")       # execution type: NEW/TRADE/CANCELED
        order_status = order.get("X", "")     # order status: FILLED/PARTIALLY_FILLED
        order_id = order.get("i", 0)          # orderId
        orig_order_type = order.get("ot", "")  # original order type (reliable for TP/SL)
        avg_price = order.get("ap", "0")      # average fill price
        realized_pnl = order.get("rp", "0")   # realized PnL
        client_order_id = order.get("c", "")  # custom client order ID
        position_side = order.get("ps", "")   # LONG or SHORT

        pos = self._positions.get(symbol)
        if not pos:
            return

        # ── Entry fill ──────────────────────────────────────────
        if (
            not pos.entry_filled
            and order_status == "FILLED"
            and order_id == pos.entry_order_id
        ):
            pos.entry_filled = True
            pos.entry_price = Decimal(avg_price) if avg_price != "0" else None
            pos.entry_fill_time = datetime.now(timezone.utc)
            logger.info(
                "⚡ WS 入场成交: %s %s @ %s (即时通知)",
                symbol, pos.side, avg_price,
            )
            if self.notifier:
                await self.notifier.notify_entry_filled(symbol, pos.side, avg_price)

            self._record_live_trade(
                pos, "entry",
                entry_price=avg_price,
                order_id=str(order_id),
            )

            # Place deferred TP/SL
            if not pos.tp_sl_placed:
                await self._place_deferred_tp_sl(pos)

        # ── TP/SL triggered ──────────────────────────────────────
        elif pos.entry_filled and order_status == "FILLED":
            # Use `ot` (original order type) to detect TP/SL — per Binance spec
            is_tp = (
                "TAKE_PROFIT" in orig_order_type
                or "tp_" in client_order_id.lower()
            )
            is_sl = (
                orig_order_type in ("STOP_MARKET", "STOP")
                or "sl_" in client_order_id.lower()
            )

            if is_tp and not pos.tp_triggered:
                pos.tp_triggered = True
                pos.closed = True
                self._clear_closed_position_store(pos)
                logger.info(
                    "⚡ WS 止盈触发: %s %s rp=%s (即时通知)",
                    symbol, pos.side, realized_pnl,
                )
                if self.notifier:
                    await self.notifier.notify_tp_triggered(
                        symbol, pos.side,
                        price=avg_price,
                        pnl_usdt=realized_pnl,
                    )
                self._record_live_trade(
                    pos, "tp",
                    exit_price=avg_price,
                    pnl_usdt=realized_pnl,
                )
                # Cancel SL if exists
                if pos.sl_algo_id:
                    try:
                        await self.client.cancel_algo_order(symbol, pos.sl_algo_id)
                    except Exception:
                        pass

            elif is_sl and not pos.sl_triggered:
                pos.sl_triggered = True
                pos.closed = True
                self._clear_closed_position_store(pos)
                logger.info(
                    "⚡ WS 止损触发: %s %s rp=%s (即时通知)",
                    symbol, pos.side, realized_pnl,
                )
                if self.notifier:
                    await self.notifier.notify_sl_triggered(
                        symbol, pos.side,
                        price=avg_price,
                        pnl_usdt=realized_pnl,
                    )
                # S: notify scanner to block same-day re-entry
                if self.on_sl_triggered:
                    self.on_sl_triggered(symbol, pos.strategy_id or "")
                self._record_live_trade(
                    pos, "sl",
                    exit_price=avg_price,
                    pnl_usdt=realized_pnl,
                )
                # Cancel TP if exists
                if pos.tp_algo_id:
                    try:
                        await self.client.cancel_algo_order(symbol, pos.tp_algo_id)
                    except Exception:
                        pass

        # ── Order expired/canceled ───────────────────────────────
        elif exec_type in ("EXPIRED", "CANCELED"):
            # Check if entry expired (not filled)
            if not pos.entry_filled and order_id == pos.entry_order_id:
                logger.warning(
                    "⚠️ WS 入场单 %s/%s 已%s",
                    symbol, order_id, exec_type,
                )

    async def handle_account_update(self, event: dict) -> None:
        """Handle ACCOUNT_UPDATE from WebSocket user data stream (WS-first).

        Updates in-memory position state in real-time without REST polling.
        Binance ACCOUNT_UPDATE positions array (field 'P') keys:
          s  = symbol
          pa = position amount (positive=LONG, negative=SHORT, 0=closed)
          ep = entry price
          up = unrealized PnL
          ps = position side (LONG/SHORT/BOTH)
        """
        positions_data = event.get("a", {}).get("P", [])
        for p in positions_data:
            symbol = p.get("s", "")
            pos = self._positions.get(symbol)
            if not pos:
                continue

            amt_str = p.get("pa", "0")
            ep_str  = p.get("ep", "0")

            try:
                amt = float(amt_str)
            except (ValueError, TypeError):
                continue

            if amt == 0:
                # Position fully closed — mark as closed if not already
                if not pos.closed and pos.entry_filled:
                    logger.info(
                        "⚡ WS 检测到仓位已关闭: %s（通过 ACCOUNT_UPDATE）",
                        symbol,
                    )
                    pos.closed = True
                    self._clear_closed_position_store(pos)
            else:
                # Update live entry price if changed (e.g. after partial fill)
                try:
                    new_ep = Decimal(ep_str)
                    if new_ep > 0 and new_ep != pos.entry_price:
                        logger.debug(
                            "⚡ WS 更新入场价: %s %s → %s",
                            symbol, pos.entry_price, new_ep,
                        )
                        pos.entry_price = new_ep
                except Exception:
                    pass

        if positions_data:
            logger.debug("⚡ WS ACCOUNT_UPDATE 处理完成 (%d 仓位)", len(positions_data))
