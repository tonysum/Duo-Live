"""Rolling Live Scanner — R24 raw-surge（与 paper RawSurgeScanner 语义一致）。

漏斗顺序（与 ``duo-moonshot/moonshot/paper/rolling_scanner.py`` 对齐）：

1. GET /fapi/v1/ticker/24hr → 过滤 ``priceChangePercent >= raw_min_pct_chg``
   得到「涨幅合格集合」（不截断 top_n）。
2. 对集合内每币算 ``sell_surge_ratio_at_hour``，保留 ``sr > raw_min_sell_surge``
   者 → 得到「涨幅 ∩ 卖量 合格集合」。
3. **按 sr 降序** 截 ``top_n`` 作为最终候选（排序依据见
   ``docs/signal-scan-order.md``）。
4. ``select_raw_surge_signals``：``min_pct_chg``、上市天数、可选二次卖量门控。
5. 去重/冷却 → 入队。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from rich.console import Console

from .binance_client import BinanceFuturesClient
from .models import SurgeSignal
from .raw_surge_signal import RawSurgeFeedAdapter, select_raw_surge_signals
from .rolling_config import RollingLiveConfig
from .sell_surge_binance import load_listing_date_utc, sell_surge_ratio_at_hour

logger = logging.getLogger(__name__)


def _next_scan_utc(now: datetime, interval_h: int, delay_minutes: int) -> datetime:
    """下一扫描时刻：UTC 网格 ``0, interval_h, ...`` + ``delay_minutes`` 分 + 5 秒。"""
    if interval_h < 1:
        interval_h = 1
    dm = max(0, min(int(delay_minutes), 59))
    base = now.replace(second=0, microsecond=0)
    qh = (now.hour // interval_h) * interval_h
    candidate = now.replace(hour=qh, minute=dm, second=5, microsecond=0)
    if candidate > now:
        return candidate
    nh = qh + interval_h
    if nh < 24:
        return base.replace(hour=nh, minute=dm, second=5, microsecond=0)
    return (base + timedelta(days=1)).replace(hour=0, minute=dm, second=5, microsecond=0)


class RollingLiveScanner:
    """R24 raw-surge scanner for live trading."""

    def __init__(
        self,
        config: RollingLiveConfig,
        signal_queue: asyncio.Queue,
        client: Optional[BinanceFuturesClient] = None,
        console: Optional[Console] = None,
        strategy_id: Optional[str] = None,
    ):
        self.config = config
        self.signal_queue = signal_queue
        self._strategy_id = strategy_id or getattr(config, "strategy_id", None) or "r24"
        self.client = client or BinanceFuturesClient()
        self.console = console or Console()
        self.running = False

        self._usdt_symbols: Optional[set[str]] = None
        self._seen_signals: set[str] = set()
        self._sl_cooldown: set[str] = set()
        self._cache_date: Optional[date] = None
        self._listing_cache: dict[str, datetime | None] = {}

    def _lp(self, fmt: str, *args) -> None:
        logger.info("[%s] " + fmt, self._strategy_id, *args)

    def _lpe(self, fmt: str, *args, exc_info: bool = False) -> None:
        logger.error("[%s] " + fmt, self._strategy_id, *args, exc_info=exc_info)

    async def run_forever(self) -> None:
        self.running = True
        self._lp(
            "RollingLiveScanner started (R24 raw-surge: raw_min_pct_chg=%.1f%%, raw_min_sr=%.1f, top_n=%d, interval=%dh, delay=%dm)",
            self.config.raw_min_pct_chg,
            self.config.raw_min_sell_surge,
            self.config.top_n,
            self.config.scan_interval_hours,
            self.config.scan_delay_minutes,
        )

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
            self._lpe("❌ R24 启动扫描错误: %s", e, exc_info=True)

        while self.running:
            now = datetime.now(timezone.utc)
            interval_h = self.config.scan_interval_hours
            delay_m = self.config.scan_delay_minutes
            next_scan = _next_scan_utc(now, interval_h, delay_m)
            wait_seconds = (next_scan - now).total_seconds()
            self._lp(
                "⏳ 下次 R24 定时扫描: %s UTC (约 %.0f 秒后)",
                next_scan.strftime("%Y-%m-%d %H:%M:%S"),
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)

            if not self.running:
                break

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

                self._log_scan_summary(new_signals, stats)

            except Exception as e:
                self._lpe("❌ R24 扫描周期错误: %s", e, exc_info=True)

    async def stop(self) -> None:
        self.running = False

    def add_sl_cooldown(self, symbol: str) -> None:
        key = self._cooldown_key(symbol)
        self._sl_cooldown.add(key)
        self._seen_signals.add(key)
        self._lp("🛎️ SL冷却: %s 冷却期内不再入场", symbol)

    async def _scan(self) -> tuple[list[SurgeSignal], dict]:
        now = datetime.now(timezone.utc)
        tradeable = await self._get_usdt_symbols()
        tickers = await self.client.get_24hr_tickers()

        # Step 1: 涨幅合格集合（不截断 top_n；对齐 paper）
        raw_candidates: list[tuple[str, float, float]] = []
        for t in tickers:
            sym = t.get("symbol", "")
            if sym not in tradeable:
                continue
            pct_chg = float(t.get("priceChangePercent", 0))
            if pct_chg >= self.config.raw_min_pct_chg:
                raw_candidates.append((sym, pct_chg, float(t.get("lastPrice", 0))))

        raw_candidates.sort(key=lambda x: x[1], reverse=True)

        # REST 洪水保险：极端行情下涨幅合格币可能上百，每币要发 1h/1d K 线请求。
        # 按涨幅倒序截到 MAX_SR_PROBE 再逐个算 sr。
        max_probe = int(getattr(self.config, "max_sr_probe", 50) or 50)
        probe_list = raw_candidates[:max_probe]

        price_map = {s: p for s, _, p in raw_candidates}

        stats: dict = {
            "total": len(tradeable),
            "raw_candidates": len(raw_candidates),
            "probe": len(probe_list),
            "probe_cap": max_probe,
            "sr_pass": 0,
            "sell_surge_fail": 0,
            "sl_cooldown": 0,
            "already_sent": 0,
            "select_pass": 0,
            "top_detail": [],
        }

        hour_key = now.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:00")
        preloaded: dict[str, list[tuple]] = {hour_key: []}
        adapter = RawSurgeFeedAdapter()

        # Step 2: 卖量门 —— 对探测集里每个币算 sr，收集所有通过者
        sr_passed: list[tuple[str, float, float, float]] = []  # (sym, pct_chg, sr, yavg)
        for sym, pct_chg, _price in probe_list:
            ck = self._cooldown_key(sym)
            if ck in self._sl_cooldown:
                stats["sl_cooldown"] += 1
                continue
            if ck in self._seen_signals:
                stats["already_sent"] += 1
                continue

            sr, yavg = await sell_surge_ratio_at_hour(self.client, sym, now)
            if sr is None or sr <= self.config.raw_min_sell_surge:
                stats["sell_surge_fail"] += 1
                self._lp(
                    "  raw-surge SKIP %s +%.1f%%: sr=%s (need >%.1f)",
                    sym,
                    pct_chg,
                    f"{sr:.2f}" if sr is not None else "None",
                    self.config.raw_min_sell_surge,
                )
                continue

            sr_passed.append((sym, float(pct_chg), float(sr), float(yavg or 0.0)))

        stats["sr_pass"] = len(sr_passed)

        # Step 3: 排序截断 —— 按 sr 降序取 top_n（与 paper RawSurgeScanner 对齐）
        # 将来若要改为「按涨幅降序」或其他策略，参见 docs/signal-scan-order.md。
        sr_passed.sort(key=lambda x: x[2], reverse=True)
        finalists = sr_passed[: self.config.top_n]

        # 装载 adapter / preloaded 供 select_raw_surge_signals 使用
        for sym, pct_chg, sr, yavg in finalists:
            listing = await self._get_listing_cached(sym)
            adapter.set_listing_date(sym, listing)
            adapter.set_sell_surge_detail(sym, now, sr, yavg)
            preloaded[hour_key].append(
                (sym, float(pct_chg), float(sr), float(yavg or 0.0)),
            )

        results, _details = select_raw_surge_signals(self.config, adapter, now, preloaded)
        stats["select_pass"] = len(results)

        signals: list[SurgeSignal] = []
        for sym, pct_chg in results:
            ck = self._cooldown_key(sym)
            if ck in self._seen_signals or ck in self._sl_cooldown:
                continue
            price = price_map.get(sym, 0.0)
            # 从 preloaded 取 sr/yavg
            sr_out: float | None = None
            y_out: float | None = None
            for row in preloaded.get(hour_key, []):
                if row[0] == sym:
                    if len(row) >= 4:
                        sr_out = float(row[2])
                        y_out = float(row[3]) if row[3] is not None else None
                    break

            stats["top_detail"].append((sym, pct_chg, "✅ raw-surge"))
            signals.append(
                SurgeSignal(
                    symbol=sym,
                    signal_date=now,
                    surge_ratio=pct_chg,
                    price=price,
                    yesterday_avg_sell_vol=0.0,
                    hourly_sell_vol=0.0,
                    strategy_id=self._strategy_id,
                    sell_surge_ratio=sr_out,
                    yesterday_avg_hour_sell_quote=y_out,
                ),
            )

        return signals, stats

    async def _get_listing_cached(self, symbol: str) -> datetime | None:
        if symbol in self._listing_cache:
            return self._listing_cache[symbol]
        d = await load_listing_date_utc(self.client, symbol)
        self._listing_cache[symbol] = d
        return d

    def _log_scan_summary(self, new_signals: int, stats: dict, startup: bool = False) -> None:
        prefix = "📡 R24 启动扫描" if startup else "📡 R24 扫描"
        probe = stats.get("probe", 0)
        probe_cap = stats.get("probe_cap")
        probe_label = f"probe:{probe}"
        if probe_cap is not None and stats.get("raw_candidates", 0) > probe_cap:
            probe_label += f"(cap{probe_cap})"
        parts = [
            f"{stats['total']}币",
            f"raw≥{self.config.raw_min_pct_chg}%:{stats['raw_candidates']}",
            probe_label,
            f"sr>{self.config.raw_min_sell_surge:g}:{stats.get('sr_pass', 0)}",
            f"sr_fail:{stats.get('sell_surge_fail', 0)}",
            f"top{self.config.top_n}_select:{stats.get('select_pass', 0)}",
        ]
        if stats.get("already_sent", 0) > 0:
            parts.append(f"已发:{stats['already_sent']}")
        if stats.get("sl_cooldown", 0) > 0:
            parts.append(f"SL冷:{stats['sl_cooldown']}")
        parts.append(f"{new_signals} new")
        self._lp("%s: %s", prefix, " | ".join(parts))

        for rank, (sym, pct, status) in enumerate(stats.get("top_detail", []), start=1):
            self._lp("  #%d %s +%.1f%% → %s", rank, sym, pct, status)

    def _cooldown_key(self, symbol: str) -> str:
        now = datetime.now(timezone.utc)
        if self.config.signal_cooldown_hours >= 24:
            return f"{symbol}:{now.strftime('%Y-%m-%d')}"
        bucket = now.hour // max(1, self.config.signal_cooldown_hours)
        return f"{symbol}:{now.strftime('%Y-%m-%d')}:{bucket}"

    def _refresh_cache_if_needed(self, now: datetime) -> None:
        today = now.date()
        if self._cache_date != today:
            if self._seen_signals:
                self._lp(
                    "UTC date changed to %s — clearing dedup set (%d), symbol list",
                    today,
                    len(self._seen_signals),
                )
            self._seen_signals.clear()
            self._sl_cooldown.clear()
            self._usdt_symbols = None
            self._listing_cache.clear()
            self._cache_date = today

    async def _get_usdt_symbols(self) -> set[str]:
        if self._usdt_symbols is not None:
            return self._usdt_symbols

        info = await self.client.get_exchange_info()
        self._usdt_symbols = {
            s.symbol
            for s in info.symbols
            if s.quote_asset == "USDT"
            and s.contract_type.value == "PERPETUAL"
            and s.status.value == "TRADING"
        }
        self._lp("Found %d tradeable USDT perpetual symbols", len(self._usdt_symbols))
        return self._usdt_symbols

    def clear_dedup_cache(self) -> None:
        self._seen_signals.clear()
