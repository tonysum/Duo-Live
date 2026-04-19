"""卖量暴涨比（与 moonshot/paper/live_feed 口径一致）：1h 主动卖出 quote / 昨日日均每小时主动卖出 quote。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from .binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)


async def load_1h_sell_quote(
    client: BinanceFuturesClient,
    symbol: str,
    hour_dt: datetime,
) -> float | None:
    """该 UTC 小时已完成 1h K 的主动卖出成交额（quote）。sell_quote = quote_total - taker_buy_quote。"""
    try:
        h0 = hour_dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        start_ms = int(h0.timestamp() * 1000)
        end_ms = start_ms + 3_600_000
        klines = await client.get_klines(
            symbol=symbol,
            interval="1h",
            start_time=start_ms,
            end_time=end_ms,
            limit=2,
        )
        if not klines:
            return None
        k = klines[0]
        q_total = float(k.quote_asset_volume)
        q_buy = float(k.taker_buy_quote_volume)
        return max(0.0, q_total - q_buy)
    except Exception as e:
        logger.debug("load_1h_sell_quote %s: %s", symbol, e)
        return None


async def load_1d_sell_quote(
    client: BinanceFuturesClient,
    symbol: str,
    day_dt: datetime,
) -> float | None:
    """某自然日（UTC 00:00 起）的已完成 1d K 主动卖出成交额（quote）。"""
    try:
        d0 = day_dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(d0.timestamp() * 1000)
        end_ms = start_ms + 86_400_000
        klines = await client.get_klines(
            symbol=symbol,
            interval="1d",
            start_time=start_ms,
            end_time=end_ms,
            limit=2,
        )
        if not klines:
            return None
        k = klines[0]
        q_total = float(k.quote_asset_volume)
        q_buy = float(k.taker_buy_quote_volume)
        return max(0.0, q_total - q_buy)
    except Exception as e:
        logger.debug("load_1d_sell_quote %s: %s", symbol, e)
        return None


async def sell_surge_ratio_at_hour(
    client: BinanceFuturesClient,
    symbol: str,
    hour_dt: datetime,
) -> tuple[float | None, float | None]:
    """返回 (sell_surge_ratio, yesterday_avg_hour_sell_quote)。"""
    try:
        h0 = hour_dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        sell_1h = await load_1h_sell_quote(client, symbol, h0)
        if sell_1h is None:
            return None, None

        yday0 = (h0 - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sell_1d = await load_1d_sell_quote(client, symbol, yday0)
        if sell_1d is None:
            return None, None

        yavg = sell_1d / 24.0
        if yavg <= 0:
            return None, yavg
        return sell_1h / yavg, yavg
    except Exception as e:
        logger.debug("sell_surge_ratio_at_hour %s: %s", symbol, e)
        return None, None


async def load_listing_date_utc(
    client: BinanceFuturesClient,
    symbol: str,
) -> datetime | None:
    """最早 1d K 开盘时间作为上市日（UTC）。"""
    try:
        klines = await client.get_klines(
            symbol=symbol, interval="1d", limit=1, start_time=0,
        )
        if klines:
            return datetime.fromtimestamp(klines[0].open_time / 1000, tz=UTC)
    except Exception as e:
        logger.debug("load_listing_date_utc %s: %s", symbol, e)
    return None
