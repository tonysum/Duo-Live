"""Rolling Strategy configuration for live trading.

Mirrors RollingConfig from duo-moonshot/moonshot/rolling_strategy.py,
adapted for the duo-live LiveTrader framework.

R24 strategy overrides live in ``data/config.json`` under the ``"rolling"`` key
(same file as :class:`~live.live_config.LiveTradingConfig` mutable fields).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path

logger = logging.getLogger(__name__)

ROLLING_JSON_KEY = "rolling"


@dataclass
class RollingLiveConfig:
    """Configuration for Moonshot-R24 Rolling Strategy (live)."""

    # Identity for multi-strategy routing (signals, attribution, monitor).
    strategy_id: str = "r24"

    # ── 1. Signal Generation ─────────────────────────────────────────
    top_n: int = 5                        # 每次扫描取涨幅前 N 名
    min_pct_chg: float = 5.0             # 最小涨幅要求 10%
    min_listed_days: int = 10             # 新币过滤
    signal_cooldown_hours: int = 8       # 同币种信号冷却期(小时)
    scan_interval_hours: int = 2          # 扫描间隔(小时)

    # Main profit check
    enable_main_profit_check: bool = True
    main_profit_thresholds: list = field(default_factory=lambda: [
        (40,  51),
        (60,  45),
        (999, 35),
    ])

    # ── 2. Position Management ───────────────────────────────────────
    max_hold_days: int = 7

    # Take Profit
    tp_initial: float = 0.16              # 初始止盈 34%
    tp_reduced: float = 0.08              # 时间衰减后止盈 14%
    tp_hours_threshold: int = 6          # N小时后降低止盈
    tp_after_add: float = 0.46            # 加仓后止盈 45%

    # Stop Loss
    sl_threshold: float = 0.42            # 止损 44%

    # Trailing Stop
    enable_trailing_stop: bool = True
    trailing_activation_pct: float = 0.08  # 激活阈值 16%
    trailing_distance_pct: float = 0.04    # 回弹距离 9%

    # Add Position
    enable_add_position: bool = True
    add_position_threshold: float = 0.2
    add_position_multiplier: float = 0.08


def _apply_rolling_field(cfg: RollingLiveConfig, name: str, value: object) -> None:
    cur = getattr(cfg, name)
    if name == "main_profit_thresholds" and isinstance(value, list):
        pairs: list[tuple[int, int]] = []
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((int(item[0]), int(item[1])))
        if pairs:
            setattr(cfg, name, pairs)
        return
    if isinstance(cur, bool):
        setattr(cfg, name, bool(value))
    elif isinstance(cur, int) and not isinstance(cur, bool):
        setattr(cfg, name, int(value))
    elif isinstance(cur, float):
        setattr(cfg, name, float(value))
    elif isinstance(cur, str):
        setattr(cfg, name, str(value))
    elif isinstance(cur, list):
        logger.warning("Unsupported list field override: %s", name)


def load_rolling_from_config_json(
    rolling: RollingLiveConfig,
    path: Path | None = None,
) -> bool:
    """Read ``"rolling"`` from ``data/config.json`` and apply to ``rolling``.

    Returns True if a non-empty rolling object was found and processed.
    """
    from .live_config import CONFIG_PATH

    p = path or CONFIG_PATH
    if not p.is_file():
        return False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read config for rolling: %s", e)
        return False
    block = raw.get(ROLLING_JSON_KEY)
    if not isinstance(block, dict) or not block:
        return False
    rolling_names = {f.name for f in fields(RollingLiveConfig)}
    applied: list[str] = []
    unknown: list[str] = []
    for k, v in block.items():
        if v is None:
            continue
        if k in rolling_names:
            _apply_rolling_field(rolling, k, v)
            applied.append(k)
        else:
            unknown.append(k)
    if unknown:
        logger.info(
            "config.json rolling: unknown keys (ignored): %s",
            ", ".join(sorted(unknown)),
        )
    logger.info(
        "Rolling params from %s [%s] — applied: [%s]",
        p,
        ROLLING_JSON_KEY,
        ", ".join(applied) or "-",
    )
    return True
