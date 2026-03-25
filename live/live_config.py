"""Live trading configuration.

Strategy-independent parameters for the live trading system.
Strategy-specific parameters live in rolling_config.py.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("data/config.json")


@dataclass
class LiveTradingConfig:
    """Configuration for live trading infrastructure.

    Strategy-specific params (TP/SL/trailing) are in RollingLiveConfig.
    These are shared infra & capital params used by trader/monitor.
    """

    # ── Capital & Leverage ──────────────────────────────────────────────
    leverage: int = 3
    max_positions: int = 8
    max_entries_per_day: int = 8
    live_fixed_margin_usdt: Decimal = Decimal("5")  # 固定保证金 (USDT/笔)
    daily_loss_limit_usdt: Decimal = Decimal("50")   # 每日亏损限额 (0=不限)
    margin_mode: str = "fixed"  # "fixed" 或 "percent"
    margin_pct: float = 2.0     # 百分比模式: 可用余额的百分比

    # ── Position Monitoring ────────────────────────────────────────────
    monitor_interval_seconds: int = 60  # 🔧 网络优化：降低请求频率 30s → 60s

    # ── Persistence ────────────────────────────────────────────────────
    db_path: str = "data/trades.db"

    # ── Mutable fields (saveable via frontend) ────────────────────────
    MUTABLE_FIELDS = {
        "leverage", "max_positions", "max_entries_per_day",
        "live_fixed_margin_usdt", "daily_loss_limit_usdt",
        "margin_mode", "margin_pct",
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
            "margin_mode": self.margin_mode,
            "margin_pct": self.margin_pct,
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
                if "margin_mode" in data:
                    config.margin_mode = data["margin_mode"]
                if "margin_pct" in data:
                    config.margin_pct = float(data["margin_pct"])
                logger.info("Config loaded from %s", path)
            except Exception as e:
                logger.warning("Failed to load config from %s: %s", path, e)
        return config
