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
from datetime import datetime, timezone
from decimal import Decimal
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
    tp_sl_placed: bool = False
    tp_algo_id: Optional[int] = None
    sl_algo_id: Optional[int] = None
    tp_triggered: bool = False
    sl_triggered: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed: bool = False


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
        store=None,     # PaperStore (optional, for live trade recording)
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

    def _record_live_trade(self, pos: TrackedPosition, event: str, **kwargs):
        """Record a live trade event to the store."""
        if not self.store:
            return
        try:
            from .paper_store import LiveTrade
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
