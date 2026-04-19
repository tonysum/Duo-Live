"""Live trading configuration.

Strategy-independent parameters for the live trading system.
Strategy-specific parameters live in rolling_config.py.
"""

from dataclasses import dataclass, field
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

    #: True：按真实行情与策略逻辑模拟开平仓，写入 SQLite，**不向交易所下单**
    paper_trading: bool = False

    # ── Multi-strategy manifest (declarative; runtime still wires instances in __main__) ──
    strategies: list[dict] = field(default_factory=list)

    # ── Mutable fields (saveable via frontend) ────────────────────────
    MUTABLE_FIELDS = {
        "leverage", "max_positions", "max_entries_per_day",
        "live_fixed_margin_usdt", "daily_loss_limit_usdt",
        "margin_mode", "margin_pct", "paper_trading",
    }

    def save_to_file(self, path: Path = CONFIG_PATH) -> None:
        """Save mutable config fields to JSON; preserve ``rolling`` block if present."""
        path.parent.mkdir(parents=True, exist_ok=True)
        rolling_block: dict = {}
        strategies_block: list = []
        if path.exists():
            try:
                existing = json.loads(path.read_text())
                rb = existing.get("rolling")
                if isinstance(rb, dict):
                    rolling_block = rb
                st = existing.get("strategies")
                if isinstance(st, list):
                    strategies_block = st
            except (OSError, json.JSONDecodeError):
                pass
        if self.strategies:
            strategies_block = self.strategies
        data = {
            "leverage": self.leverage,
            "max_positions": self.max_positions,
            "max_entries_per_day": self.max_entries_per_day,
            "live_fixed_margin_usdt": float(self.live_fixed_margin_usdt),
            "daily_loss_limit_usdt": float(self.daily_loss_limit_usdt),
            "margin_mode": self.margin_mode,
            "margin_pct": self.margin_pct,
            "paper_trading": self.paper_trading,
        }
        if rolling_block:
            data["rolling"] = rolling_block
        if strategies_block:
            data["strategies"] = strategies_block
        path.write_text(json.dumps(data, indent=2))
        logger.info("Config saved to %s", path)

    @classmethod
    def load_from_file(cls, path: Path = CONFIG_PATH) -> "LiveTradingConfig":
        """Create config, overriding defaults with values from JSON if it exists."""
        config = cls()
        config.apply_partial_from_file(path, exclude_keys=frozenset())
        return config

    def apply_partial_from_file(
        self,
        path: Path = CONFIG_PATH,
        exclude_keys: frozenset[str] | set[str] | None = None,
    ) -> None:
        """Merge mutable fields from JSON; skip keys in ``exclude_keys``. Ignores ``rolling``."""
        ex = frozenset(exclude_keys) if exclude_keys is not None else frozenset()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            if "leverage" in data and "leverage" not in ex:
                self.leverage = int(data["leverage"])
            if "max_positions" in data and "max_positions" not in ex:
                self.max_positions = int(data["max_positions"])
            if "max_entries_per_day" in data and "max_entries_per_day" not in ex:
                self.max_entries_per_day = int(data["max_entries_per_day"])
            if "live_fixed_margin_usdt" in data and "live_fixed_margin_usdt" not in ex:
                self.live_fixed_margin_usdt = Decimal(str(data["live_fixed_margin_usdt"]))
            if "daily_loss_limit_usdt" in data and "daily_loss_limit_usdt" not in ex:
                self.daily_loss_limit_usdt = Decimal(str(data["daily_loss_limit_usdt"]))
            if "margin_mode" in data and "margin_mode" not in ex:
                self.margin_mode = data["margin_mode"]
            if "margin_pct" in data and "margin_pct" not in ex:
                self.margin_pct = float(data["margin_pct"])
            if "paper_trading" in data and "paper_trading" not in ex:
                self.paper_trading = bool(data["paper_trading"])
            if "strategies" in data and isinstance(data["strategies"], list):
                self.strategies = [x for x in data["strategies"] if isinstance(x, dict)]
            logger.info("Config loaded from %s (excluded keys: %s)", path, ", ".join(sorted(ex)) or "-")
        except Exception as e:
            logger.warning("Failed to load config from %s: %s", path, e)
