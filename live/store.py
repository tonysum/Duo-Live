"""SQLite persistence for live trading.

Stores signal events and live trade records.
Designed for crash-recovery: all state is persisted so the trader
can resume after restart.
"""

import sqlite3
import threading
from dataclasses import asdict, dataclass
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
# Schema
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

-- Persists per-position TP/strength state so crash-recovery reads it back
CREATE TABLE IF NOT EXISTS position_state (
    symbol TEXT PRIMARY KEY,
    current_tp_pct REAL NOT NULL,
    strength TEXT NOT NULL DEFAULT 'unknown',
    evaluated_2h INTEGER NOT NULL DEFAULT 0,
    evaluated_12h INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


# ──────────────────────────────────────────────────────────────────────
# Store
# ──────────────────────────────────────────────────────────────────────

class TradeStore:
    """SQLite-backed persistence for live trading state.

    Thread-safe: all DB access is serialised via self._lock.
    check_same_thread=False is intentional and safe because _lock
    ensures only one thread accesses the connection at a time.
    """

    def __init__(self, db_path: str = "data/trades.db"):
        self._lock = threading.Lock()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ── Signal Events ────────────────────────────────────────────────

    def save_signal_event(self, event: SignalEvent):
        d = asdict(event)
        d["accepted"] = int(d["accepted"])
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        with self._lock:
            self._conn.execute(
                f"INSERT INTO signal_events ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
            self._conn.commit()

    def get_signal_events(self, limit: int = 100) -> list[SignalEvent]:
        cols = [c for c in SignalEvent.__dataclass_fields__]
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM signal_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
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
        with self._lock:
            self._conn.execute(
                f"INSERT INTO live_trades ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
            self._conn.commit()

    def get_live_trades(
        self, limit: int = 100, since_date: Optional[str] = None
    ) -> list[LiveTrade]:
        """Get live trades ordered by most recent first.

        Args:
            limit: Max rows to return.
            since_date: Optional UTC date string 'YYYY-MM-DD'. Only rows
                with timestamp >= this date are included (pushed to SQL).
        """
        cols = [c for c in LiveTrade.__dataclass_fields__]
        with self._lock:
            if since_date:
                rows = self._conn.execute(
                    "SELECT * FROM live_trades WHERE timestamp >= ? ORDER BY id DESC LIMIT ?",
                    (since_date, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM live_trades ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [LiveTrade(**{k: row[k] for k in cols}) for row in rows]

    # ── Position State (crash-recovery for TP pct / strength) ────────

    def save_position_state(
        self,
        symbol: str,
        current_tp_pct: float,
        strength: str,
        evaluated_2h: bool,
        evaluated_12h: bool,
    ) -> None:
        """Upsert TP/strength state so it survives process restarts."""
        now = utc_now().isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO position_state
                    (symbol, current_tp_pct, strength, evaluated_2h, evaluated_12h, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    current_tp_pct = excluded.current_tp_pct,
                    strength       = excluded.strength,
                    evaluated_2h   = excluded.evaluated_2h,
                    evaluated_12h  = excluded.evaluated_12h,
                    updated_at     = excluded.updated_at
                """,
                (symbol, current_tp_pct, strength, int(evaluated_2h), int(evaluated_12h), now),
            )
            self._conn.commit()

    def get_position_state(self, symbol: str) -> Optional[dict]:
        """Return saved TP/strength state for a symbol, or None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM position_state WHERE symbol = ?", (symbol,)
            ).fetchone()
        if row is None:
            return None
        return {
            "current_tp_pct": row["current_tp_pct"],
            "strength": row["strength"],
            "evaluated_2h": bool(row["evaluated_2h"]),
            "evaluated_12h": bool(row["evaluated_12h"]),
        }

    def delete_position_state(self, symbol: str) -> None:
        """Remove position state when a position is fully closed."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM position_state WHERE symbol = ?", (symbol,)
            )
            self._conn.commit()
