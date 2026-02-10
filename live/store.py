"""SQLite persistence for live trading.

Stores signal events and live trade records.
Designed for crash-recovery: all state is persisted so the trader
can resume after restart.
"""

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .models import utc_now


# ──────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SignalEvent:
    """A signal detection event + risk filter result."""
    timestamp: str
    symbol: str
    surge_ratio: float
    price: str
    accepted: bool
    reject_reason: str = ""
    risk_metrics_json: str = "{}"


@dataclass
class LiveTrade:
    """A live trade event record."""
    symbol: str
    side: str           # LONG / SHORT
    event: str          # entry / tp / sl / timeout / close
    entry_price: str
    exit_price: str = ""
    quantity: str = ""
    margin_usdt: str = ""
    leverage: int = 4
    pnl_usdt: str = ""
    pnl_pct: str = ""
    order_id: str = ""
    algo_id: str = ""
    timestamp: str = ""


# ──────────────────────────────────────────────────────────────────────
# Store
# ──────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    surge_ratio REAL NOT NULL,
    price TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    reject_reason TEXT NOT NULL DEFAULT '',
    risk_metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS live_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    event TEXT NOT NULL,
    entry_price TEXT NOT NULL DEFAULT '',
    exit_price TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '',
    margin_usdt TEXT NOT NULL DEFAULT '',
    leverage INTEGER NOT NULL DEFAULT 4,
    pnl_usdt TEXT NOT NULL DEFAULT '',
    pnl_pct TEXT NOT NULL DEFAULT '',
    order_id TEXT NOT NULL DEFAULT '',
    algo_id TEXT NOT NULL DEFAULT ''
);
"""


class TradeStore:
    """SQLite-backed persistence for live trading state."""

    def __init__(self, db_path: str = "data/trades.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # ── Signal Events ────────────────────────────────────────────────

    def save_signal_event(self, event: SignalEvent):
        d = asdict(event)
        d["accepted"] = int(d["accepted"])
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        self._conn.execute(
            f"INSERT INTO signal_events ({cols}) VALUES ({placeholders})",
            list(d.values()),
        )
        self._conn.commit()

    def get_signal_events(self, limit: int = 100) -> list[SignalEvent]:
        rows = self._conn.execute(
            "SELECT * FROM signal_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = [c for c in SignalEvent.__dataclass_fields__]
        result = []
        for row in rows:
            d = {k: row[k] for k in cols}
            d["accepted"] = bool(d["accepted"])
            result.append(SignalEvent(**d))
        return result

    # ── Live Trades ───────────────────────────────────────────────────

    def save_live_trade(self, trade: LiveTrade):
        d = asdict(trade)
        if not d.get("timestamp"):
            d["timestamp"] = utc_now().isoformat()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        self._conn.execute(
            f"INSERT INTO live_trades ({cols}) VALUES ({placeholders})",
            list(d.values()),
        )
        self._conn.commit()

    def get_live_trades(self, limit: int = 100) -> list[LiveTrade]:
        rows = self._conn.execute(
            "SELECT * FROM live_trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        cols = [c for c in LiveTrade.__dataclass_fields__]
        return [LiveTrade(**{k: row[k] for k in cols}) for row in rows]
