"""Real-time surge signal scanner using Binance REST API.

Core detection logic: sell_volume / yesterday_avg_sell_volume >= threshold.

Data source:
  - BinanceFuturesClient.get_klines("1d") for yesterday's daily volume
  - BinanceFuturesClient.get_klines("1h") for current-hour volume
  - sell_volume = total_volume - taker_buy_base_volume
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from rich.console import Console

from .live_config import LiveTradingConfig
from .binance_client import BinanceFuturesClient
from .models import SurgeSignal

logger = logging.getLogger(__name__)


@dataclass
class LiveScanResult:
    """Result of a single live scan cycle."""
    timestamp: datetime
    signals: list[SurgeSignal]
    symbols_scanned: int = 0
    errors: int = 0


class LiveSurgeScanner:
    """Real-time surge signal scanner using Binance Futures API.

    Scans all USDT-margined perpetual contracts at each UTC hour boundary.
    Detection logic mirrors SurgeScanner._scan_symbol():
      1. Fetch yesterday's 1d kline → compute avg hourly sell volume
      2. Fetch current-hour 1h kline → compute hourly sell volume
      3. ratio = hourly_sell_vol / yesterday_avg_sell_vol
      4. If threshold <= ratio <= max_multiple → emit SurgeSignal
    """

    def __init__(
        self,
        config: LiveTradingConfig,
        signal_queue: asyncio.Queue,
        client: Optional[BinanceFuturesClient] = None,
        console: Optional[Console] = None,
    ):
        self.config = config
        self.signal_queue = signal_queue
        self.client = client or BinanceFuturesClient()
        self.console = console or Console()
        self.running = False

        # Cache
        self._usdt_symbols: Optional[list[str]] = None
        self._seen_signals: set[str] = set()  # "SYMBOL:YYYY-MM-DD" dedup key (one per day)
        self._sl_cooldown: set[str] = set()   # S: symbols blocked after SL today (same format)

        # Daily kline cache: symbol -> y_avg_hour_sell
        # Invalidated automatically when UTC date changes.
        self._daily_cache: dict[str, float] = {}
        self._daily_cache_date: Optional[date] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_forever(self):
        """Main loop: scan at each UTC hour boundary.

        Always waits for the next hour boundary before scanning
        (even on first startup). Adds 5s buffer for kline finalization.
        """
        self.running = True
        logger.info(
            "LiveSurgeScanner started (threshold=%.1fx)",
            self.config.surge_threshold,
        )
        # 启动信息已由上方 logger.info 记录，无需重复

        while self.running:
            # Wait until next hour boundary (+5s buffer)
            now = datetime.now(timezone.utc)
            next_hour = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
            wait_seconds = (next_hour - now).total_seconds()
            logger.debug(
                "⏳ 下次扫描: %s UTC (%.0f秒后)",
                next_hour.strftime("%H:%M:%S"), wait_seconds,
            )
            await asyncio.sleep(wait_seconds)

            if not self.running:
                break

            try:
                result = await self.scan_current_hour()
                new_signals = 0
                for sig in result.signals:
                    dedup_key = f"{sig.symbol}:{sig.signal_date.strftime('%Y-%m-%d')}"
                    if dedup_key not in self._seen_signals:
                        self._seen_signals.add(dedup_key)
                        await self.signal_queue.put(sig)
                        new_signals += 1

                if new_signals > 0:
                    logger.info(
                        "📡 扫描完成: %d 个新信号 (%d 个标的, %d 个错误)",
                        new_signals, result.symbols_scanned, result.errors,
                    )
                else:
                    logger.info(
                        "Scan complete: 0 new signals (%d symbols, %d errors)",
                        result.symbols_scanned, result.errors,
                    )

            except Exception as e:
                logger.error("❌ 扫描周期错误: %s", e, exc_info=True)

    async def stop(self):
        self.running = False

    def add_sl_cooldown(self, symbol: str) -> None:
        """S: Block symbol from re-entering today after a stop-loss.

        Adds the symbol to both _sl_cooldown and _seen_signals for today
        so the next scan cycle won't re-queue it.  Resets at midnight UTC
        together with the daily cache.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        key = f"{symbol}:{today}"
        self._sl_cooldown.add(key)
        self._seen_signals.add(key)
        logger.info("🛎️ SL冷却: %s 今日不再入场", symbol)

    # ------------------------------------------------------------------
    # Core Scan Logic
    # ------------------------------------------------------------------

    async def scan_current_hour(self) -> LiveScanResult:
        """Scan all USDT pairs for the current hour."""
        now = datetime.now(timezone.utc)
        result = LiveScanResult(timestamp=now, signals=[])

        # 0. Invalidate daily cache + symbol list if UTC date changed
        self._refresh_daily_cache_if_needed(now)

        # 1. Fetch tradeable USDT symbols (cached daily; refreshed in _refresh_daily_cache_if_needed)
        symbols = await self._get_usdt_symbols()
        result.symbols_scanned = len(symbols)

        # 2. Scan each symbol (with concurrency control)
        # Binance kline weight=5 per call, rate limit 2400/min (40/s).
        # With 542 symbols × 2 calls = 1084 calls, we need ~27s to stay under limit.
        # Reduce concurrency to 2 and add small delay between batches.
        semaphore = asyncio.Semaphore(2)  # Reduced from 3 to 2
        batch_delay = 0.05  # 50ms delay between requests

        async def scan_one(symbol: str):
            async with semaphore:
                try:
                    signal = await self._scan_symbol(symbol, now)
                    if signal:
                        result.signals.append(signal)
                    # Small delay to avoid rate limit
                    await asyncio.sleep(batch_delay)
                except Exception as e:
                    logger.debug("Error scanning %s: %s", symbol, e)
                    result.errors += 1

        await asyncio.gather(*[scan_one(s) for s in symbols])
        return result

    async def _scan_symbol(
        self,
        symbol: str,
        now: datetime,
    ) -> Optional[SurgeSignal]:
        """Scan a single symbol for surge signal (mirrors SurgeScanner._scan_symbol)."""
        # ── Yesterday's avg hourly sell volume (cached) ──────────────
        y_avg_hour_sell = await self._get_yesterday_avg_sell(symbol, now)
        if y_avg_hour_sell is None or y_avg_hour_sell <= 0:
            return None

        # ── Latest completed hourly kline ────────────────────────────
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)
        prev_hour_start_ms = int((current_hour_start - timedelta(hours=1)).timestamp() * 1000)
        current_hour_start_ms = int(current_hour_start.timestamp() * 1000)

        hourly_klines = await self.client.get_klines(
            symbol=symbol, interval="1h",
            start_time=prev_hour_start_ms, end_time=current_hour_start_ms,
            limit=1,
        )
        if not hourly_klines:
            return None

        hk = hourly_klines[0]
        h_vol = float(hk.volume)
        h_buy_vol = float(hk.taker_buy_base_volume)
        h_sell_vol = h_vol - h_buy_vol
        price = float(hk.close)

        if h_sell_vol <= 0:
            return None

        ratio = h_sell_vol / y_avg_hour_sell

        if self.config.surge_threshold <= ratio <= self.config.surge_max_multiple:
            signal_dt = datetime.fromtimestamp(hk.open_time / 1000, tz=timezone.utc)
            return SurgeSignal(
                symbol=symbol,
                signal_date=signal_dt,
                surge_ratio=ratio,
                price=price,
                yesterday_avg_sell_vol=y_avg_hour_sell,
                hourly_sell_vol=h_sell_vol,
            )

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_yesterday_avg_sell(self, symbol: str, now: datetime) -> Optional[float]:
        """Return yesterday's avg hourly sell volume, with daily cache."""
        cached = self._daily_cache.get(symbol)
        if cached is not None:
            return cached

        yesterday = now - timedelta(days=1)
        y_start_ms = int(
            yesterday.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        )
        y_end_ms = y_start_ms + 86400_000

        daily_klines = await self.client.get_klines(
            symbol=symbol, interval="1d",
            start_time=y_start_ms, end_time=y_end_ms, limit=1,
        )
        if not daily_klines:
            return None

        dk = daily_klines[0]
        y_total_vol = float(dk.volume)
        y_buy_vol = float(dk.taker_buy_base_volume)
        y_sell_vol = y_total_vol - y_buy_vol
        y_avg_hour_sell = y_sell_vol / 24.0

        # Cache the result (even if <= 0, to avoid re-fetching)
        self._daily_cache[symbol] = y_avg_hour_sell
        return y_avg_hour_sell

    def _refresh_daily_cache_if_needed(self, now: datetime) -> None:
        """Invalidate caches when UTC date changes.

        Clears:
        - daily kline cache (y_avg_hour_sell values)
        - _seen_signals dedup set (allow re-signalling on a new day)
        - _usdt_symbols list (pick up new listings / delistings)
        """
        today = now.date()
        if self._daily_cache_date != today:
            if self._daily_cache:
                logger.info(
                    "UTC date changed to %s — clearing daily cache (%d entries), "
                    "dedup set (%d entries), symbol list",
                    today, len(self._daily_cache), len(self._seen_signals),
                )
            self._daily_cache.clear()
            self._seen_signals.clear()   # E: reset dedup so new-day signals aren't blocked
            self._usdt_symbols = None    # B: refresh symbol list once per day
            self._daily_cache_date = today

    async def _get_usdt_symbols(self) -> list[str]:
        """Get all tradeable USDT-margined perpetual symbols."""
        if self._usdt_symbols is not None:
            return self._usdt_symbols

        info = await self.client.get_exchange_info()
        self._usdt_symbols = [
            s.symbol for s in info.symbols
            if s.quote_asset == "USDT"
            and s.contract_type.value == "PERPETUAL"
            and s.status.value == "TRADING"
        ]
        logger.info("Found %d tradeable USDT perpetual symbols", len(self._usdt_symbols))
        return self._usdt_symbols

    def clear_dedup_cache(self):
        """Clear seen signals (call at midnight UTC for daily reset)."""
        self._seen_signals.clear()
