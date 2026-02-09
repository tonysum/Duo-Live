"""SQLite persistence for paper trading.

Stores open positions, closed trades, equity snapshots, and signal events.
Designed for crash-recovery: all state is persisted so the paper trader
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
# Data classes for the store (lightweight, serialisable)
# ──────────────────────────────────────────────────────────────────────

@dataclass
class PaperPosition:
    """An open paper-trade position (mirrors PositionStateV2 essentials)."""
    symbol: str
    side: str  # "short"
    entry_price: str  # stored as string for Decimal fidelity
    entry_time: str    # ISO-8601 UTC
    size: str
    margin: str
    leverage: int
    signal_price: str
    signal_time: str
    signal_surge_ratio: float
    tp_pct: float
    strength: str = "strong"  # strong / medium / weak
    status: str = "normal"    # normal / observing / virtual_tracking
    max_price: str = "0"
    min_price: str = "999999999"
    evaluated_2h: bool = False
    evaluated_12h: bool = False
    checked_2h_early_stop: bool = False
    checked_12h_early_stop: bool = False
    # Observing fields
    observing_since: Optional[str] = None
    observing_entry_price: Optional[str] = None
    capital_already_returned: bool = False
    # Virtual tracking
    is_virtual_tracking: bool = False
    virtual_entry_price: Optional[str] = None
    # Extra signal data
    signal_data_json: str = "{}"


@dataclass
class PaperTrade:
    """A completed paper trade (mirrors TradeRecordV2)."""
    symbol: str
    side: str
    signal_time: str
    signal_price: str
    entry_time: str
    exit_time: str
    entry_price: str
    exit_price: str
    size: str
    pnl: str
    pnl_pct: str
    exit_reason: str
    hold_hours: float
    signal_surge_ratio: float
    coin_strength: str
    status_at_exit: str
    tp_pct_used: float


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
CREATE TABLE IF NOT EXISTS paper_positions (
    symbol TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    size TEXT NOT NULL,
    margin TEXT NOT NULL,
    leverage INTEGER NOT NULL,
    signal_price TEXT NOT NULL,
    signal_time TEXT NOT NULL,
    signal_surge_ratio REAL NOT NULL,
    tp_pct REAL NOT NULL,
    strength TEXT NOT NULL DEFAULT 'strong',
    status TEXT NOT NULL DEFAULT 'normal',
    max_price TEXT NOT NULL DEFAULT '0',
    min_price TEXT NOT NULL DEFAULT '999999999',
    evaluated_2h INTEGER NOT NULL DEFAULT 0,
    evaluated_12h INTEGER NOT NULL DEFAULT 0,
    checked_2h_early_stop INTEGER NOT NULL DEFAULT 0,
    checked_12h_early_stop INTEGER NOT NULL DEFAULT 0,
    observing_since TEXT,
    observing_entry_price TEXT,
    capital_already_returned INTEGER NOT NULL DEFAULT 0,
    is_virtual_tracking INTEGER NOT NULL DEFAULT 0,
    virtual_entry_price TEXT,
    signal_data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    signal_time TEXT NOT NULL,
    signal_price TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    exit_price TEXT NOT NULL,
    size TEXT NOT NULL,
    pnl TEXT NOT NULL,
    pnl_pct TEXT NOT NULL,
    exit_reason TEXT NOT NULL,
    hold_hours REAL NOT NULL,
    signal_surge_ratio REAL NOT NULL,
    coin_strength TEXT NOT NULL,
    status_at_exit TEXT NOT NULL DEFAULT 'normal',
    tp_pct_used REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    equity TEXT NOT NULL,
    cash TEXT NOT NULL,
    open_positions INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paper_signal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    surge_ratio REAL NOT NULL,
    price TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    reject_reason TEXT NOT NULL DEFAULT '',
    risk_metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS paper_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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


class PaperStore:
    """SQLite-backed persistence for paper trading state."""

    def __init__(self, db_path: str = "data/paper_trades.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # ── State (capital, etc.) ──────────────────────────────────────────

    def get_state(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM paper_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str):
        self._conn.execute(
            "INSERT OR REPLACE INTO paper_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    # ── Positions ────────────────────────────────────────────────────

    def save_position(self, pos: PaperPosition):
        d = asdict(pos)
        for k in ("evaluated_2h", "evaluated_12h", "checked_2h_early_stop",
                   "checked_12h_early_stop", "capital_already_returned",
                   "is_virtual_tracking"):
            d[k] = int(d[k])
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        self._conn.execute(
            f"INSERT OR REPLACE INTO paper_positions ({cols}) VALUES ({placeholders})",
            list(d.values()),
        )
        self._conn.commit()

    def get_open_positions(self) -> list[PaperPosition]:
        rows = self._conn.execute("SELECT * FROM paper_positions").fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for k in ("evaluated_2h", "evaluated_12h", "checked_2h_early_stop",
                       "checked_12h_early_stop", "capital_already_returned",
                       "is_virtual_tracking"):
                d[k] = bool(d[k])
            result.append(PaperPosition(**d))
        return result

    def get_position(self, symbol: str) -> Optional[PaperPosition]:
        row = self._conn.execute(
            "SELECT * FROM paper_positions WHERE symbol = ?", (symbol,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("evaluated_2h", "evaluated_12h", "checked_2h_early_stop",
                   "checked_12h_early_stop", "capital_already_returned",
                   "is_virtual_tracking"):
            d[k] = bool(d[k])
        return PaperPosition(**d)

    def remove_position(self, symbol: str):
        self._conn.execute(
            "DELETE FROM paper_positions WHERE symbol = ?", (symbol,)
        )
        self._conn.commit()

    def position_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_positions"
        ).fetchone()
        return row["cnt"]

    # ── Trades ───────────────────────────────────────────────────────

    def save_trade(self, trade: PaperTrade):
        d = asdict(trade)
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        self._conn.execute(
            f"INSERT INTO paper_trades ({cols}) VALUES ({placeholders})",
            list(d.values()),
        )
        self._conn.commit()

    def get_trades(self, limit: int = 100) -> list[PaperTrade]:
        rows = self._conn.execute(
            "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        cols = [c for c in PaperTrade.__dataclass_fields__]
        return [PaperTrade(**{k: row[k] for k in cols}) for row in rows]

    def get_trade_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_trades"
        ).fetchone()
        return row["cnt"]

    # ── Equity Snapshots ─────────────────────────────────────────────

    def save_equity_snapshot(
        self,
        equity: Decimal,
        cash: Decimal,
        open_positions: int,
        timestamp: Optional[str] = None,
    ):
        ts = timestamp or utc_now().isoformat()
        self._conn.execute(
            "INSERT INTO paper_equity_snapshots "
            "(timestamp, equity, cash, open_positions) VALUES (?, ?, ?, ?)",
            (ts, str(equity), str(cash), open_positions),
        )
        self._conn.commit()

    def get_equity_curve(
        self, limit: int = 1000
    ) -> list[tuple[str, Decimal]]:
        rows = self._conn.execute(
            "SELECT timestamp, equity FROM paper_equity_snapshots "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(r["timestamp"], Decimal(r["equity"])) for r in reversed(rows)]

    # ── Signal Events ────────────────────────────────────────────────

    def save_signal_event(self, event: SignalEvent):
        d = asdict(event)
        d["accepted"] = int(d["accepted"])
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        self._conn.execute(
            f"INSERT INTO paper_signal_events ({cols}) VALUES ({placeholders})",
            list(d.values()),
        )
        self._conn.commit()

    def get_signal_events(self, limit: int = 100) -> list[SignalEvent]:
        rows = self._conn.execute(
            "SELECT * FROM paper_signal_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = [c for c in SignalEvent.__dataclass_fields__]
        result = []
        for row in rows:
            d = {k: row[k] for k in cols}
            d["accepted"] = bool(d["accepted"])
            result.append(SignalEvent(**d))
        return result

    # ── Today's entry count ──────────────────────────────────────────

    def get_entries_today(self) -> int:
        """Count trades entered today (UTC)."""
        today = utc_now().strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_trades WHERE entry_time LIKE ?",
            (f"{today}%",),
        ).fetchone()
        pos_today = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM paper_positions WHERE entry_time LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return row["cnt"] + pos_today["cnt"]

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
