"""Live trading configuration.

Mirrors SurgeShortEngineConfig V2 parameters for consistency between
backtest and live trading. Adds real-time-specific settings.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class LiveTradingConfig:
    """Unified configuration for paper / live trading."""

    # ── Capital & Leverage ──────────────────────────────────────────────
    initial_capital: Decimal = Decimal("10000")
    leverage: int = 3 #回测时是4，实盘时是3
    position_size_pct: Decimal = Decimal("0.015")#回测时是0.5，实盘时是0.015
    max_positions: int = 6 #回测时是10，实盘时是6
    max_entries_per_day: int = 4 #回测时是20，实盘时是4
    max_position_value_ratio: float = 0.5 
    min_capital_ratio: float = 0.1
    commission_rate: Decimal = Decimal("0.0004")

    # ── Execution Mode ──────────────────────────────────────────────────
    live_mode: bool = False  # True = real orders, False = paper trading
    live_fixed_margin_usdt: Decimal = Decimal("5")  # 固定保证金 (0=按比例)
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
    paper_db_path: str = "data/paper_trades.db"
