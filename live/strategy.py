"""Pluggable trading strategy interface.

Defines a Strategy ABC and the default SurgeShortStrategy implementation.
To create a new strategy, subclass Strategy and implement all abstract methods,
then pass it to LiveTrader.

Decision points:
  1. create_scanner()        — how to find trading signals
  2. filter_entry()          — pre-entry risk checks + entry parameters
  3. evaluate_position()     — dynamic TP, early stops, max hold
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .binance_client import BinanceFuturesClient
    from .live_config import LiveTradingConfig
    from .live_position_monitor import TrackedPosition

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────


@dataclass
class EntryDecision:
    """Result of a strategy's entry filter."""

    should_enter: bool
    reject_reason: str = ""
    side: str = "SELL"          # SELL = short, BUY = long
    tp_pct: float = 33.0
    sl_pct: float = 18.0


@dataclass
class PositionAction:
    """Action to take on a monitored position.

    action values:
      "hold"       — keep position as-is
      "close"      — force close (market order)
      "adjust_tp"  — replace TP order with new_tp_pct
    """

    action: str = "hold"
    reason: str = ""
    new_tp_pct: float = 0
    new_strength: str = ""


# ──────────────────────────────────────────────────────────────────────
# Abstract Strategy
# ──────────────────────────────────────────────────────────────────────


class Strategy(ABC):
    """Abstract trading strategy interface.

    Implement this to create a new trading strategy. The LiveTrader and
    LivePositionMonitor delegate all strategy-specific decisions to this
    interface. Infrastructure (order placement, position tracking,
    WebSocket, Telegram) stays in the framework.

    Lifecycle::

        trader = LiveTrader(strategy=MyStrategy())
        # startup:
        scanner = strategy.create_scanner(config, queue, client, console)
        # on signal:
        decision = await strategy.filter_entry(client, signal, price, now)
        # every monitor cycle:
        action = await strategy.evaluate_position(client, pos, config, now)
    """

    @abstractmethod
    def create_scanner(
        self,
        config: LiveTradingConfig,
        signal_queue: Any,
        client: BinanceFuturesClient,
        console: Any,
    ) -> Any:
        """Create and return a signal scanner with a ``run_forever()`` coroutine."""

    @abstractmethod
    async def filter_entry(
        self,
        client: BinanceFuturesClient,
        signal: Any,
        entry_price: Decimal,
        signal_price: Decimal,
        now: datetime,
        config: LiveTradingConfig,
    ) -> EntryDecision:
        """Decide whether to enter a position for this signal.

        Called after infrastructure-level guards (max positions, duplicate
        symbol check). Should run strategy-specific risk filters and return
        entry parameters (side, TP%, SL%).
        """

    @abstractmethod
    async def evaluate_position(
        self,
        client: BinanceFuturesClient,
        pos: TrackedPosition,
        config: LiveTradingConfig,
        now: datetime,
    ) -> PositionAction:
        """Evaluate an open position and decide what to do.

        Called every monitor cycle for each tracked position.
        Return PositionAction("hold"), ("close", reason), or
        ("adjust_tp", new_tp_pct=X, new_strength=Y).
        """


# ──────────────────────────────────────────────────────────────────────
# SurgeShortStrategy — default implementation
# ──────────────────────────────────────────────────────────────────────


class SurgeShortStrategy(Strategy):
    """Surge Short V2 strategy.

    Signal:  sell volume surge detection (LiveSurgeScanner)
    Entry:   SHORT with risk filter pipeline
    TP/SL:   dynamic TP based on coin strength at 2h/12h checkpoints
    Exit:    max hold time, early stops
    """

    def __init__(self) -> None:
        self._risk_filters: Optional[object] = None

    # ── Scanner ────────────────────────────────────────────────────

    def create_scanner(self, config, signal_queue, client, console):
        from .live_scanner import LiveSurgeScanner

        return LiveSurgeScanner(
            config=config,
            signal_queue=signal_queue,
            client=client,
            console=console,
        )

    # ── Entry Filter ──────────────────────────────────────────────

    async def filter_entry(
        self,
        client: BinanceFuturesClient,
        signal: Any,
        entry_price: Decimal,
        signal_price: Decimal,
        now: datetime,
        config: LiveTradingConfig,
    ) -> EntryDecision:
        """Run risk filters, return SHORT entry with TP/SL from config."""

        # Lazy-init risk filters
        if config.enable_risk_filters and self._risk_filters is None:
            from .risk_filters import RiskFilters
            self._risk_filters = RiskFilters(client)

        # Run risk filter pipeline
        if self._risk_filters:
            try:
                result = await self._risk_filters.check_all(
                    signal.symbol, now, entry_price, signal_price,
                )
                if not result.should_trade:
                    return EntryDecision(
                        should_enter=False,
                        reject_reason=result.reason,
                    )
            except Exception as e:
                logger.warning(
                    "Risk filter error for %s (fail-open): %s",
                    signal.symbol, e,
                )

        return EntryDecision(
            should_enter=True,
            side="SELL",  # SHORT strategy
            tp_pct=config.strong_tp_pct,
            sl_pct=config.stop_loss_pct,
        )

    # ── Position Evaluation ───────────────────────────────────────

    async def evaluate_position(
        self,
        client: BinanceFuturesClient,
        pos: TrackedPosition,
        config: LiveTradingConfig,
        now: datetime,
    ) -> PositionAction:
        """Evaluate position: dynamic TP at 2h/12h, max hold time."""

        if not pos.entry_fill_time or not pos.entry_price:
            return PositionAction("hold")

        hold_hours = (now - pos.entry_fill_time).total_seconds() / 3600

        # ── Max hold time ────────────────────────────────────────
        if hold_hours >= config.max_hold_hours:
            return PositionAction("close", reason="max_hold_time")

        # ── Dynamic TP: 2h checkpoint ────────────────────────────
        if not pos.evaluated_2h and hold_hours >= 2.0:
            pos.evaluated_2h = True
            pct_drop = await self._calc_5m_drop_ratio(
                client, pos.symbol,
                pos.entry_fill_time,
                pos.entry_fill_time + timedelta(hours=2),
                pos.entry_price,
                config.strength_eval_2h_growth,
            )
            old_tp = pos.current_tp_pct
            if pct_drop is not None and pct_drop >= config.strength_eval_2h_ratio:
                new_strength = "strong"
                new_tp = config.strong_tp_pct
            else:
                new_strength = "medium"
                new_tp = config.medium_tp_pct

            logger.info(
                "📊 2h 评估: %s → %s (TP %s%% → %s%%)",
                pos.symbol, new_strength, old_tp, new_tp,
            )
            if new_tp != old_tp:
                return PositionAction(
                    "adjust_tp", new_tp_pct=new_tp, new_strength=new_strength,
                )
            # TP unchanged but update strength
            pos.strength = new_strength

        # ── Dynamic TP: 12h checkpoint ───────────────────────────
        if not pos.evaluated_12h and hold_hours >= 12.0:
            pos.evaluated_12h = True
            pct_drop = await self._calc_5m_drop_ratio(
                client, pos.symbol,
                pos.entry_fill_time,
                pos.entry_fill_time + timedelta(hours=12),
                pos.entry_price,
                config.strength_eval_12h_growth,
            )
            old_tp = pos.current_tp_pct
            if pct_drop is not None and pct_drop >= config.strength_eval_12h_ratio:
                new_strength = "strong"
                new_tp = config.strong_tp_pct
            else:
                new_strength = "weak"
                new_tp = config.weak_tp_pct

            logger.info(
                "📊 12h 评估: %s → %s (TP %s%% → %s%%)",
                pos.symbol, new_strength, old_tp, new_tp,
            )
            if new_tp != old_tp:
                return PositionAction(
                    "adjust_tp", new_tp_pct=new_tp, new_strength=new_strength,
                )
            pos.strength = new_strength

        return PositionAction("hold")

    # ── Helper ────────────────────────────────────────────────────

    @staticmethod
    async def _calc_5m_drop_ratio(
        client: BinanceFuturesClient,
        symbol: str,
        start: datetime,
        end: datetime,
        entry_price: Decimal,
        threshold: float,
    ) -> float | None:
        """Compute fraction of 5m candles whose close dropped > threshold."""
        try:
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)

            klines = await client.get_klines(
                symbol=symbol,
                interval="5m",
                start_time=start_ms,
                end_time=end_ms,
                limit=1500,
            )

            if not klines or len(klines) < 2:
                return None

            ep = float(entry_price)
            drops = sum(
                1 for k in klines if (float(k.close) - ep) / ep < -threshold
            )
            return drops / len(klines)

        except Exception as e:
            logger.debug("5m drop ratio error for %s: %s", symbol, e)
            return None
