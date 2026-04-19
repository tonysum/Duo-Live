"""Strategy Quota — 策略级资金配额管理

为每个策略维护独立的资金配额和风控限制，实现策略间的资金隔离。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StrategyQuota:
    """单个策略的资金配额和风控限制

    每个策略实例有独立的：
    - 最大持仓数限制
    - 单笔保证金额度
    - 每日亏损限额

    运行时状态：
    - 当前持仓数
    - 今日已实现盈亏
    """

    strategy_id: str
    max_positions: int = 3
    margin_per_position: Decimal = Decimal("5")
    daily_loss_limit: Decimal = Decimal("20")

    # 运行时状态
    current_positions: int = 0
    daily_realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))

    def can_open_position(self) -> tuple[bool, str]:
        """
        检查是否可以开新仓

        Returns:
            (can_open, reason) 元组
            - can_open: True 表示可以开仓
            - reason: 如果不能开仓，返回原因；否则为空字符串
        """
        # 检查持仓数限制
        if self.current_positions >= self.max_positions:
            return False, (
                f"max_positions reached "
                f"({self.current_positions}/{self.max_positions})"
            )

        # 检查每日亏损限额
        if self.daily_loss_limit > 0 and self.daily_realized_pnl <= -self.daily_loss_limit:
            return False, (
                f"daily loss limit "
                f"({self.daily_realized_pnl} <= -{self.daily_loss_limit})"
            )

        return True, ""

    def increment_position(self) -> None:
        """增加持仓计数（开仓成功后调用）"""
        self.current_positions += 1
        logger.debug(
            f"[{self.strategy_id}] Position count: {self.current_positions}/{self.max_positions}"
        )

    def decrement_position(self) -> None:
        """减少持仓计数（平仓后调用）"""
        if self.current_positions > 0:
            self.current_positions -= 1
            logger.debug(
                f"[{self.strategy_id}] Position count: {self.current_positions}/{self.max_positions}"
            )

    def update_daily_pnl(self, pnl: Decimal) -> None:
        """
        更新今日已实现盈亏

        Args:
            pnl: 本次交易的盈亏（正数为盈利，负数为亏损）
        """
        self.daily_realized_pnl += pnl
        logger.info(
            f"[{self.strategy_id}] Daily PnL updated: {self.daily_realized_pnl:+.2f} USDT "
            f"(this trade: {pnl:+.2f})"
        )

    def reset_daily_stats(self) -> None:
        """重置每日统计（UTC 日期变更时调用）"""
        logger.info(
            f"[{self.strategy_id}] Resetting daily stats "
            f"(previous PnL: {self.daily_realized_pnl:+.2f})"
        )
        self.daily_realized_pnl = Decimal("0")

    def get_available_margin(self) -> Decimal:
        """
        获取可用保证金（单笔）

        Returns:
            单笔交易可用的保证金额度
        """
        return self.margin_per_position

    def to_dict(self) -> dict:
        """转换为字典（用于 API 响应）"""
        return {
            "strategy_id": self.strategy_id,
            "max_positions": self.max_positions,
            "current_positions": self.current_positions,
            "margin_per_position": float(self.margin_per_position),
            "daily_loss_limit": float(self.daily_loss_limit),
            "daily_realized_pnl": float(self.daily_realized_pnl),
            "available_slots": max(0, self.max_positions - self.current_positions),
        }


class QuotaManager:
    """配额管理器：管理所有策略的配额"""

    def __init__(self):
        self._quotas: dict[str, StrategyQuota] = {}

    def register_strategy(
        self,
        strategy_id: str,
        max_positions: int = 3,
        margin_per_position: Decimal = Decimal("5"),
        daily_loss_limit: Decimal = Decimal("20"),
    ) -> None:
        """
        注册策略配额

        Args:
            strategy_id: 策略唯一标识
            max_positions: 最大持仓数
            margin_per_position: 单笔保证金
            daily_loss_limit: 每日亏损限额
        """
        if strategy_id in self._quotas:
            logger.warning(f"Strategy {strategy_id} already registered, overwriting")

        self._quotas[strategy_id] = StrategyQuota(
            strategy_id=strategy_id,
            max_positions=max_positions,
            margin_per_position=margin_per_position,
            daily_loss_limit=daily_loss_limit,
        )
        logger.info(
            f"Registered quota for {strategy_id}: "
            f"max_pos={max_positions}, margin={margin_per_position}, "
            f"loss_limit={daily_loss_limit}"
        )

    def get_quota(self, strategy_id: str) -> Optional[StrategyQuota]:
        """获取策略配额"""
        return self._quotas.get(strategy_id)

    def reset_all_daily_stats(self) -> None:
        """重置所有策略的每日统计"""
        for quota in self._quotas.values():
            quota.reset_daily_stats()

    def get_all_quotas(self) -> dict[str, StrategyQuota]:
        """获取所有配额（用于监控）"""
        return self._quotas.copy()

    def to_dict(self) -> dict:
        """转换为字典（用于 API 响应）"""
        return {
            strategy_id: quota.to_dict()
            for strategy_id, quota in self._quotas.items()
        }
