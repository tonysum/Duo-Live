"""Core models and utilities — inlined from backend.scanners.surge + backend.core.time_utils."""

from dataclasses import dataclass
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Time utils (from backend.core.time_utils)
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


def from_timestamp_ms(ts_ms: int | float) -> datetime:
    """Convert milliseconds timestamp to UTC datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# SurgeSignal (from backend.scanners.surge)
# ---------------------------------------------------------------------------

@dataclass
class SurgeSignal:
    """A detected surge signal."""

    symbol: str
    signal_date: datetime
    surge_ratio: float
    price: float
    yesterday_avg_sell_vol: float
    hourly_sell_vol: float
    status: str = "pending"
    note: str = ""

    @property
    def signal_time(self) -> str:
        """Return time portion as HH:MM:SS."""
        return self.signal_date.strftime("%H:%M:%S")

    @property
    def signal_date_str(self) -> str:
        """Return full datetime string."""
        return self.signal_date.strftime("%Y-%m-%d %H:%M:%S")
