"""Live trading configuration.

Strategy parameters for the Surge Short V2 live trading system.
"""

from dataclasses import dataclass, fields, asdict
from decimal import Decimal
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("data/config.json")


@dataclass
class LiveTradingConfig:
    """Configuration for live trading."""

    # ── Capital & Leverage ──────────────────────────────────────────────
    leverage: int = 3
    max_positions: int = 6
    max_entries_per_day: int = 2
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

    # ── Mutable fields (saveable via frontend) ────────────────────────
    MUTABLE_FIELDS = {
        "leverage", "max_positions", "max_entries_per_day",
        "live_fixed_margin_usdt", "daily_loss_limit_usdt",
    }

    def save_to_file(self, path: Path = CONFIG_PATH) -> None:
        """Save mutable config fields to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "leverage": self.leverage,
            "max_positions": self.max_positions,
            "max_entries_per_day": self.max_entries_per_day,
            "live_fixed_margin_usdt": float(self.live_fixed_margin_usdt),
            "daily_loss_limit_usdt": float(self.daily_loss_limit_usdt),
        }
        path.write_text(json.dumps(data, indent=2))
        logger.info("Config saved to %s", path)

    @classmethod
    def load_from_file(cls, path: Path = CONFIG_PATH) -> "LiveTradingConfig":
        """Create config, overriding defaults with values from JSON if it exists."""
        config = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if "leverage" in data:
                    config.leverage = int(data["leverage"])
                if "max_positions" in data:
                    config.max_positions = int(data["max_positions"])
                if "max_entries_per_day" in data:
                    config.max_entries_per_day = int(data["max_entries_per_day"])
                if "live_fixed_margin_usdt" in data:
                    config.live_fixed_margin_usdt = Decimal(str(data["live_fixed_margin_usdt"]))
                if "daily_loss_limit_usdt" in data:
                    config.daily_loss_limit_usdt = Decimal(str(data["daily_loss_limit_usdt"]))
                logger.info("Config loaded from %s", path)
            except Exception as e:
                logger.warning("Failed to load config from %s: %s", path, e)
        return config
