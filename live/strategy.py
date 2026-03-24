"""Pluggable trading strategy interface.

Defines a Strategy ABC used by LiveTrader.
The default (and only) implementation is RollingLiveStrategy in
rolling_live_strategy.py.

Decision points:
  1. create_scanner()        — how to find trading signals
  2. filter_entry()          — pre-entry risk checks + entry parameters
  3. evaluate_position()     — dynamic TP, early stops, max hold
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
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
    side: str = "SHORT"         # "SHORT" or "LONG" (position side)
    tp_pct: float = 34.0
    sl_pct: float = 44.0


@dataclass
class PositionAction:
    """Action to take on a monitored position.

    action values:
      "hold"         — keep position as-is
      "close"        — force close (market order)
      "adjust_tp"    — replace TP order with new_tp_pct
      "add_position" — add to existing position (加仓)
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
