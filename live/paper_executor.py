"""Paper order executor — simulated trade execution.

Replicates SurgeShortEngine._try_entry() V2 logic:
  - Pre-trade risk filters (optional, via Binance API)
  - Position sizing (capital * position_size_pct, capped by max_position_value_ratio)
  - Slippage simulation
  - PaperPosition creation

Data sources:
  - BinanceFuturesClient.get_ticker_price for real-time prices
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from rich.console import Console

from .models import utc_now, SurgeSignal
from .live_config import LiveTradingConfig
from .paper_store import PaperPosition, PaperStore, PaperTrade, SignalEvent
from .binance_client import BinanceFuturesClient
from .risk_filters import RiskFilters, RiskFilterConfig

logger = logging.getLogger(__name__)

# Simulated slippage (0.01% — conservative for futures)
SLIPPAGE_BPS = Decimal("0.0001")


class PaperOrderExecutor:
    """Simulated order execution for paper trading.

    Mirrors SurgeShortEngine._try_entry() V2 branch:
      1. Check max positions / max entries per day
      2. Get real-time price
      3. Compute position sizing
      4. Create PaperPosition (with slippage simulation)
    """

    def __init__(
        self,
        config: LiveTradingConfig,
        store: PaperStore,
        client: Optional[BinanceFuturesClient] = None,
        console: Optional[Console] = None,
    ):
        self.config = config
        self.store = store
        self.client = client or BinanceFuturesClient()
        self.console = console or Console()

        # Risk filters (uses same Binance client)
        self.risk_filters: Optional[RiskFilters] = None
        if config.enable_risk_filters:
            self.risk_filters = RiskFilters(self.client)

        # Capital tracking (loaded from store on start)
        self._capital = config.initial_capital
        self._pending_virtual_compensations: int = 0

    def restore_state(self):
        """Restore capital from persistent store (crash recovery)."""
        saved_capital = self.store.get_state("capital")
        if saved_capital:
            self._capital = Decimal(saved_capital)
            logger.info("Restored capital: $%s", self._capital)
        else:
            self._capital = self.config.initial_capital
            self.store.set_state("capital", str(self._capital))

        saved_virtual = self.store.get_state("pending_virtual_compensations")
        if saved_virtual:
            self._pending_virtual_compensations = int(saved_virtual)

    def _save_capital(self):
        """Persist current capital."""
        self.store.set_state("capital", str(self._capital))

    @property
    def capital(self) -> Decimal:
        return self._capital

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    async def execute_entry(self, signal: SurgeSignal) -> Optional[PaperPosition]:
        """Attempt to open a paper position (mirrors _try_entry V2)."""
        symbol = signal.symbol
        now = utc_now()

        # ── Guard checks ─────────────────────────────────────────────
        existing = self.store.get_position(symbol)
        if existing:
            logger.debug("Skip %s: already in position", symbol)
            return None

        open_count = self.store.position_count()
        if open_count >= self.config.max_positions:
            logger.debug("Skip %s: max positions reached (%d)", symbol, open_count)
            return None

        entries_today = self.store.get_entries_today()
        if entries_today >= self.config.max_entries_per_day:
            logger.debug("Skip %s: max entries today (%d)", symbol, entries_today)
            return None

        # ── Real-time price ──────────────────────────────────────────
        try:
            ticker = await self.client.get_ticker_price(symbol)
            entry_price = ticker.price  # Decimal
        except Exception as e:
            logger.warning("Failed to get price for %s: %s", symbol, e)
            return None

        signal_price = Decimal(str(signal.price))

        # ── Risk filters ────────────────────────────────────────────
        if self.risk_filters:
            try:
                filter_result = await self.risk_filters.check_all(
                    symbol, now, entry_price, signal_price
                )
                if not filter_result.should_trade:
                    import json as _json
                    self.store.save_signal_event(SignalEvent(
                        timestamp=now.isoformat(),
                        symbol=symbol,
                        surge_ratio=signal.surge_ratio,
                        price=str(entry_price),
                        accepted=False,
                        reject_reason=filter_result.reason,
                        risk_metrics_json=_json.dumps(
                            filter_result.metrics or {}, default=str
                        ),
                    ))
                    self.console.print(
                        f"  [yellow]FILTERED[/yellow] {symbol}: "
                        f"{filter_result.reason}"
                    )
                    return None
            except Exception as e:
                logger.warning("Risk filter error for %s (fail-open): %s", symbol, e)

        # ── Position sizing (mirrors _try_entry) ─────────────────────
        margin = self._capital * self.config.position_size_pct
        max_value = self._capital * Decimal(str(self.config.max_position_value_ratio))
        if margin > max_value:
            margin = max_value
        if margin > self._capital:
            logger.debug("Skip %s: insufficient capital", symbol)
            return None

        # Capital protection
        if self._capital < self.config.initial_capital * Decimal(str(self.config.min_capital_ratio)):
            logger.warning("Capital exhausted ($%s), skipping entry", self._capital)
            return None

        # Slippage simulation (price moves slightly against us for SHORT)
        slippage_price = entry_price * (Decimal("1") - SLIPPAGE_BPS)  # SHORT: we sell, filled lower
        size = margin * self.config.leverage / slippage_price
        self._capital -= margin
        self._save_capital()

        # ── Create paper position ────────────────────────────────────
        position = PaperPosition(
            symbol=symbol,
            side="short",
            entry_price=str(slippage_price),
            entry_time=now.isoformat(),
            size=str(size),
            margin=str(margin),
            leverage=self.config.leverage,
            signal_price=str(signal_price),
            signal_time=signal.signal_date.isoformat(),
            signal_surge_ratio=signal.surge_ratio,
            tp_pct=self.config.strong_tp_pct,
            strength="strong",
            status="normal",
            max_price=str(slippage_price),
            min_price=str(slippage_price),
        )
        self.store.save_position(position)

        # Log accepted signal
        self.store.save_signal_event(SignalEvent(
            timestamp=now.isoformat(),
            symbol=symbol,
            surge_ratio=signal.surge_ratio,
            price=str(entry_price),
            accepted=True,
        ))

        self.console.print(
            f"  [green]ENTRY[/green] {symbol} @ {slippage_price:.6f} "
            f"(surge: {signal.surge_ratio:.1f}x, "
            f"margin: ${margin:.2f})"
        )
        return position

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    async def execute_exit(
        self,
        position: PaperPosition,
        exit_price: Decimal,
        exit_time: datetime,
        reason: str,
    ) -> PaperTrade:
        """Close a paper position and record the trade."""
        entry_price = Decimal(position.entry_price)
        size = Decimal(position.size)
        margin = Decimal(position.margin)

        # PnL for SHORT
        pnl = (entry_price - exit_price) * size
        pnl_pct = (entry_price - exit_price) / entry_price * 100

        # Commission
        commission = (entry_price + exit_price) * size * self.config.commission_rate
        pnl -= commission

        # Return margin + PnL (unless capital already returned in observing)
        if position.capital_already_returned:
            self._capital += pnl
        else:
            self._capital += margin + pnl
        self._save_capital()

        hold_hours = (
            exit_time - datetime.fromisoformat(position.entry_time)
        ).total_seconds() / 3600

        trade = PaperTrade(
            symbol=position.symbol,
            side="short",
            signal_time=position.signal_time,
            signal_price=position.signal_price,
            entry_time=position.entry_time,
            exit_time=exit_time.isoformat(),
            entry_price=position.entry_price,
            exit_price=str(exit_price),
            size=position.size,
            pnl=str(pnl),
            pnl_pct=str(pnl_pct),
            exit_reason=reason,
            hold_hours=hold_hours,
            signal_surge_ratio=position.signal_surge_ratio,
            coin_strength=position.strength,
            status_at_exit=position.status,
            tp_pct_used=position.tp_pct,
        )

        self.store.save_trade(trade)
        self.store.remove_position(position.symbol)

        color = "green" if pnl > 0 else "red"
        self.console.print(
            f"  [{color}]EXIT[/{color}] {position.symbol} @ {exit_price:.6f} "
            f"PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%) [{reason}] "
            f"[{position.strength}]"
        )
        return trade

    def cleanup(self):
        """Cleanup resources."""
        pass
