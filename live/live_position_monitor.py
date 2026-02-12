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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional

from .binance_client import BinanceFuturesClient, BinanceAPIError
from .live_config import LiveTradingConfig

logger = logging.getLogger(__name__)


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
    evaluated_2h: bool = False
    evaluated_12h: bool = False
    strength: str = "unknown"  # strong / medium / weak


class LivePositionMonitor:
    """Monitor live positions with deferred TP/SL placement.

    TP/SL algo orders are placed ONLY after the entry order fills,
    matching Binance App behavior and preventing -2021 errors.
    """

    def __init__(
        self,
        client: BinanceFuturesClient,
        executor,  # LiveOrderExecutor (avoids circular import)
        config: Optional[LiveTradingConfig] = None,
        poll_interval: int = 30,
        notifier=None,  # TelegramNotifier (optional)
        store=None,     # TradeStore (optional, for live trade recording)
    ):
        self.client = client
        self.executor = executor
        self.config = config or LiveTradingConfig()
        self.poll_interval = poll_interval
        self.notifier = notifier
        self.store = store
        self._positions: dict[str, TrackedPosition] = {}  # symbol → position
        self._running = False

    def track(
        self,
        symbol: str,
        entry_order_id: int,
        side: str,
        quantity: str,
        deferred_tp_sl: dict[str, str],
    ):
        """Start tracking a new position (entry pending, TP/SL deferred)."""
        pos = TrackedPosition(
            symbol=symbol,
            entry_order_id=entry_order_id,
            side=side,
            quantity=quantity,
            deferred_tp_sl=deferred_tp_sl,
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
            self._positions[symbol] = tracked
            recovered += 1

            tp_info = f"tp={tp_algo_id}" if tp_algo_id else "无TP"
            sl_info = f"sl={sl_algo_id}" if sl_algo_id else "无SL"
            logger.info(
                "🔄 恢复持仓: %s %s qty=%s entry=%s [%s, %s]",
                symbol, side, qty, pos_risk.entry_price, tp_info, sl_info,
            )

        if recovered > 0:
            logger.info("🔄 共恢复 %d 个持仓", recovered)
            if self.notifier:
                await self.notifier.send(
                    f"🔄 <b>断线恢复</b>\n  已恢复 {recovered} 个持仓监控"
                )
        else:
            logger.info("🔄 无持仓需要恢复")

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

        # Skip TP/SL checks if entry not yet filled or TP/SL not yet placed
        if not pos.entry_filled or not pos.tp_sl_placed:
            return

        # ── 1.5. Dynamic TP evaluation (2h / 12h) ────────────────────
        await self._update_dynamic_tp(pos, now)

        # ── 2. Check TP/SL algo order status ──────────────────────────
        try:
            algo_orders = await self.client.get_open_algo_orders(pos.symbol)
            algo_ids = {o.algo_id for o in algo_orders}

            tp_still_open = pos.tp_algo_id is not None and pos.tp_algo_id in algo_ids
            sl_still_open = pos.sl_algo_id is not None and pos.sl_algo_id in algo_ids

            if pos.tp_algo_id and not tp_still_open and not pos.tp_triggered:
                # TP was triggered
                pos.tp_triggered = True
                logger.info("🎯 止盈触发: %s (algoId=%s)", pos.symbol, pos.tp_algo_id)
                if self.notifier:
                    await self.notifier.notify_tp_triggered(pos.symbol, pos.side)
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

            elif pos.sl_algo_id and not sl_still_open and not pos.sl_triggered:
                # SL was triggered
                pos.sl_triggered = True
                logger.info("🛑 止损触发: %s (algoId=%s)", pos.symbol, pos.sl_algo_id)
                if self.notifier:
                    await self.notifier.notify_sl_triggered(pos.symbol, pos.side)
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

        except Exception as e:
            logger.debug("Algo order check failed: %s", e)

        # ── 3. Max hold time enforcement ──────────────────────────────
        if not pos.closed and pos.entry_filled:
            hold_hours = (now - pos.created_at).total_seconds() / 3600
            if hold_hours >= self.config.max_hold_hours:
                logger.warning(
                    "⏰ 持仓超时 (%dh): %s — 市价平仓",
                    self.config.max_hold_hours, pos.symbol,
                )
                if self.notifier:
                    await self.notifier.notify_timeout_close(
                        pos.symbol, self.config.max_hold_hours,
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
        """Force close a position with a market order and cancel TP/SL."""
        close_side = "SELL" if pos.side == "LONG" else "BUY"
        try:
            is_hedge = await self.client.get_position_mode()
            ps = pos.side if is_hedge else "BOTH"
            await self.client.place_market_close(
                symbol=pos.symbol,
                side=close_side,
                quantity=pos.quantity,
                position_side=ps,
            )
            logger.info("✅ 市价平仓成功: %s", pos.symbol)
        except Exception as e:
            logger.error("❌ 市价平仓失败 %s: %s", pos.symbol, e)

        await self._cancel_tp_sl(pos)
        pos.closed = True

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
    # Price rounding helper (tickSize-based)
    # ------------------------------------------------------------------

    async def _round_trigger_price(self, symbol: str, price: Decimal) -> Decimal:
        """Round a trigger price to the symbol's tickSize from PRICE_FILTER.

        Falls back to pricePrecision if tickSize is not available.
        """
        info = await self.client.get_exchange_info()
        for s in info.symbols:
            if s.symbol == symbol:
                # Prefer tickSize from PRICE_FILTER (definitive constraint)
                for f in s.filters:
                    if f.filter_type.value == "PRICE_FILTER" and f.tick_size:
                        tick = f.tick_size
                        # Round down to nearest tick
                        return (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick
                # Fallback: use pricePrecision
                return self.executor._round_price(price, s.price_precision)
        # Last resort: use executor's precision cache
        price_prec, _ = await self.executor._get_precision(symbol)
        return self.executor._round_price(price, price_prec)

    # ------------------------------------------------------------------
    # Dynamic TP (V2 — strength evaluation at 2h / 12h checkpoints)
    # ------------------------------------------------------------------

    async def _update_dynamic_tp(self, pos: TrackedPosition, now: datetime):
        """Evaluate coin strength at 2h/12h and adjust TP if needed.

        V2 dynamic TP evaluation:
          - 2h:  strong → 33%, medium → 21%
          - 12h: strong → 33%, weak → 10%
        """
        if not pos.entry_fill_time or not pos.entry_price:
            return

        hold_hours = (now - pos.entry_fill_time).total_seconds() / 3600

        if hold_hours < 2.0:
            return

        old_tp_pct = pos.current_tp_pct

        # 2h evaluation (run once)
        if not pos.evaluated_2h and hold_hours >= 2.0:
            pos.evaluated_2h = True
            pct_drop = await self._calc_5m_drop_ratio(
                pos.symbol,
                pos.entry_fill_time,
                pos.entry_fill_time + timedelta(hours=2),
                pos.entry_price,
                self.config.strength_eval_2h_growth,
            )
            if pct_drop is not None and pct_drop >= self.config.strength_eval_2h_ratio:
                pos.strength = "strong"
                pos.current_tp_pct = self.config.strong_tp_pct
            else:
                pos.strength = "medium"
                pos.current_tp_pct = self.config.medium_tp_pct
            logger.info(
                "📊 2h 评估: %s → %s (TP %s%% → %s%%)",
                pos.symbol, pos.strength, old_tp_pct, pos.current_tp_pct,
            )

        # 12h evaluation (run once)
        if not pos.evaluated_12h and hold_hours >= 12.0:
            pos.evaluated_12h = True
            pct_drop = await self._calc_5m_drop_ratio(
                pos.symbol,
                pos.entry_fill_time,
                pos.entry_fill_time + timedelta(hours=12),
                pos.entry_price,
                self.config.strength_eval_12h_growth,
            )
            if pct_drop is not None and pct_drop >= self.config.strength_eval_12h_ratio:
                pos.strength = "strong"
                pos.current_tp_pct = self.config.strong_tp_pct
            else:
                pos.strength = "weak"
                pos.current_tp_pct = self.config.weak_tp_pct
            logger.info(
                "📊 12h 评估: %s → %s (TP %s%% → %s%%)",
                pos.symbol, pos.strength, old_tp_pct, pos.current_tp_pct,
            )

        # Replace TP order if changed
        if pos.current_tp_pct != old_tp_pct:
            await self._replace_tp_order(pos)

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

        # Calculate new TP price
        is_long = pos.side == "LONG"
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

            import uuid
            order_prefix = uuid.uuid4().hex[:8]

            tp_order = await self.client.place_algo_order(
                symbol=pos.symbol,
                side=close_side,
                positionSide=ps,
                type="TAKE_PROFIT_MARKET",
                triggerPrice=new_tp_str,
                quantity=pos.quantity,
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

        is_long = pos.side == "LONG"
        # Fall back to the original TP percentage from config
        fallback_pct = self.config.strong_tp_pct
        tp_mult = (
            Decimal("1") + Decimal(str(fallback_pct)) / Decimal("100")
            if is_long
            else Decimal("1") - Decimal(str(fallback_pct)) / Decimal("100")
        )
        restore_price = pos.entry_price * tp_mult

        try:
            restore_price = await self._round_trigger_price(pos.symbol, restore_price)
        except Exception as e:
            logger.error("恢复止盈时获取精度失败: %s — 使用 8 位小数兜底", e)
            restore_price = restore_price.quantize(Decimal("1e-8"), rounding=ROUND_DOWN)

        close_side = "SELL" if is_long else "BUY"
        try:
            is_hedge = await self.client.get_position_mode()
            ps = pos.side if is_hedge else "BOTH"

            import uuid
            order_prefix = uuid.uuid4().hex[:8]

            tp_order = await self.client.place_algo_order(
                symbol=pos.symbol,
                side=close_side,
                positionSide=ps,
                type="TAKE_PROFIT_MARKET",
                triggerPrice=str(restore_price),
                quantity=pos.quantity,
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

    async def _calc_5m_drop_ratio(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        entry_price: Decimal,
        threshold: float,
    ) -> float | None:
        """Compute fraction of 5m candles whose close dropped > threshold from entry.

        Computes the fraction of 5m candles that dropped beyond threshold.
        """
        try:
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)

            klines = await self.client.get_klines(
                symbol=symbol,
                interval="5m",
                start_time=start_ms,
                end_time=end_ms,
                limit=1500,
            )

            if not klines or len(klines) < 2:
                return None

            ep = float(entry_price)
            drops = sum(1 for k in klines if (float(k.close) - ep) / ep < -threshold)
            return drops / len(klines)

        except Exception as e:
            logger.debug("5m drop ratio error for %s: %s", symbol, e)
            return None

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
                logger.info(
                    "⚡ WS 止盈触发: %s %s rp=%s (即时通知)",
                    symbol, pos.side, realized_pnl,
                )
                if self.notifier:
                    await self.notifier.notify_tp_triggered(symbol, pos.side)
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
                logger.info(
                    "⚡ WS 止损触发: %s %s rp=%s (即时通知)",
                    symbol, pos.side, realized_pnl,
                )
                if self.notifier:
                    await self.notifier.notify_sl_triggered(symbol, pos.side)
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
