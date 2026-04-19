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
    strategy_id: str = "r24"


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
    is_paper: bool = False


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
    risk_metrics_json TEXT NOT NULL DEFAULT '{}',
    strategy_id TEXT NOT NULL DEFAULT 'r24'
);

CREATE TABLE IF NOT EXISTS position_attribution (
    symbol TEXT NOT NULL,
    position_side TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, position_side)
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

-- Daily balance snapshots for daily report comparison
CREATE TABLE IF NOT EXISTS balance_snapshots (
    date TEXT PRIMARY KEY,
    total_balance REAL NOT NULL,
    unrealized_pnl REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Open simulated positions (paper trading); survives restart
CREATE TABLE IF NOT EXISTS paper_positions (
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL DEFAULT 'r24',
    side TEXT NOT NULL,
    entry_order_id INTEGER NOT NULL,
    quantity TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    margin_usdt TEXT NOT NULL,
    leverage INTEGER NOT NULL,
    tp_pct REAL NOT NULL,
    sl_pct REAL NOT NULL,
    current_tp_pct REAL NOT NULL,
    entry_fill_time TEXT NOT NULL,
    has_added_position INTEGER NOT NULL DEFAULT 0,
    lowest_price TEXT,
    deferred_tp_sl_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, strategy_id)
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
        self._migrate_schema()

    def close(self):
        with self._lock:
            self._conn.close()

    def _migrate_schema(self) -> None:
        """Add columns/tables created after older installs."""
        with self._lock:
            row_factory = self._conn.row_factory
            self._conn.row_factory = None
            try:
                cols = {
                    r[1]
                    for r in self._conn.execute(
                        "PRAGMA table_info(signal_events)"
                    ).fetchall()
                }
                if "strategy_id" not in cols:
                    self._conn.execute(
                        "ALTER TABLE signal_events ADD COLUMN strategy_id "
                        "TEXT NOT NULL DEFAULT 'r24'"
                    )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS position_attribution (
                        symbol TEXT NOT NULL,
                        position_side TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (symbol, position_side)
                    )
                    """
                )
                lt_cols = {
                    r[1]
                    for r in self._conn.execute("PRAGMA table_info(live_trades)").fetchall()
                }
                if "is_paper" not in lt_cols:
                    self._conn.execute(
                        "ALTER TABLE live_trades ADD COLUMN is_paper INTEGER NOT NULL DEFAULT 0"
                    )
            finally:
                self._conn.row_factory = row_factory
            self._conn.commit()

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
            if not d.get("strategy_id"):
                d["strategy_id"] = "r24"
            result.append(SignalEvent(**d))
        return result

    # ── Live Trades ───────────────────────────────────────────────────

    def save_live_trade(self, trade: LiveTrade):
        d = asdict(trade)
        if not d.get("timestamp"):
            d["timestamp"] = utc_now().isoformat()
        d["is_paper"] = int(d.get("is_paper", False))
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        with self._lock:
            self._conn.execute(
                f"INSERT INTO live_trades ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
            self._conn.commit()

    def get_latest_entry_timestamp_iso(self, symbol: str, side: str) -> Optional[str]:
        """Most recent `live_trades` row with event=entry for symbol+side.

        Used to backfill chart entry time when the exchange monitor has no fill time.
        """
        sym = symbol.upper()
        sd = side.upper()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT timestamp FROM live_trades
                WHERE symbol = ? AND event = 'entry' AND UPPER(side) = ?
                ORDER BY id DESC LIMIT 1
                """,
                (sym, sd),
            ).fetchone()
        if row is None or not row["timestamp"]:
            return None
        return str(row["timestamp"]).strip() or None

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
        out = []
        for row in rows:
            d = {k: row[k] for k in cols}
            if "is_paper" in d:
                d["is_paper"] = bool(d["is_paper"])
            out.append(LiveTrade(**d))
        return out

    # ── Paper trading (simulated positions) ───────────────────────────

    def paper_open_symbols(self) -> set[str]:
        """Symbols with an open paper position (any strategy)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT symbol FROM paper_positions"
            ).fetchall()
        return {str(r["symbol"]) for r in rows}

    def paper_open_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM paper_positions"
            ).fetchone()
        return int(row["c"]) if row else 0

    def has_paper_position(self, symbol: str, strategy_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM paper_positions WHERE symbol = ? AND strategy_id = ?",
                (symbol, strategy_id),
            ).fetchone()
        return row is not None

    def upsert_paper_position(
        self,
        *,
        symbol: str,
        strategy_id: str,
        side: str,
        entry_order_id: int,
        quantity: str,
        entry_price: str,
        margin_usdt: str,
        leverage: int,
        tp_pct: float,
        sl_pct: float,
        current_tp_pct: float,
        entry_fill_time: str,
        has_added_position: bool,
        lowest_price: str | None,
        deferred_tp_sl_json: str,
    ) -> None:
        now = utc_now().isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO paper_positions (
                    symbol, strategy_id, side, entry_order_id, quantity,
                    entry_price, margin_usdt, leverage, tp_pct, sl_pct,
                    current_tp_pct, entry_fill_time, has_added_position,
                    lowest_price, deferred_tp_sl_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, strategy_id) DO UPDATE SET
                    entry_order_id = excluded.entry_order_id,
                    quantity = excluded.quantity,
                    entry_price = excluded.entry_price,
                    margin_usdt = excluded.margin_usdt,
                    leverage = excluded.leverage,
                    tp_pct = excluded.tp_pct,
                    sl_pct = excluded.sl_pct,
                    current_tp_pct = excluded.current_tp_pct,
                    entry_fill_time = excluded.entry_fill_time,
                    has_added_position = excluded.has_added_position,
                    lowest_price = excluded.lowest_price,
                    deferred_tp_sl_json = excluded.deferred_tp_sl_json
                """,
                (
                    symbol,
                    strategy_id,
                    side,
                    entry_order_id,
                    quantity,
                    entry_price,
                    margin_usdt,
                    leverage,
                    tp_pct,
                    sl_pct,
                    current_tp_pct,
                    entry_fill_time,
                    int(has_added_position),
                    lowest_price or "",
                    deferred_tp_sl_json,
                    now,
                ),
            )
            self._conn.commit()

    def delete_paper_position(self, symbol: str, strategy_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM paper_positions WHERE symbol = ? AND strategy_id = ?",
                (symbol, strategy_id),
            )
            self._conn.commit()

    def list_paper_positions(self) -> list[dict]:
        """All rows from ``paper_positions`` for recovery."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM paper_positions").fetchall()
        return [dict(r) for r in rows]

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

    # ── Position ↔ strategy attribution (multi-strategy) ────────────

    def upsert_position_attribution(
        self, symbol: str, position_side: str, strategy_id: str
    ) -> None:
        """Record which logical strategy owns an open position (symbol + side)."""
        now = utc_now().isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO position_attribution
                    (symbol, position_side, strategy_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, position_side) DO UPDATE SET
                    strategy_id = excluded.strategy_id,
                    updated_at = excluded.updated_at
                """,
                (symbol, position_side, strategy_id, now),
            )
            self._conn.commit()

    def get_position_attribution(
        self, symbol: str, position_side: str
    ) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT strategy_id FROM position_attribution "
                "WHERE symbol = ? AND position_side = ?",
                (symbol, position_side),
            ).fetchone()
        return str(row["strategy_id"]) if row else None

    def delete_position_attribution(self, symbol: str, position_side: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM position_attribution WHERE symbol = ? AND position_side = ?",
                (symbol, position_side),
            )
            self._conn.commit()

    # ── Balance Snapshots ──────────────────────────────────────────────

    def save_balance_snapshot(self, date: str, total_balance: float, unrealized_pnl: float = 0) -> None:
        """Save or update a daily balance snapshot (upsert by date)."""
        # SQLite 不接受 Decimal；余额来自交易所常为 Decimal
        tb = float(total_balance)
        up = float(unrealized_pnl)
        with self._lock:
            self._conn.execute(
                """INSERT INTO balance_snapshots (date, total_balance, unrealized_pnl)
                   VALUES (?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                     total_balance = excluded.total_balance,
                     unrealized_pnl = excluded.unrealized_pnl""",
                (date, tb, up),
            )
            self._conn.commit()

    def get_yesterday_balance(self, today: str) -> float | None:
        """Get the most recent balance snapshot before today."""
        row = self._conn.execute(
            "SELECT total_balance FROM balance_snapshots WHERE date < ? ORDER BY date DESC LIMIT 1",
            (today,),
        ).fetchone()
        return row["total_balance"] if row else None
