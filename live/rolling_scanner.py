"""Rolling Live Scanner — 24h rolling top gainer scanner using Binance 24hr Ticker.

Uses a single API call (GET /fapi/v1/ticker/24hr) each scan cycle to find
the top N gainers by 24h price change percentage.

Runs at each UTC hour boundary (same cadence as LiveSurgeScanner).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from rich.console import Console

from .binance_client import BinanceFuturesClient
from .models import SurgeSignal
from .rolling_config import RollingLiveConfig

logger = logging.getLogger(__name__)


def _next_scan_utc(now: datetime, interval_h: int) -> datetime:
    """Next scan time: UTC hour on grid ``0, interval_h, 2*interval_h, ...``, at ``:00:05``."""
    if interval_h < 1:
        interval_h = 1
    base = now.replace(minute=0, second=0, microsecond=0)
    qh = (now.hour // interval_h) * interval_h
    candidate = now.replace(hour=qh, minute=0, second=5, microsecond=0)
    if candidate > now:
        return candidate
    nh = qh + interval_h
    if nh < 24:
        return base.replace(hour=nh, minute=0, second=5, microsecond=0)
    return (base + timedelta(days=1)).replace(
        hour=0, minute=0, second=5, microsecond=0,
    )


class RollingLiveScanner:
    """24h rolling top gainer scanner for live trading.

    Scans all USDT-margined perpetual contracts via Binance 24hr Ticker
    at each UTC hour boundary.

    Detection:
      1. GET /fapi/v1/ticker/24hr → all symbols in one call
      2. Filter: priceChangePercent >= min_pct_chg
      3. Sort descending, take top_n
      4. Emit SurgeSignal for each (reuses surge_ratio field for pct_chg)
    """

    def __init__(
        self,
        config: RollingLiveConfig,
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
        self._usdt_symbols: Optional[set[str]] = None
        # Cooldown tracking: "SYMBOL:YYYY-MM-DD-HH" keys
        self._seen_signals: set[str] = set()
        self._sl_cooldown: set[str] = set()
        self._cache_date: Optional[date] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_forever(self):
        """Main loop: scan at each UTC hour boundary."""
        self.running = True
        logger.info(
            "RollingLiveScanner started (min_pct_chg=%.1f%%, top_n=%d, interval=%dh)",
            self.config.min_pct_chg, self.config.top_n,
            self.config.scan_interval_hours,
        )

        # ── Immediate scan on startup ─────────────────────────
        try:
            self._refresh_cache_if_needed(datetime.now(timezone.utc))
            signals, stats = await self._scan()
            new_signals = 0
            for sig in signals:
                cooldown_key = self._cooldown_key(sig.symbol)
                if cooldown_key not in self._seen_signals:
                    self._seen_signals.add(cooldown_key)
                    await self.signal_queue.put(sig)
                    new_signals += 1
            self._log_scan_summary(new_signals, stats, startup=True)
        except Exception as e:
            logger.error("❌ R24 启动扫描错误: %s", e, exc_info=True)

        while self.running:
            now = datetime.now(timezone.utc)
            interval_h = self.config.scan_interval_hours
            next_scan = _next_scan_utc(now, interval_h)
            wait_seconds = (next_scan - now).total_seconds()
            logger.info(
                "⏳ 下次 R24 定时扫描: %s UTC (约 %.0f 秒后)",
                next_scan.strftime("%Y-%m-%d %H:%M:%S"),
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)

            if not self.running:
                break

            try:
                # Refresh caches on date change
                self._refresh_cache_if_needed(datetime.now(timezone.utc))
                signals, stats = await self._scan()

                new_signals = 0
                for sig in signals:
                    cooldown_key = self._cooldown_key(sig.symbol)
                    if cooldown_key not in self._seen_signals:
                        self._seen_signals.add(cooldown_key)
                        await self.signal_queue.put(sig)
                        new_signals += 1

                self._log_scan_summary(new_signals, stats)

            except Exception as e:
                logger.error("❌ R24 扫描周期错误: %s", e, exc_info=True)

    async def stop(self):
        self.running = False

    def add_sl_cooldown(self, symbol: str) -> None:
        """Block symbol from re-entering after a stop-loss."""
        key = self._cooldown_key(symbol)
        self._sl_cooldown.add(key)
        self._seen_signals.add(key)
        logger.info("🛎️ SL冷却: %s 冷却期内不再入场", symbol)

    # ------------------------------------------------------------------
    # Core Scan Logic
    # ------------------------------------------------------------------

    async def _scan(self) -> tuple[list, dict]:
        """Scan using 24hr ticker — single API call.

        Returns:
            (signals, stats) where stats is a dict with rejection breakdown.
        """
        now = datetime.now(timezone.utc)

        # Ensure symbol list is loaded
        tradeable = await self._get_usdt_symbols()

        # Single API call for all 24hr tickers
        tickers = await self.client.get_24hr_tickers()

        # Filter and sort
        candidates = []
        for t in tickers:
            sym = t.get("symbol", "")
            if sym not in tradeable:
                continue
            pct_chg = float(t.get("priceChangePercent", 0))
            if pct_chg >= self.config.min_pct_chg:
                candidates.append((sym, pct_chg, float(t.get("lastPrice", 0))))

        candidates.sort(key=lambda x: x[1], reverse=True)

        # Track rejection reasons
        stats = {
            "total": len(tradeable),
            "above_threshold": len(candidates),
            "cooldown": 0,
            "sl_cooldown": 0,
            "already_sent": 0,
            "top_detail": [],  # [(sym, pct_chg, status)] for logging
        }

        signals = []
        for i, (sym, pct_chg, price) in enumerate(candidates):
            ck = self._cooldown_key(sym)
            is_top = i < self.config.top_n  # show detail for top_n

            if ck in self._sl_cooldown:
                stats["sl_cooldown"] += 1
                if is_top:
                    stats["top_detail"].append((sym, pct_chg, "SL冷却"))
                continue
            if ck in self._seen_signals:
                stats["already_sent"] += 1
                if is_top:
                    stats["top_detail"].append((sym, pct_chg, "已发送"))
                continue

            if is_top:
                stats["top_detail"].append((sym, pct_chg, "✅ new"))
            signals.append(SurgeSignal(
                symbol=sym,
                signal_date=now,
                surge_ratio=pct_chg,  # reuse field for pct_chg
                price=price,
                yesterday_avg_sell_vol=0.0,  # not used in rolling
                hourly_sell_vol=0.0,
            ))

        # Show runners-up (next few after top_n) for context
        top_n = self.config.top_n
        runners_up_start = top_n
        runners_up = []
        count = 0
        for sym, pct, _ in candidates[runners_up_start:]:
            if count >= 4:
                break
            ck = self._cooldown_key(sym)
            if ck not in self._seen_signals and ck not in self._sl_cooldown:
                runners_up.append((sym, pct))
                count += 1
        stats["runners_up"] = runners_up

        return signals, stats

    def _log_scan_summary(self, new_signals: int, stats: dict, startup: bool = False) -> None:
        """Log scan summary with top candidates breakdown."""
        prefix = "📡 R24 启动扫描" if startup else "📡 R24 扫描"

        # Summary line
        parts = [
            f"{stats['total']}币",
            f"{stats['above_threshold']} ≥{self.config.min_pct_chg}%",
        ]
        if stats["already_sent"] > 0:
            parts.append(f"{stats['already_sent']} 已发送")
        if stats["sl_cooldown"] > 0:
            parts.append(f"{stats['sl_cooldown']} SL冷却")
        parts.append(f"{new_signals} new")
        logger.info("%s: %s", prefix, " | ".join(parts))

        # Top candidates detail
        top_detail = stats.get("top_detail", [])
        if top_detail:
            for sym, pct, status in top_detail:
                logger.info("  #1 %s +%.1f%% → %s", sym, pct, status)
        elif stats["above_threshold"] == 0:
            logger.info("  无币种达到涨幅阈值 %.0f%%", self.config.min_pct_chg)

        # Runners-up for context
        runners = stats.get("runners_up", [])
        if runners:
            ru_str = ", ".join(f"{s} +{p:.1f}%" for s, p in runners)
            logger.info("  候选: %s", ru_str)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cooldown_key(self, symbol: str) -> str:
        """Generate cooldown key based on cooldown window."""
        now = datetime.now(timezone.utc)
        # Use date + hour bucket for hourly cooldown tracking
        # For 24h cooldown, all hours on same day share same key
        if self.config.signal_cooldown_hours >= 24:
            return f"{symbol}:{now.strftime('%Y-%m-%d')}"
        # For shorter cooldowns, use hour-aligned bucket
        bucket = now.hour // self.config.signal_cooldown_hours
        return f"{symbol}:{now.strftime('%Y-%m-%d')}:{bucket}"

    def _refresh_cache_if_needed(self, now: datetime) -> None:
        """Invalidate caches when UTC date changes."""
        today = now.date()
        if self._cache_date != today:
            if self._seen_signals:
                logger.info(
                    "UTC date changed to %s — clearing dedup set (%d), symbol list",
                    today, len(self._seen_signals),
                )
            self._seen_signals.clear()
            self._sl_cooldown.clear()
            self._usdt_symbols = None
            self._cache_date = today

    async def _get_usdt_symbols(self) -> set[str]:
        """Get all tradeable USDT-margined perpetual symbols (cached daily)."""
        if self._usdt_symbols is not None:
            return self._usdt_symbols

        info = await self.client.get_exchange_info()
        self._usdt_symbols = {
            s.symbol for s in info.symbols
            if s.quote_asset == "USDT"
            and s.contract_type.value == "PERPETUAL"
            and s.status.value == "TRADING"
        }
        logger.info("Found %d tradeable USDT perpetual symbols", len(self._usdt_symbols))
        return self._usdt_symbols

    def clear_dedup_cache(self):
        """Clear seen signals (compatibility with LiveSurgeScanner interface)."""
        self._seen_signals.clear()
