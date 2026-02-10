"""Live trading configuration.

Strategy parameters for the Surge Short V2 live trading system.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class LiveTradingConfig:
    """Configuration for live trading."""

    # ── Capital & Leverage ──────────────────────────────────────────────
    leverage: int = 3
    max_positions: int = 6
    max_entries_per_day: int = 4
    live_fixed_margin_usdt: Decimal = Decimal("5")  # 固定保证金 (USDT/笔)
    daily_loss_limit_usdt: Decimal = Decimal("50")   # 每日亏损限额 (0=不限)

    # ── V2 Strategy Parameters ──────────────────────────────────────────
    stop_loss_pct: float = 18.0
    strong_tp_pct: float = 33.0
    medium_tp_pct: float = 21.0
    weak_tp_pct: float = 10.0
    max_hold_hours: int = 72

    # V2 early-stop
    enable_2h_early_stop: bool = True
    early_stop_2h_threshold: float = 0.02
    enable_12h_early_stop: bool = True
    early_stop_12h_threshold: float = 0.03

    # V2 weak-24h exit → Observing
    enable_weak_24h_exit: bool = True
    weak_24h_threshold: float = -0.01

    # V2 max-gain 24h exit
    enable_max_gain_24h_exit: bool = True
    max_gain_24h_threshold: float = 0.05

    # V2 strength evaluation
    strength_eval_2h_growth: float = 0.055
    strength_eval_2h_ratio: float = 0.60
    strength_eval_12h_growth: float = 0.075
    strength_eval_12h_ratio: float = 0.60

    # ── Signal Scanning ────────────────────────────────────────────────
    surge_threshold: float = 10.0
    surge_max_multiple: float = 14008.0
    scan_interval_seconds: int = 3600  # 1 hour

    # ── Risk Filters ──────────────────────────────────────────────────
    enable_risk_filters: bool = True

    # ── Position Monitoring ────────────────────────────────────────────
    monitor_interval_seconds: int = 30

    # ── Persistence ────────────────────────────────────────────────────
    db_path: str = "data/trades.db"
