"""R24 raw-surge：与 moonshot RawSurgeRollingStrategy.select_signals 等价的筛选（无 moonshot 依赖）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .rolling_config import RollingLiveConfig


class _ListingFeed(Protocol):
    def load_listing_date(self, symbol: str) -> datetime | None: ...


@dataclass
class RawSurgeFeedAdapter:
    """同步适配层：供 select_raw_surge_signals 查询上市日与卖量详情。"""

    _listing: dict[str, datetime | None] = field(default_factory=dict)
    _sell: dict[tuple[str, str], tuple[float | None, float | None]] = field(default_factory=dict)

    def set_listing_date(self, symbol: str, dt: datetime | None) -> None:
        self._listing[symbol] = dt

    def set_sell_surge_detail(
        self, symbol: str, hour_dt: datetime, sr: float | None, yavg: float | None,
    ) -> None:
        key = (symbol, hour_dt.strftime("%Y-%m-%d %H:00"))
        self._sell[key] = (sr, yavg)

    def load_listing_date(self, symbol: str) -> datetime | None:
        return self._listing.get(symbol)

    def load_sell_surge_detail(self, symbol: str, dt: datetime) -> tuple[float | None, float | None]:
        key = (symbol, dt.strftime("%Y-%m-%d %H:00"))
        return self._sell.get(key, (None, None))


def select_raw_surge_signals(
    cfg: RollingLiveConfig,
    feed: _ListingFeed,
    dt: datetime,
    preloaded_gainers: dict[str, list[tuple]] | None,
) -> tuple[list[tuple[str, float]], list[dict[str, Any]]]:
    """返回 (通过筛选的 (symbol, pct_chg) 列表, last_signal_details 明细)。"""
    last_signal_details: list[dict[str, Any]] = []
    key = dt.strftime("%Y-%m-%d %H:00")
    raw = preloaded_gainers.get(key, []) if preloaded_gainers else []

    rows: list[tuple] = []
    for item in raw:
        if len(item) >= 4:
            rows.append((item[0], item[1], item[2], item[3]))
        else:
            rows.append((item[0], item[1], None, None))

    if cfg.raw_max_signals_per_hour is not None:
        rows = rows[: cfg.raw_max_signals_per_hour]

    results: list[tuple[str, float]] = []
    for symbol, pct_chg, pre_sr, pre_yavg in rows:
        detail: dict[str, Any] = {
            "symbol": symbol,
            "pct_chg": pct_chg,
            "listed_days": None,
            "filter_result": "通过",
            "sell_surge_ratio": None,
            "yesterday_avg_hour_sell_volume": None,
        }

        if pct_chg < cfg.min_pct_chg:
            detail["filter_result"] = "剔除:涨幅不达标"
            last_signal_details.append(detail)
            continue

        if cfg.min_listed_days > 0:
            listing_date = feed.load_listing_date(symbol)
            if listing_date is not None:
                days_listed = (dt - listing_date).days
                detail["listed_days"] = days_listed
                if days_listed < cfg.min_listed_days:
                    detail["filter_result"] = "剔除:上市天数不足"
                    last_signal_details.append(detail)
                    continue

        if pre_sr is not None:
            detail["sell_surge_ratio"] = pre_sr
            detail["yesterday_avg_hour_sell_volume"] = pre_yavg
        else:
            loader = getattr(feed, "load_sell_surge_detail", None)
            if loader is not None:
                sr, yavg = loader(symbol, dt)
                detail["sell_surge_ratio"] = sr
                detail["yesterday_avg_hour_sell_volume"] = yavg

        if cfg.enable_sell_surge_gate:
            if detail.get("sell_surge_ratio") is None:
                loader = getattr(feed, "load_sell_surge_detail", None)
                if loader is None:
                    detail["filter_result"] = "剔除:卖量门控需要 feed"
                    last_signal_details.append(detail)
                    continue
                sr, yavg = loader(symbol, dt)
                detail["sell_surge_ratio"] = sr
                detail["yesterday_avg_hour_sell_volume"] = yavg

            sr = detail.get("sell_surge_ratio")
            if sr is None:
                detail["filter_result"] = "剔除:卖量数据不足"
                last_signal_details.append(detail)
                continue
            if sr < cfg.sell_surge_threshold:
                detail["filter_result"] = "剔除:卖量未暴涨"
                last_signal_details.append(detail)
                continue
            if sr > cfg.sell_surge_max:
                detail["filter_result"] = "剔除:卖量倍数过高"
                last_signal_details.append(detail)
                continue

        last_signal_details.append(detail)
        results.append((symbol, pct_chg))

    return results, last_signal_details
