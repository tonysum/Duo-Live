"""Rolling Strategy configuration for live trading.

Mirrors RollingConfig from duo-moonshot/moonshot/rolling_strategy.py,
adapted for the duo-live LiveTrader framework.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RollingLiveConfig:
    """Configuration for Moonshot-R24 Rolling Strategy (live)."""

    # ── 1. Signal Generation ─────────────────────────────────────────
    top_n: int = 1                        # 每次扫描取涨幅前 N 名
    min_pct_chg: float = 10.0             # 最小涨幅要求 10%
    min_listed_days: int = 10             # 新币过滤
    signal_cooldown_hours: int = 24       # 同币种信号冷却期(小时)
    scan_interval_hours: int = 1          # 扫描间隔(小时)

    # Main profit check
    enable_main_profit_check: bool = True
    main_profit_thresholds: list = field(default_factory=lambda: [
        (40,  51),
        (60,  45),
        (999, 35),
    ])

    # ── 2. Position Management ───────────────────────────────────────
    max_hold_days: int = 11

    # Take Profit
    tp_initial: float = 0.34              # 初始止盈 34%
    tp_reduced: float = 0.14              # 时间衰减后止盈 14%
    tp_hours_threshold: int = 10          # N小时后降低止盈
    tp_after_add: float = 0.45            # 加仓后止盈 45%

    # Stop Loss
    sl_threshold: float = 0.44            # 止损 44%

    # Trailing Stop
    enable_trailing_stop: bool = True
    trailing_activation_pct: float = 0.16  # 激活阈值 16%
    trailing_distance_pct: float = 0.09    # 回弹距离 9%

    # Add Position
    enable_add_position: bool = True
    add_position_threshold: float = 0.36
    add_position_multiplier: float = 1.0
