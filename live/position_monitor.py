"""Position monitor — real-time exit logic for open paper positions.

Replicates SurgeShortEngine._check_positions() V2 with all exit conditions:
  - Stop loss (18%)
  - Dynamic take profit (33%/21%/10% based on coin strength)
  - Max hold time (72h)
  - 2h / 12h early stop
  - Dynamic TP evaluation (_update_dynamic_tp_v2 + _calc_5m_drop_ratio)
  - 24h weak-exit → Observing state machine
  - Observing → Virtual Tracking / Take Profit
  - 24h max-gain exit

Data sources: BinanceFuturesClient for real-time prices and 5m klines.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from rich.console import Console

from .models import utc_now
from .live_config import LiveTradingConfig
from .paper_executor import PaperOrderExecutor
from .paper_store import PaperPosition, PaperStore
from .binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)


class PositionMonitor:
    """Real-time position monitor with V2 exit logic.

    Polls all open positions every monitor_interval_seconds (default: 30s).
    Exit conditions are identical to SurgeShortEngine._check_positions() V2.
    """

    def __init__(
        self,
        config: LiveTradingConfig,
        store: PaperStore,
        executor: PaperOrderExecutor,
        client: Optional[BinanceFuturesClient] = None,
        console: Optional[Console] = None,
    ):
        self.config = config
        self.store = store
        self.executor = executor
        self.client = client or BinanceFuturesClient()
        self.console = console or Console()
        self.running = False

    # ------------------------------------------------------------------
    # Main Loop
    # ------------------------------------------------------------------

    async def run_forever(self):
        """Check all open positions every monitor_interval_seconds."""
        self.running = True
        logger.info("PositionMonitor started (interval=%ds)", self.config.monitor_interval_seconds)

        while self.running:
            try:
                positions = self.store.get_open_positions()
                if positions:
                    await self._check_all_positions(positions)
            except Exception as e:
                logger.error("Monitor cycle error: %s", e, exc_info=True)

            await asyncio.sleep(self.config.monitor_interval_seconds)

    async def stop(self):
        self.running = False

    # ------------------------------------------------------------------
    # Position Checking (mirrors _check_positions V2)
    # ------------------------------------------------------------------

    async def _check_all_positions(self, positions: list[PaperPosition]):
        """Check all open positions for exit conditions."""
        now = utc_now()

        for pos in positions:
            try:
                await self._check_single_position(pos, now)
            except Exception as e:
                logger.error("Error checking %s: %s", pos.symbol, e)

    async def _check_single_position(self, pos: PaperPosition, now: datetime):
        """Check one position for exit conditions."""
        # Get current price
        try:
            ticker = await self.client.get_ticker_price(pos.symbol)
            current_price = ticker.price  # Decimal
        except Exception as e:
            logger.debug("Cannot get price for %s: %s", pos.symbol, e)
            return

        entry_price = Decimal(pos.entry_price)
        entry_time = datetime.fromisoformat(pos.entry_time)
        hold_hours = (now - entry_time).total_seconds() / 3600

        # Update price tracking
        max_price = Decimal(pos.max_price)
        min_price = Decimal(pos.min_price)
        price_updated = False
        if current_price > max_price:
            pos.max_price = str(current_price)
            price_updated = True
        if current_price < min_price:
            pos.min_price = str(current_price)
            price_updated = True

        # ── Observing state machine ──────────────────────────────────
        if pos.status == "observing":
            await self._handle_observing(pos, current_price, now, hold_hours)
            return

        # ── Virtual tracking (no action, just track) ─────────────────
        if pos.status == "virtual_tracking":
            # Max hold → close
            if hold_hours >= self.config.max_hold_hours:
                await self.executor.execute_exit(pos, current_price, now, "virtual_timeout")
            return

        # ── Max hold time ────────────────────────────────────────────
        if hold_hours >= self.config.max_hold_hours:
            await self.executor.execute_exit(pos, current_price, now, "max_hold_time")
            return

        # ── Stop loss (price goes UP for SHORT) ─────────────────────
        sl_price = entry_price * (1 + Decimal(str(self.config.stop_loss_pct)) / 100)
        if current_price >= sl_price:
            await self.executor.execute_exit(pos, sl_price, now, "stop_loss")
            return

        # ── 2h early stop ────────────────────────────────────────────
        if (
            self.config.enable_2h_early_stop
            and not pos.checked_2h_early_stop
            and hold_hours >= 2.0
        ):
            pos.checked_2h_early_stop = True
            change = (current_price - entry_price) / entry_price
            if change > Decimal(str(self.config.early_stop_2h_threshold)):
                await self.executor.execute_exit(pos, current_price, now, "early_stop_loss")
                return

        # ── 12h early stop ───────────────────────────────────────────
        if (
            self.config.enable_12h_early_stop
            and not pos.checked_12h_early_stop
            and hold_hours >= 12.0
        ):
            pos.checked_12h_early_stop = True
            change = (current_price - entry_price) / entry_price
            if change > Decimal(str(self.config.early_stop_12h_threshold)):
                await self.executor.execute_exit(pos, current_price, now, "early_stop_loss_12h")
                return

        # ── Dynamic TP update (2h / 12h strength evaluation) ─────────
        await self._update_dynamic_tp(pos, now, hold_hours, entry_price)

        # ── Take profit (price goes DOWN for SHORT) ──────────────────
        tp_pct = Decimal(str(pos.tp_pct))
        tp_price = entry_price * (1 - tp_pct / 100)
        if current_price <= tp_price:
            await self.executor.execute_exit(pos, tp_price, now, "take_profit")
            return

        # ── 24h weak-exit → Observing ────────────────────────────────
        if (
            self.config.enable_weak_24h_exit
            and pos.status == "normal"
            and 24 <= hold_hours < 25
        ):
            return_24h = (current_price - entry_price) / entry_price
            if return_24h < Decimal(str(self.config.weak_24h_threshold)):
                pos.status = "observing"
                pos.observing_since = now.isoformat()
                pos.observing_entry_price = str(entry_price)
                if not pos.capital_already_returned:
                    margin = Decimal(pos.margin)
                    self.executor._capital += margin
                    self.executor._save_capital()
                    pos.capital_already_returned = True
                self.store.save_position(pos)
                self.console.print(
                    f"  [yellow]OBSERVE[/yellow] {pos.symbol} "
                    f"24h return {float(return_24h)*100:.2f}%"
                )
                return

        # ── 24h max-gain exit ────────────────────────────────────────
        if (
            self.config.enable_max_gain_24h_exit
            and 24 <= hold_hours < 25
            and pos.status == "normal"
        ):
            gain = (current_price - entry_price) / entry_price
            if gain > Decimal(str(self.config.max_gain_24h_threshold)):
                await self.executor.execute_exit(pos, current_price, now, "max_gain_24h_exit")
                return

        # Persist updated price tracking and flags
        if price_updated or pos.checked_2h_early_stop or pos.checked_12h_early_stop:
            self.store.save_position(pos)

    # ------------------------------------------------------------------
    # Observing State Machine (V2)
    # ------------------------------------------------------------------

    async def _handle_observing(
        self,
        pos: PaperPosition,
        current_price: Decimal,
        now: datetime,
        total_hold_hours: float,
    ):
        """Handle a position in observing state."""
        obs_entry_price = Decimal(pos.observing_entry_price or pos.entry_price)
        price_change = (current_price - obs_entry_price) / obs_entry_price
        lev_return = price_change * self.config.leverage

        # Total hold timeout
        if total_hold_hours >= self.config.max_hold_hours:
            await self.executor.execute_exit(pos, current_price, now, "observing_timeout")
            return

        # Path A: drops -18% → virtual tracking
        if lev_return <= Decimal("-0.18"):
            pos.status = "virtual_tracking"
            pos.is_virtual_tracking = True
            pos.virtual_entry_price = str(current_price)
            self.executor._pending_virtual_compensations += 1
            self.store.save_position(pos)
            self.console.print(
                f"  [magenta]VIRTUAL[/magenta] {pos.symbol} "
                f"obs drop {float(lev_return)*100:.1f}%"
            )
            return

        # Path B: gains +11% → take profit
        if lev_return >= Decimal("0.11"):
            await self.executor.execute_exit(pos, current_price, now, "observing_take_profit")
            return

        # Observing timeout
        if pos.observing_since:
            obs_hours = (now - datetime.fromisoformat(pos.observing_since)).total_seconds() / 3600
            if obs_hours >= self.config.max_hold_hours:
                await self.executor.execute_exit(pos, current_price, now, "observing_timeout")

    # ------------------------------------------------------------------
    # Dynamic TP (V2 — mirrors _update_dynamic_tp_v2)
    # ------------------------------------------------------------------

    async def _update_dynamic_tp(
        self,
        pos: PaperPosition,
        now: datetime,
        hold_hours: float,
        entry_price: Decimal,
    ):
        """Update dynamic TP based on coin strength evaluation."""
        if hold_hours < 2.0:
            return

        # 2h evaluation (run once)
        if not pos.evaluated_2h and hold_hours >= 2.0:
            pos.evaluated_2h = True
            entry_time = datetime.fromisoformat(pos.entry_time)
            pct_drop = await self._calc_5m_drop_ratio(
                pos.symbol,
                entry_time,
                entry_time + timedelta(hours=2),
                entry_price,
                self.config.strength_eval_2h_growth,
            )
            if pct_drop is not None and pct_drop >= self.config.strength_eval_2h_ratio:
                pos.strength = "strong"
                pos.tp_pct = self.config.strong_tp_pct
            else:
                pos.strength = "medium"
                pos.tp_pct = self.config.medium_tp_pct
            self.store.save_position(pos)

        # 12h evaluation (run once)
        if not pos.evaluated_12h and hold_hours >= 12.0:
            pos.evaluated_12h = True
            entry_time = datetime.fromisoformat(pos.entry_time)
            pct_drop = await self._calc_5m_drop_ratio(
                pos.symbol,
                entry_time,
                entry_time + timedelta(hours=12),
                entry_price,
                self.config.strength_eval_12h_growth,
            )
            if pct_drop is not None and pct_drop >= self.config.strength_eval_12h_ratio:
                pos.strength = "strong"
                pos.tp_pct = self.config.strong_tp_pct
            else:
                pos.strength = "weak"
                pos.tp_pct = self.config.weak_tp_pct
            self.store.save_position(pos)

    async def _calc_5m_drop_ratio(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        entry_price: Decimal,
        threshold: float,
    ) -> Optional[float]:
        """Compute fraction of 5m candles whose close dropped > threshold from entry."""
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
