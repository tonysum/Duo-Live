"""Strategy Factory — 根据配置创建策略实例

支持动态加载不同类型的策略，目前支持：
- rolling: RollingLiveStrategy (R24)
- 未来可扩展: rsi, grid, ma_cross 等
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .strategy import Strategy

logger = logging.getLogger(__name__)


class StrategyFactory:
    """策略工厂：根据配置创建策略实例"""

    @staticmethod
    def create_strategy(
        kind: str,
        config_dict: dict,
        store: Any,
        config_path: Optional[Any] = None,
    ) -> Optional[Strategy]:
        """
        根据策略类型创建实例

        Args:
            kind: 策略类型 ("rolling", "rsi", "grid", etc.)
            config_dict: 策略配置字典，包含 id, enabled, 以及策略特定参数
            store: TradeStore 实例
            config_path: 可选；``rolling`` 类型时先加载该 JSON 的全局 ``rolling`` 再合并槽位

        Returns:
            Strategy 实例，如果类型不支持则返回 None

        Example:
            >>> config = {
            ...     "id": "r24-main",
            ...     "kind": "rolling",
            ...     "enabled": True,
            ...     "rolling": {"top_n": 3, "min_pct_chg": 10.0}
            ... }
            >>> strategy = StrategyFactory.create_strategy("rolling", config, store)
        """
        kind_lower = kind.lower()

        if kind_lower == "rolling":
            return StrategyFactory._create_rolling_strategy(
                config_dict, store, config_path=config_path,
            )

        # 未来扩展点：其他策略类型
        # elif kind_lower == "rsi":
        #     from .rsi_strategy import RSIStrategy
        #     return RSIStrategy(config=config_dict, store=store)
        #
        # elif kind_lower == "grid":
        #     from .grid_strategy import GridStrategy
        #     return GridStrategy(config=config_dict, store=store)

        else:
            logger.warning(f"Unknown strategy kind: {kind}")
            return None

    @staticmethod
    def _create_rolling_strategy(
        config_dict: dict,
        store: Any,
        *,
        config_path: Optional[Any] = None,
    ) -> Strategy:
        """创建 RollingLiveStrategy 实例（先合并 data/config.json 全局 ``rolling``，再合并槽位）。"""
        from .rolling_config import RollingLiveConfig, apply_rolling_overrides, load_rolling_from_config_json
        from .rolling_live_strategy import RollingLiveStrategy

        rolling_cfg = RollingLiveConfig()
        if config_path:
            load_rolling_from_config_json(rolling_cfg, config_path, log_applied=False)

        rolling_block = config_dict.get("rolling", {})
        if rolling_block:
            apply_rolling_overrides(rolling_cfg, rolling_block)

        # 设置策略 ID
        strategy_id = config_dict.get("id", "r24")
        rolling_cfg.strategy_id = strategy_id

        # ── 设置策略级配额参数（如果配置中有的话）──
        if "max_positions" in config_dict:
            rolling_cfg.max_positions = config_dict["max_positions"]
        if "margin_per_position" in config_dict:
            rolling_cfg.margin_per_position = config_dict["margin_per_position"]
        if "daily_loss_limit" in config_dict:
            rolling_cfg.daily_loss_limit = config_dict["daily_loss_limit"]

        logger.info(f"Created RollingLiveStrategy: {strategy_id}")
        return RollingLiveStrategy(config=rolling_cfg, store=store)


def load_strategies_from_config(
    config: Any,
    store: Any,
    config_path: Optional[Any] = None,
) -> list[Strategy]:
    """
    从 LiveTradingConfig 加载所有启用的策略

    Args:
        config: LiveTradingConfig 实例
        store: TradeStore 实例
        config_path: 配置文件路径（可选，用于加载 rolling 全局配置）

    Returns:
        策略实例列表（至少包含一个默认策略）

    Fallback 行为:
        - 如果 strategies[] 为空或全部禁用，返回单个默认 R24 策略
        - 如果某个策略创建失败，跳过该策略并记录警告
    """
    strategies = []

    # 尝试从 strategies[] 加载
    if hasattr(config, 'strategies') and config.strategies:
        for slot in config.strategies:
            if not isinstance(slot, dict):
                logger.warning(f"Invalid strategy slot (not dict): {slot}")
                continue

            # 检查是否启用
            if slot.get("enabled") is False:
                logger.info(f"Strategy {slot.get('id', 'unknown')} is disabled, skipping")
                continue

            # 获取策略类型
            kind = slot.get("kind", "rolling")

            # 创建策略实例
            strategy = StrategyFactory.create_strategy(
                kind=kind,
                config_dict=slot,
                store=store,
                config_path=config_path,
            )

            if strategy:
                strategies.append(strategy)
            else:
                logger.warning(f"Failed to create strategy: {slot.get('id', 'unknown')}")

    # Fallback: 如果没有加载到任何策略，使用默认 R24
    if not strategies:
        logger.warning(
            "No strategies configured or all disabled, using default R24 strategy"
        )
        from .rolling_live_strategy import RollingLiveStrategy

        # 使用全局 rolling 配置创建默认策略
        default_strategy = RollingLiveStrategy(store=store)

        # 如果有配置文件，加载全局 rolling 参数
        if config_path:
            from .rolling_config import load_rolling_from_config_json
            load_rolling_from_config_json(default_strategy.config, config_path)

        strategies.append(default_strategy)

    logger.info(f"Loaded {len(strategies)} strategy(ies): {[s.config.strategy_id for s in strategies]}")
    return strategies
