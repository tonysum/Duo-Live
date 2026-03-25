"""Rolling Strategy configuration for live trading.

Mirrors RollingConfig from duo-moonshot/moonshot/rolling_strategy.py,
adapted for the duo-live LiveTrader framework.

External param bundle (optional):
  - Place ``r24_params.json`` in the project cwd (optional fallback: ``r2_params.json``),
    or set env ``DUO_LIVE_PARAMS_FILE`` to a path.
  - Format: ``{"params": { ... }}`` or a flat object. Recognized keys map to
    :class:`RollingLiveConfig` and mutable :class:`~live.live_config.LiveTradingConfig`
    fields (e.g. ``leverage``, ``max_positions``). Unknown keys are logged and skipped.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .live_config import LiveTradingConfig

logger = logging.getLogger(__name__)


@dataclass
class RollingLiveConfig:
    """Configuration for Moonshot-R24 Rolling Strategy (live)."""

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


def resolve_strategy_params_path(explicit: Path | None = None) -> Path | None:
    """First existing path wins: explicit → DUO_LIVE_PARAMS_FILE → r24_params.json → r2_params.json."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    env = os.environ.get("DUO_LIVE_PARAMS_FILE")
    if env:
        candidates.append(Path(env))
    candidates.extend(Path(n) for n in ("r24_params.json", "r2_params.json"))
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


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
    elif isinstance(cur, list):
        logger.warning("Unsupported list field override: %s", name)


def _apply_live_field(live: LiveTradingConfig, name: str, value: object) -> None:
    from .live_config import LiveTradingConfig as _LC

    if name not in _LC.MUTABLE_FIELDS:
        return
    if name in ("live_fixed_margin_usdt", "daily_loss_limit_usdt"):
        setattr(live, name, Decimal(str(value)))
    elif name == "margin_pct":
        setattr(live, name, float(value))
    elif name == "margin_mode":
        setattr(live, name, str(value))
    else:
        setattr(live, name, int(value))


def apply_strategy_params_from_json(
    rolling: RollingLiveConfig,
    live: LiveTradingConfig,
    json_path: Path | None = None,
) -> Path | None:
    """Load strategy/capital params from JSON and override ``rolling`` and ``live``.

    Returns the path loaded, or ``None`` if no file or parse error.
    Precedence for this call: values in the JSON replace current attributes on
    ``rolling`` / ``live`` (typically after ``LiveTradingConfig.load_from_file()``).

    Args:
        rolling: Strategy config instance to mutate.
        live: Live trading config instance to mutate.
        json_path: If set, only try this path (must exist).
    """
    from .live_config import LiveTradingConfig as _LC

    path = json_path if json_path is not None else resolve_strategy_params_path()
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Strategy params file unreadable %s: %s", path, e)
        return None

    params = raw.get("params", raw)
    if not isinstance(params, dict):
        logger.warning("Strategy params: expected object or 'params' object in %s", path)
        return None

    rolling_names = {f.name for f in fields(RollingLiveConfig)}
    applied_live: list[str] = []
    applied_roll: list[str] = []
    unknown: list[str] = []

    for k, v in params.items():
        if v is None:
            continue
        if k in _LC.MUTABLE_FIELDS:
            _apply_live_field(live, k, v)
            applied_live.append(k)
        elif k in rolling_names:
            _apply_rolling_field(rolling, k, v)
            applied_roll.append(k)
        else:
            unknown.append(k)

    if unknown:
        logger.info(
            "Strategy params %s: keys not mapped in duo-live (ignored): %s",
            path,
            ", ".join(sorted(unknown)),
        )
    logger.info(
        "Strategy params from %s — live: [%s], rolling: [%s]",
        path,
        ", ".join(applied_live) or "-",
        ", ".join(applied_roll) or "-",
    )
    return path
