"""Rolling Live Strategy — Moonshot-R24 implementation of Strategy ABC.

Plugs into LiveTrader to replace SurgeShortStrategy with 24h rolling
top gainer short strategy.

Decision points:
  1. create_scanner()        → RollingLiveScanner (24hr ticker based)
  2. filter_entry()          → main profit check + listing date filter
  3. evaluate_position()     → trailing stop + time-based TP + max hold
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from .strategy import Strategy, EntryDecision, PositionAction
from .rolling_config import RollingLiveConfig

if TYPE_CHECKING:
    from .binance_client import BinanceFuturesClient
    from .live_config import LiveTradingConfig
    from .live_position_monitor import TrackedPosition

logger = logging.getLogger(__name__)


class RollingLiveStrategy(Strategy):
    """Moonshot-R24 — 24h rolling top gainer short strategy for live trading.

    Signal:  24h rolling price change top N (via Binance 24hr Ticker)
    Entry:   SHORT with main profit check + listing date filter
    TP/SL:   time-decaying TP, fixed SL, trailing stop
    Exit:    max hold time, trailing stop bounce
    """

    def __init__(self, config: Optional[RollingLiveConfig] = None, store: Any = None) -> None:
        self.config = config or RollingLiveConfig()
        self._store = store  # TradeStore for cooldown check

        # Cache for listing dates (avoid re-fetching)
        self._listing_cache: dict[str, Optional[datetime]] = {}

    # ── Scanner ────────────────────────────────────────────────────

    def create_scanner(self, config, signal_queue, client, console):
        from .rolling_scanner import RollingLiveScanner

        return RollingLiveScanner(
            config=self.config,
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
        """Run R24 entry checks: listing date + main profit check."""

        symbol = signal.symbol
        pct_chg = signal.surge_ratio  # reused field

        logger.info(
            "🔍 R24 入场过滤: %s (24h涨幅=%.1f%%, 价格=%.6f)",
            symbol, pct_chg, float(entry_price),
        )

        # ── 0. Cooldown period check ───────────────────────────
        if self._store and self.config.signal_cooldown_hours > 0:
            try:
                cooldown_cutoff = now - timedelta(hours=self.config.signal_cooldown_hours)
                cutoff_str = cooldown_cutoff.strftime("%Y-%m-%d")
                recent_trades = self._store.get_live_trades(limit=200, since_date=cutoff_str)
                for t in recent_trades:
                    if t.symbol == symbol and t.event == "entry":
                        # Parse timestamp
                        try:
                            trade_time = datetime.fromisoformat(t.timestamp.replace("Z", "+00:00"))
                            if trade_time.tzinfo is None:
                                trade_time = trade_time.replace(tzinfo=timezone.utc)
                            hours_ago = (now - trade_time).total_seconds() / 3600
                            if hours_ago < self.config.signal_cooldown_hours:
                                logger.info(
                                    "  ❌ 冷却期: %s %.1f小时前刚入场 < 冷却%dh",
                                    symbol, hours_ago, self.config.signal_cooldown_hours,
                                )
                                return EntryDecision(
                                    should_enter=False,
                                    reject_reason=(
                                        f"冷却期: {hours_ago:.1f}h前刚入场 "
                                        f"< 冷却{self.config.signal_cooldown_hours}h"
                                    ),
                                )
                        except (ValueError, TypeError):
                            pass
                        break  # Only check the most recent entry
            except Exception as e:
                logger.debug("冷却期检查异常 (fail-open): %s", e)

        # ── 1. Listing date filter ─────────────────────────────
        if self.config.min_listed_days > 0:
            listing_date = await self._get_listing_date(client, symbol)
            if listing_date is not None:
                days_listed = (now - listing_date).days
                if days_listed < self.config.min_listed_days:
                    logger.info(
                        "  ❌ 新币过滤: %s 上市%d天 < %d天 (上市日: %s)",
                        symbol, days_listed, self.config.min_listed_days,
                        listing_date.strftime("%Y-%m-%d"),
                    )
                    return EntryDecision(
                        should_enter=False,
                        reject_reason=f"新币过滤: 上市{days_listed}天 < {self.config.min_listed_days}天",
                    )
                logger.info(
                    "  ✅ 上市天数: %s 已上市%d天 (上市日: %s)",
                    symbol, days_listed, listing_date.strftime("%Y-%m-%d"),
                )
            else:
                logger.info("  ⚠️ 上市日期: %s 无法获取 (跳过检查)", symbol)

        # ── 2. Main profit check ───────────────────────────────
        if self.config.enable_main_profit_check:
            try:
                avg_price = await self._get_30d_avg_price(client, symbol)
                yesterday_close = await self._get_yesterday_close(client, symbol, now)

                if avg_price and avg_price > 0 and yesterday_close and yesterday_close > 0:
                    from_avg_pct = (yesterday_close - avg_price) / avg_price * 100
                    threshold = self._get_main_profit_threshold(pct_chg)

                    if from_avg_pct < threshold:
                        logger.info(
                            "  ❌ 主力未获利: %s 距30d均价 %.1f%% < 阈值 %d%% "
                            "(30d均价=%.6f, 昨收=%.6f)",
                            symbol, from_avg_pct, threshold, avg_price, yesterday_close,
                        )
                        return EntryDecision(
                            should_enter=False,
                            reject_reason=(
                                f"主力未获利: 距30d均价 {from_avg_pct:.1f}% "
                                f"< 阈值 {threshold}%"
                            ),
                        )
                    logger.info(
                        "  ✅ 主力获利: %s 距30d均价 +%.1f%% ≥ 阈值 %d%% "
                        "(30d均价=%.6f, 昨收=%.6f)",
                        symbol, from_avg_pct, threshold, avg_price, yesterday_close,
                    )
                else:
                    logger.info(
                        "  ⚠️ 主力获利: %s 数据不足 (30d均价=%s, 昨收=%s, 跳过检查)",
                        symbol, avg_price, yesterday_close,
                    )
            except Exception as e:
                logger.warning("  ⚠️ 主力获利检查异常 %s (fail-open): %s", symbol, e)

        # ── All checks passed ──────────────────────────────────
        tp_pct = self.config.tp_initial * 100   # 0.34 → 34%
        sl_pct = self.config.sl_threshold * 100  # 0.44 → 44%

        logger.info(
            "  🟢 %s 通过全部过滤 → SHORT TP=%.0f%% SL=%.0f%%",
            symbol, tp_pct, sl_pct,
        )
        return EntryDecision(
            should_enter=True,
            side="SHORT",  # position side: open_position() converts to SELL internally
            tp_pct=tp_pct,
            sl_pct=sl_pct,
        )

    # ── Position Evaluation ───────────────────────────────────────

    async def evaluate_position(
        self,
        client: BinanceFuturesClient,
        pos: TrackedPosition,
        config: LiveTradingConfig,
        now: datetime,
    ) -> PositionAction:
        """Evaluate R24 position: trailing stop, time-based TP, max hold.

        Unlike SurgeShort's 2h/12h checkpoint evaluation, R24 uses:
        1. Max hold time check
        2. Time-based TP decay (tp_initial → tp_reduced after N hours)
        3. Trailing stop (activation + distance)
        """

        if not pos.entry_fill_time or not pos.entry_price:
            return PositionAction("hold")

        entry_price = float(pos.entry_price)
        hold_hours = (now - pos.entry_fill_time).total_seconds() / 3600

        # ── 1. Max hold time ─────────────────────────────────
        max_hold_hours = self.config.max_hold_days * 24
        if hold_hours >= max_hold_hours:
            return PositionAction("close", reason="max_hold_time")

        # ── 2. Time-based TP adjustment ──────────────────────
        # Determine current TP based on hold duration
        if pos.has_added_position:
            target_tp_pct = self.config.tp_after_add * 100
        elif hold_hours >= self.config.tp_hours_threshold:
            target_tp_pct = self.config.tp_reduced * 100
        else:
            target_tp_pct = self.config.tp_initial * 100

        # If TP needs adjustment, request it
        if abs(pos.current_tp_pct - target_tp_pct) > 0.1:
            logger.info(
                "📊 R24 TP调整: %s hold=%.1fh — TP %.1f%% → %.1f%%",
                pos.symbol, hold_hours, pos.current_tp_pct, target_tp_pct,
            )
            return PositionAction(
                "adjust_tp",
                new_tp_pct=target_tp_pct,
                new_strength="reduced" if target_tp_pct == self.config.tp_reduced * 100 else "initial",
            )

        # ── 2.5. Add position (逆势加仓) ─────────────────────
        if (self.config.enable_add_position
                and not pos.has_added_position):
            try:
                ticker = await client.get_ticker_price(pos.symbol)
                current_price = float(ticker.price)

                # SHORT: price rises above threshold → add position
                rise_from_entry = (current_price - entry_price) / entry_price
                if rise_from_entry >= self.config.add_position_threshold:
                    logger.info(
                        "📈 R24 加仓触发: %s — 入场 %.6f, 当前 %.6f, "
                        "涨幅 %.1f%% ≥ 阈值 %.0f%%",
                        pos.symbol, entry_price, current_price, rise_from_entry * 100,
                        self.config.add_position_threshold * 100,
                    )
                    return PositionAction(
                        "add_position",
                        reason="add_position",
                        new_tp_pct=self.config.tp_after_add * 100,
                    )
            except Exception as e:
                logger.debug("Add-position check failed for %s: %s", pos.symbol, e)

        # ── 3. Trailing stop (via real-time price) ───────────
        if self.config.enable_trailing_stop:
            try:
                ticker = await client.get_ticker_price(pos.symbol)
                current_price = float(ticker.price)

                # Track lowest price for SHORT position
                lowest = float(pos.lowest_price) if pos.lowest_price is not None else current_price
                if current_price < lowest:
                    lowest = current_price
                    pos.lowest_price = Decimal(str(current_price))

                # Check trailing stop activation
                drop_from_entry = (entry_price - lowest) / entry_price
                if drop_from_entry >= self.config.trailing_activation_pct:
                    # Trailing stop activated — check if price bounced
                    trailing_price = lowest * (1 + self.config.trailing_distance_pct)
                    if current_price >= trailing_price:
                        logger.info(
                            "📈 R24 追踪止损触发: %s — 最低 %.6f, 当前 %.6f, "
                            "回弹线 %.6f (激活=%.0f%%, 距离=%.0f%%)",
                            pos.symbol, lowest, current_price, trailing_price,
                            self.config.trailing_activation_pct * 100,
                            self.config.trailing_distance_pct * 100,
                        )
                        return PositionAction("close", reason="trailing_stop")

            except Exception as e:
                logger.debug("Trailing stop check failed for %s: %s", pos.symbol, e)

        return PositionAction("hold")

    # ── Helpers ────────────────────────────────────────────────────

    def _get_main_profit_threshold(self, pct_chg: float) -> float:
        """Get profit threshold based on surge percentage."""
        for max_pct, threshold in self.config.main_profit_thresholds:
            if pct_chg < max_pct:
                return threshold
        return self.config.main_profit_thresholds[-1][1]

    async def _get_listing_date(
        self, client: BinanceFuturesClient, symbol: str,
    ) -> Optional[datetime]:
        """Get listing date by querying earliest kline (cached)."""
        if symbol in self._listing_cache:
            return self._listing_cache[symbol]
        try:
            klines = await client.get_klines(
                symbol=symbol, interval="1d", limit=1, start_time=0,
            )
            if klines:
                listing = datetime.fromtimestamp(
                    klines[0].open_time / 1000, tz=timezone.utc,
                )
                self._listing_cache[symbol] = listing
                return listing
        except Exception as e:
            logger.debug("获取 %s 上市日期失败: %s", symbol, e)
        self._listing_cache[symbol] = None
        return None

    async def _get_30d_avg_price(
        self, client: BinanceFuturesClient, symbol: str,
    ) -> Optional[float]:
        """Get 30-day average closing price."""
        try:
            klines = await client.get_klines(
                symbol=symbol, interval="1d", limit=31,
            )
            if len(klines) < 2:
                return None
            closes = [float(k.close) for k in klines[:-1]]  # exclude today
            return sum(closes) / len(closes) if closes else None
        except Exception as e:
            logger.debug("获取 %s 30d均价失败: %s", symbol, e)
            return None

    async def _get_yesterday_close(
        self, client: BinanceFuturesClient, symbol: str, now: datetime,
    ) -> Optional[float]:
        """Get yesterday's daily close price."""
        try:
            yesterday = now - timedelta(days=1)
            day_start_ms = int(
                yesterday.replace(
                    hour=0, minute=0, second=0, microsecond=0,
                ).timestamp() * 1000,
            )
            klines = await client.get_klines(
                symbol=symbol, interval="1d",
                start_time=day_start_ms,
                end_time=day_start_ms + 86_400_000,
                limit=1,
            )
            return float(klines[0].close) if klines else None
        except Exception as e:
            logger.debug("获取 %s 昨日收盘价失败: %s", symbol, e)
            return None
