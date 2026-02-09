"""Risk filters for pre-trade entry checks.

Controls position-opening risk by running a pipeline of filters before entry.
All market data is fetched from Binance Futures API — no external database needed.

Each filter returns a FilterResult:
  - should_trade: bool — whether to proceed
  - reason: str — explanation if filtered
  - metrics: dict — diagnostic data for logging
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────

@dataclass
class FilterResult:
    """Result from a risk filter check."""
    should_trade: bool
    reason: str = ""
    metrics: dict | None = None


@dataclass
class RiskFilterConfig:
    """Configuration for risk filters.

    All filters default to disabled. Enable selectively as needed.
    """

    # ── Premium 24h change ──────────────────────────────────────────
    enable_premium_24h_filter: bool = False
    premium_24h_drop_threshold: float = -40.0  # reject if premium dropped > 40%

    # ── Entry gain (price change since signal) ──────────────────────
    enable_entry_gain_filter: bool = True
    entry_gain_max_pct: float = 9.04   # max allowed gain %
    entry_gain_min_pct: float = -3.0   # min allowed (reject if dropped too much)

    # ── CVD new low ─────────────────────────────────────────────────
    enable_cvd_new_low_filter: bool = False
    cvd_lookback_hours: int = 24

    # ── Premium realtime (basis rate) ───────────────────────────────
    enable_premium_realtime_filter: bool = False
    premium_min_threshold: float = -0.003  # -0.3%

    # ── Buy acceleration ────────────────────────────────────────────
    enable_buy_acceleration_filter: bool = False
    buy_accel_danger_ranges: list[tuple[float, float]] = field(
        default_factory=lambda: [
            (-0.05, -0.042),
            (0.118, 0.12),
            (0.0117, 0.03),
            (0.2, 0.99),
        ]
    )

    # ── Consecutive buy ratio ───────────────────────────────────────
    enable_consecutive_buy_ratio_filter: bool = False
    consecutive_buy_ratio_hours: int = 3
    consecutive_buy_ratio_threshold: float = 2.5

    # ── Buy/sell volume ratio ───────────────────────────────────────
    enable_buy_sell_ratio_filter: bool = False
    buy_sell_ratio_danger_ranges: list[tuple[float, float]] = field(
        default_factory=lambda: [(0.94, 1.12)]
    )

    # ── Intraday buy ratio ──────────────────────────────────────────
    enable_intraday_buy_ratio_filter: bool = False
    intraday_buy_ratio_danger_ranges: list[tuple[float, float]] = field(
        default_factory=lambda: [(2.78, 3.71), (25, 29)]
    )


# ──────────────────────────────────────────────────────────────────────
# Risk Filters
# ──────────────────────────────────────────────────────────────────────

class RiskFilters:
    """Pre-trade risk filter pipeline.

    All data fetched from Binance Futures API. Fail-open: if any API
    call fails, the filter passes (allows trade) and logs the error.

    Usage::

        filters = RiskFilters(client, config)
        result = await filters.check_all(symbol, entry_dt, entry_price, signal_price)
        if not result.should_trade:
            print(f"Filtered: {result.reason}")
    """

    def __init__(
        self,
        client: BinanceFuturesClient,
        config: Optional[RiskFilterConfig] = None,
    ):
        self.client = client
        self.config = config or RiskFilterConfig()

    async def check_all(
        self,
        symbol: str,
        entry_datetime: datetime,
        entry_price: Optional[Decimal] = None,
        signal_price: Optional[Decimal] = None,
    ) -> FilterResult:
        """Run all enabled filters sequentially.

        Returns on first rejection (fail-fast). Collects metrics from
        all filters that run.
        """
        all_metrics: dict = {}

        checks = [
            (
                self.config.enable_premium_24h_filter,
                lambda: self._check_premium_24h_change(symbol, entry_datetime),
            ),
            (
                self.config.enable_entry_gain_filter
                and entry_price is not None
                and signal_price is not None,
                lambda: self._check_entry_gain(
                    symbol, entry_price, signal_price, entry_datetime  # type: ignore[arg-type]
                ),
            ),
            (
                self.config.enable_cvd_new_low_filter,
                lambda: self._check_cvd_new_low(symbol, entry_datetime),
            ),
            (
                self.config.enable_premium_realtime_filter,
                lambda: self._check_premium_realtime(symbol, entry_datetime),
            ),
            (
                self.config.enable_buy_acceleration_filter,
                lambda: self._check_buy_acceleration(symbol, entry_datetime),
            ),
            (
                self.config.enable_consecutive_buy_ratio_filter,
                lambda: self._check_consecutive_buy_ratio(symbol, entry_datetime),
            ),
            (
                self.config.enable_buy_sell_ratio_filter,
                lambda: self._check_buy_sell_ratio(symbol, entry_datetime),
            ),
        ]

        for enabled, check_fn in checks:
            if not enabled:
                continue
            result = await check_fn()
            all_metrics.update(result.metrics or {})
            if not result.should_trade:
                return FilterResult(
                    should_trade=False,
                    reason=result.reason,
                    metrics=all_metrics,
                )

        # Entry gain min-pct check (separate from max-pct above)
        if (
            self.config.enable_entry_gain_filter
            and entry_price is not None
            and signal_price is not None
            and signal_price > 0
        ):
            gain_pct = float((entry_price - signal_price) / signal_price * 100)
            if gain_pct < self.config.entry_gain_min_pct:
                return FilterResult(
                    should_trade=False,
                    reason=(
                        f"Price dropped {gain_pct:.2f}% since signal "
                        f"(min: {self.config.entry_gain_min_pct}%)"
                    ),
                    metrics={**all_metrics, "entry_gain_pct": gain_pct},
                )

        return FilterResult(should_trade=True, reason="", metrics=all_metrics)

    # ── Individual Filters ─────────────────────────────────────────

    async def _check_premium_24h_change(
        self, symbol: str, entry_datetime: datetime
    ) -> FilterResult:
        """Check 24h premium index change via kline close prices.

        Fetches 1h klines for the last 25 hours and compares the
        premium component (close relative to open) at entry vs 24h ago.
        """
        try:
            entry_ts = int(entry_datetime.timestamp() * 1000)
            start_ts = entry_ts - 25 * 3600 * 1000  # 25h to ensure coverage

            klines = await self.client.get_klines(
                symbol, "1h", start_time=start_ts, end_time=entry_ts, limit=25
            )

            if len(klines) < 2:
                return FilterResult(
                    should_trade=True,
                    reason="Insufficient kline data for premium 24h",
                    metrics={"premium_24h_change": None},
                )

            # Use close prices as proxy — first kline ≈ 24h ago, last ≈ now
            price_24h_ago = float(klines[0].close)
            price_now = float(klines[-1].close)

            if abs(price_24h_ago) < 1e-10:
                return FilterResult(
                    should_trade=True,
                    metrics={"premium_24h_change": None},
                )

            change_pct = ((price_now - price_24h_ago) / price_24h_ago) * 100
            metrics = {
                "premium_24h_change": change_pct,
                "price_24h_ago": price_24h_ago,
                "price_now": price_now,
            }

            if change_pct < self.config.premium_24h_drop_threshold:
                return FilterResult(
                    should_trade=False,
                    reason=(
                        f"Price dropped {change_pct:.2f}% in 24h "
                        f"(threshold: {self.config.premium_24h_drop_threshold}%)"
                    ),
                    metrics=metrics,
                )

            return FilterResult(should_trade=True, metrics=metrics)

        except Exception as e:
            logger.warning("Premium 24h check failed for %s: %s", symbol, e)
            return FilterResult(
                should_trade=True,
                reason=f"Premium 24h check error: {e}",
                metrics={"premium_24h_change": None},
            )

    async def _check_entry_gain(
        self,
        symbol: str,
        entry_price: Decimal,
        signal_price: Decimal,
        entry_datetime: datetime,
    ) -> FilterResult:
        """Check if price has risen too much since signal.

        For SHORT: if price already up significantly, we may be
        catching a falling knife on the reversal.
        """
        try:
            if signal_price <= 0:
                return FilterResult(
                    should_trade=True,
                    metrics={"entry_gain_pct": None},
                )

            gain_pct = float((entry_price - signal_price) / signal_price * 100)
            metrics = {
                "entry_gain_pct": gain_pct,
                "entry_price": float(entry_price),
                "signal_price": float(signal_price),
            }

            if gain_pct > self.config.entry_gain_max_pct:
                return FilterResult(
                    should_trade=False,
                    reason=(
                        f"Price already up {gain_pct:.2f}% since signal "
                        f"(max: {self.config.entry_gain_max_pct}%)"
                    ),
                    metrics=metrics,
                )

            return FilterResult(should_trade=True, metrics=metrics)

        except Exception as e:
            logger.warning("Entry gain check failed for %s: %s", symbol, e)
            return FilterResult(
                should_trade=True,
                reason=f"Entry gain check error: {e}",
                metrics={"entry_gain_pct": None},
            )

    async def _check_cvd_new_low(
        self, symbol: str, entry_datetime: datetime
    ) -> FilterResult:
        """Check if CVD (Cumulative Volume Delta) is at lookback-period low.

        CVD = Σ(taker_buy_volume - taker_sell_volume).
        If current CVD <= min CVD in window → panic selling exhaustion,
        high reversal risk for shorts.
        """
        try:
            entry_ts = int(entry_datetime.timestamp() * 1000)
            lookback_ms = self.config.cvd_lookback_hours * 3600 * 1000
            start_ts = entry_ts - lookback_ms

            klines = await self.client.get_klines(
                symbol, "1h", start_time=start_ts, end_time=entry_ts,
                limit=self.config.cvd_lookback_hours + 1,
            )

            if len(klines) < 2:
                return FilterResult(
                    should_trade=True,
                    reason="CVD data insufficient",
                    metrics={"cvd_is_new_low": None},
                )

            cvd_values = []
            cumulative = 0.0
            for k in klines:
                buy_vol = float(k.taker_buy_base_volume)
                sell_vol = float(k.volume) - buy_vol
                cumulative += buy_vol - sell_vol
                cvd_values.append(cumulative)

            cvd_current = cvd_values[-1]
            cvd_min = min(cvd_values)
            is_new_low = cvd_current <= cvd_min

            metrics = {
                "cvd_current": cvd_current,
                "cvd_min": cvd_min,
                "cvd_is_new_low": is_new_low,
            }

            if is_new_low:
                return FilterResult(
                    should_trade=False,
                    reason=(
                        f"CVD at new low ({cvd_current:.0f}, "
                        f"min: {cvd_min:.0f}) — panic selling exhaustion"
                    ),
                    metrics=metrics,
                )

            return FilterResult(should_trade=True, metrics=metrics)

        except Exception as e:
            logger.warning("CVD check failed for %s: %s", symbol, e)
            return FilterResult(
                should_trade=True,
                reason=f"CVD check error: {e}",
                metrics={"cvd_is_new_low": None},
            )

    async def _check_premium_realtime(
        self, symbol: str, entry_datetime: datetime
    ) -> FilterResult:
        """Check realtime premium (basis rate) via Binance premium index API.

        Premium = (mark_price - index_price) / index_price.
        If premium < threshold → negative basis too large, shorting costs high.
        """
        try:
            data = await self.client.get_premium_index(symbol)

            mark = float(data.get("markPrice", 0))
            index = float(data.get("indexPrice", 0))

            if index <= 0:
                return FilterResult(
                    should_trade=True,
                    metrics={"premium_realtime": None},
                )

            premium = (mark - index) / index
            metrics = {
                "premium_realtime": premium,
                "mark_price": mark,
                "index_price": index,
            }

            if premium < self.config.premium_min_threshold:
                return FilterResult(
                    should_trade=False,
                    reason=(
                        f"Premium {premium * 100:.3f}% < "
                        f"{self.config.premium_min_threshold * 100:.1f}% — "
                        f"negative basis too large"
                    ),
                    metrics=metrics,
                )

            return FilterResult(should_trade=True, metrics=metrics)

        except Exception as e:
            logger.warning("Premium realtime check failed for %s: %s", symbol, e)
            return FilterResult(
                should_trade=True,
                reason=f"Premium realtime check error: {e}",
                metrics={"premium_realtime": None},
            )

    async def _check_buy_acceleration(
        self, symbol: str, entry_datetime: datetime
    ) -> FilterResult:
        """Check buy volume acceleration (last 6h vs prior 18h).

        Acceleration = mean(buy/sell ratio, last 6h) - mean(buy/sell ratio, prior 18h).
        If in a danger range → reject.
        """
        try:
            entry_ts = int(entry_datetime.timestamp() * 1000)
            start_ts = entry_ts - 24 * 3600 * 1000

            klines = await self.client.get_klines(
                symbol, "1h", start_time=start_ts, end_time=entry_ts, limit=24
            )

            if len(klines) < 12:
                return FilterResult(
                    should_trade=True,
                    reason="Not enough data for buy acceleration",
                    metrics={"buy_acceleration": None},
                )

            ratios = []
            for k in klines:
                buy = float(k.taker_buy_base_volume)
                sell = float(k.volume) - buy
                ratios.append(buy / (sell + 1e-10))

            last_6 = ratios[-6:] if len(ratios) >= 6 else ratios
            first_18 = ratios[:-6] if len(ratios) > 6 else ratios[: len(ratios) // 2]

            accel = (sum(last_6) / len(last_6)) - (sum(first_18) / len(first_18))
            metrics = {"buy_acceleration": accel}

            for lo, hi in self.config.buy_accel_danger_ranges:
                if lo <= accel <= hi:
                    return FilterResult(
                        should_trade=False,
                        reason=(
                            f"Buy acceleration {accel:.4f} in danger range "
                            f"[{lo}, {hi}]"
                        ),
                        metrics=metrics,
                    )

            return FilterResult(should_trade=True, metrics=metrics)

        except Exception as e:
            logger.warning("Buy acceleration check failed for %s: %s", symbol, e)
            return FilterResult(
                should_trade=True,
                reason=f"Buy acceleration check error: {e}",
                metrics={"buy_acceleration": None},
            )

    async def _check_consecutive_buy_ratio(
        self, symbol: str, entry_datetime: datetime
    ) -> FilterResult:
        """Check consecutive hours of buy volume surge.

        If N consecutive hours have buy_vol > threshold × prev_hour_buy_vol,
        it indicates sustained buying pressure — dangerous for shorts.
        """
        try:
            entry_ts = int(entry_datetime.timestamp() * 1000)
            start_ts = entry_ts - 12 * 3600 * 1000
            threshold = self.config.consecutive_buy_ratio_threshold
            required = self.config.consecutive_buy_ratio_hours

            klines = await self.client.get_klines(
                symbol, "1h", start_time=start_ts, end_time=entry_ts, limit=12
            )

            if len(klines) < required + 1:
                return FilterResult(
                    should_trade=True,
                    reason="Not enough data for consecutive buy check",
                    metrics={"max_consecutive_buy": 0},
                )

            buy_vols = [float(k.taker_buy_base_volume) for k in klines]
            max_consecutive = 0
            current_run = 0

            for i in range(1, len(buy_vols)):
                if buy_vols[i - 1] > 0 and buy_vols[i] / buy_vols[i - 1] > threshold:
                    current_run += 1
                    max_consecutive = max(max_consecutive, current_run)
                else:
                    current_run = 0

            metrics = {"max_consecutive_buy": max_consecutive}

            if max_consecutive >= required:
                return FilterResult(
                    should_trade=False,
                    reason=(
                        f"Consecutive {max_consecutive}h buy surge > "
                        f"{threshold}x — sustained breakout risk"
                    ),
                    metrics=metrics,
                )

            return FilterResult(should_trade=True, metrics=metrics)

        except Exception as e:
            logger.warning("Consecutive buy check failed for %s: %s", symbol, e)
            return FilterResult(
                should_trade=True,
                reason=f"Consecutive buy check error: {e}",
                metrics={"max_consecutive_buy": 0},
            )

    async def _check_buy_sell_ratio(
        self, symbol: str, entry_datetime: datetime
    ) -> FilterResult:
        """Check buy/sell volume ratio in the 12h before entry.

        Calculates max hour-over-hour buy ratio and sell ratio,
        then checks if their ratio falls in danger ranges.
        Also checks intraday buy ratio if enabled.
        """
        try:
            entry_ts = int(entry_datetime.timestamp() * 1000)
            start_ts = entry_ts - 12 * 3600 * 1000

            klines = await self.client.get_klines(
                symbol, "1h", start_time=start_ts, end_time=entry_ts, limit=12
            )

            if len(klines) < 2:
                return FilterResult(
                    should_trade=True,
                    reason="Not enough data for buy/sell ratio",
                    metrics={"buy_sell_ratio": None},
                )

            max_buy_ratio = 0.0
            max_sell_ratio = 0.0

            for i in range(1, len(klines)):
                prev_buy = float(klines[i - 1].taker_buy_base_volume)
                prev_sell = float(klines[i - 1].volume) - prev_buy
                curr_buy = float(klines[i].taker_buy_base_volume)
                curr_sell = float(klines[i].volume) - curr_buy

                if prev_buy > 0:
                    max_buy_ratio = max(max_buy_ratio, curr_buy / prev_buy)
                if prev_sell > 0:
                    max_sell_ratio = max(max_sell_ratio, curr_sell / prev_sell)

            bs_ratio = max_buy_ratio / max_sell_ratio if max_sell_ratio > 0 else 0.0
            metrics = {
                "buy_sell_ratio": bs_ratio,
                "max_buy_ratio": max_buy_ratio,
                "max_sell_ratio": max_sell_ratio,
            }

            for lo, hi in self.config.buy_sell_ratio_danger_ranges:
                if lo <= bs_ratio <= hi:
                    return FilterResult(
                        should_trade=False,
                        reason=(
                            f"Buy/sell ratio {bs_ratio:.3f} in danger range "
                            f"[{lo}, {hi}] — ambiguous direction"
                        ),
                        metrics=metrics,
                    )

            # Also check intraday buy ratio
            if self.config.enable_intraday_buy_ratio_filter:
                for lo, hi in self.config.intraday_buy_ratio_danger_ranges:
                    if lo <= max_buy_ratio <= hi:
                        return FilterResult(
                            should_trade=False,
                            reason=(
                                f"Intraday buy ratio {max_buy_ratio:.2f}x "
                                f"in danger range [{lo}, {hi}]"
                            ),
                            metrics=metrics,
                        )

            return FilterResult(should_trade=True, metrics=metrics)

        except Exception as e:
            logger.warning("Buy/sell ratio check failed for %s: %s", symbol, e)
            return FilterResult(
                should_trade=True,
                reason=f"Buy/sell ratio check error: {e}",
                metrics={"buy_sell_ratio": None},
            )
