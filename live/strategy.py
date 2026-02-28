"""Pluggable trading strategy interface.

Defines a Strategy ABC and the default SurgeShortStrategy implementation.
To create a new strategy, subclass Strategy and implement all abstract methods,
then pass it to LiveTrader.

Decision points:
  1. create_scanner()        — how to find trading signals
  2. filter_entry()          — pre-entry risk checks + entry parameters
  3. evaluate_position()     — dynamic TP, early stops, max hold
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .binance_client import BinanceFuturesClient
    from .live_config import LiveTradingConfig
    from .live_position_monitor import TrackedPosition

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────


@dataclass
class EntryDecision:
    """Result of a strategy's entry filter."""

    should_enter: bool
    reject_reason: str = ""
    side: str = "SELL"          # SELL = short, BUY = long
    tp_pct: float = 33.0
    sl_pct: float = 18.0


@dataclass
class PositionAction:
    """Action to take on a monitored position.

    action values:
      "hold"       — keep position as-is
      "close"      — force close (market order)
      "adjust_tp"  — replace TP order with new_tp_pct
    """

    action: str = "hold"
    reason: str = ""
    new_tp_pct: float = 0
    new_strength: str = ""


# ──────────────────────────────────────────────────────────────────────
# Abstract Strategy
# ──────────────────────────────────────────────────────────────────────


class Strategy(ABC):
    """Abstract trading strategy interface.

    Implement this to create a new trading strategy. The LiveTrader and
    LivePositionMonitor delegate all strategy-specific decisions to this
    interface. Infrastructure (order placement, position tracking,
    WebSocket, Telegram) stays in the framework.

    Lifecycle::

        trader = LiveTrader(strategy=MyStrategy())
        # startup:
        scanner = strategy.create_scanner(config, queue, client, console)
        # on signal:
        decision = await strategy.filter_entry(client, signal, price, now)
        # every monitor cycle:
        action = await strategy.evaluate_position(client, pos, config, now)
    """

    @abstractmethod
    def create_scanner(
        self,
        config: LiveTradingConfig,
        signal_queue: Any,
        client: BinanceFuturesClient,
        console: Any,
    ) -> Any:
        """Create and return a signal scanner with a ``run_forever()`` coroutine."""

    @abstractmethod
    async def filter_entry(
        self,
        client: BinanceFuturesClient,
        signal: Any,
        entry_price: Decimal,
        signal_price: Decimal,
        now: datetime,
        config: LiveTradingConfig,
    ) -> EntryDecision:
        """Decide whether to enter a position for this signal.

        Called after infrastructure-level guards (max positions, duplicate
        symbol check). Should run strategy-specific risk filters and return
        entry parameters (side, TP%, SL%).
        """

    @abstractmethod
    async def evaluate_position(
        self,
        client: BinanceFuturesClient,
        pos: TrackedPosition,
        config: LiveTradingConfig,
        now: datetime,
    ) -> PositionAction:
        """Evaluate an open position and decide what to do.

        Called every monitor cycle for each tracked position.
        Return PositionAction("hold"), ("close", reason), or
        ("adjust_tp", new_tp_pct=X, new_strength=Y).
        """


# ──────────────────────────────────────────────────────────────────────
# SurgeShortStrategy — default implementation
# ──────────────────────────────────────────────────────────────────────


class SurgeShortStrategy(Strategy):
    """Surge Short V2 strategy.

    Signal:  sell volume surge detection (LiveSurgeScanner)
    Entry:   SHORT with risk filter pipeline
    TP/SL:   dynamic TP based on coin strength at 2h/12h checkpoints
    Exit:    max hold time, early stops
    """

    def __init__(self) -> None:
        self._risk_filters: Optional[object] = None

    # ── Scanner ────────────────────────────────────────────────────

    def create_scanner(self, config, signal_queue, client, console):
        from .live_scanner import LiveSurgeScanner

        return LiveSurgeScanner(
            config=config,
            signal_queue=signal_queue,
            client=client,
            console=console,
        )

    # ── Entry Filter ──────────────────────────────────────────────

    async def filter_entry(
        self,
        client: BinanceFuturesClient,
        signal: Any,
        entry_price: Decimal,
        signal_price: Decimal,
        now: datetime,
        config: LiveTradingConfig,
    ) -> EntryDecision:
        """Run risk filters, return SHORT entry with TP/SL from config."""

        # Lazy-init risk filters
        if config.enable_risk_filters and self._risk_filters is None:
            from .risk_filters import RiskFilters
            self._risk_filters = RiskFilters(client)

        # Run risk filter pipeline
        if self._risk_filters:
            try:
                result = await self._risk_filters.check_all(
                    signal.symbol, now, entry_price, signal_price,
                )
                if not result.should_trade:
                    return EntryDecision(
                        should_enter=False,
                        reject_reason=result.reason,
                    )
            except Exception as e:
                logger.warning(
                    "Risk filter error for %s (fail-open): %s",
                    signal.symbol, e,
                )

        return EntryDecision(
            should_enter=True,
            side="SHORT",  # SHORT strategy
            tp_pct=config.strong_tp_pct,
            sl_pct=config.stop_loss_pct,
        )

    # ── Position Evaluation ───────────────────────────────────────

    async def evaluate_position(
        self,
        client: BinanceFuturesClient,
        pos: TrackedPosition,
        config: LiveTradingConfig,
        now: datetime,
    ) -> PositionAction:
        """Evaluate position: dynamic TP at 2h/12h, max hold time."""

        if not pos.entry_fill_time or not pos.entry_price:
            return PositionAction("hold")

        hold_hours = (now - pos.entry_fill_time).total_seconds() / 3600

        # ── Max hold time ────────────────────────────────────────
        if hold_hours >= config.max_hold_hours:
            return PositionAction("close", reason="max_hold_time")

        # ── Dynamic TP: 2h checkpoint ────────────────────────────
        if not pos.evaluated_2h and hold_hours >= 2.0:
            pos.evaluated_2h = True
            pct_drop = await self._calc_5m_drop_ratio(
                client, pos.symbol,
                pos.entry_fill_time,
                pos.entry_fill_time + timedelta(hours=2),
                pos.entry_price,
                config.strength_eval_2h_growth,
            )
            old_tp = pos.current_tp_pct
            if pct_drop is not None and pct_drop >= config.strength_eval_2h_ratio:
                new_strength = "strong"
                new_tp = config.strong_tp_pct
            else:
                new_strength = "medium"
                new_tp = config.medium_tp_pct

            logger.info(
                "📊 2h 评估: %s → %s (TP %s%% → %s%%)",
                pos.symbol, new_strength, old_tp, new_tp,
            )
            if new_tp != old_tp:
                return PositionAction(
                    "adjust_tp", new_tp_pct=new_tp, new_strength=new_strength,
                )
            # TP unchanged but update strength
            pos.strength = new_strength

        # ── Dynamic TP: 12h checkpoint ───────────────────────────
        if not pos.evaluated_12h and hold_hours >= 12.0:
            pos.evaluated_12h = True
            pct_drop = await self._calc_5m_drop_ratio(
                client, pos.symbol,
                pos.entry_fill_time,
                pos.entry_fill_time + timedelta(hours=12),
                pos.entry_price,
                config.strength_eval_12h_growth,
            )
            old_tp = pos.current_tp_pct
            
            # 🔥 连续暴涨保护逻辑（从 AE Server 移植）
            # 如果下跌占比 >= 60%，判定为强势币
            if pct_drop is not None and pct_drop >= config.strength_eval_12h_ratio:
                new_strength = "strong"
                new_tp = config.strong_tp_pct
                logger.info(
                    "📊 12h 评估: %s → %s (TP %s%% → %s%%)",
                    pos.symbol, new_strength, old_tp, new_tp,
                )
            else:
                # 下跌占比 < 60%：检查是否为连续暴涨
                is_consecutive = await self._check_consecutive_surge(client, pos)
                
                if is_consecutive:
                    # 🔥 连续暴涨保护：保持强势或中等币止盈，不降为弱势币
                    if pos.strength == "strong":
                        new_strength = "strong"
                        new_tp = config.strong_tp_pct  # 保持33%
                        logger.info(
                            "✅ 12h 判断：%s 连续2小时暴涨，保持强势币止盈：\n"
                            "  • 下跌占比 %.1f%% < 60%%\n"
                            "  • 但为连续暴涨，保持强势币止盈=%s%%",
                            pos.symbol, (pct_drop or 0) * 100, new_tp,
                        )
                    else:
                        new_strength = "medium"
                        new_tp = config.medium_tp_pct  # 保持21%
                        logger.info(
                            "✅ 12h 判断：%s 连续2小时暴涨，保持中等币止盈：\n"
                            "  • 下跌占比 %.1f%% < 60%%\n"
                            "  • 但为连续暴涨，保持中等币止盈=%s%%",
                            pos.symbol, (pct_drop or 0) * 100, new_tp,
                        )
                else:
                    # 非连续暴涨：正常降为弱势币
                    new_strength = "weak"
                    new_tp = config.weak_tp_pct
                    logger.warning(
                        "⚠️⚠️ 12h 判定为弱势币: %s 下跌占比%.1f%% < 60%%, 止盈降至%s%%",
                        pos.symbol, (pct_drop or 0) * 100, new_tp,
                    )

            if new_tp != old_tp:
                return PositionAction(
                    "adjust_tp", new_tp_pct=new_tp, new_strength=new_strength,
                )
            pos.strength = new_strength

        return PositionAction("hold")

    # ── Helper ────────────────────────────────────────────────────

    @staticmethod
    async def _calc_5m_drop_ratio(
        client: BinanceFuturesClient,
        symbol: str,
        start: datetime,
        end: datetime,
        entry_price: Decimal,
        threshold: float,
    ) -> float | None:
        """Compute fraction of 5m candles whose close dropped > threshold."""
        try:
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)

            klines = await client.get_klines(
                symbol=symbol,
                interval="5m",
                start_time=start_ms,
                end_time=end_ms,
                limit=1500,
            )

            if not klines or len(klines) < 2:
                return None

            ep = float(entry_price)
            drops = sum(
                1 for k in klines if (float(k.close) - ep) / ep < -threshold
            )
            return drops / len(klines)

        except Exception as e:
            logger.debug("5m drop ratio error for %s: %s", symbol, e)
            return None

    @staticmethod
    async def _check_consecutive_surge(
        client: BinanceFuturesClient,
        pos: TrackedPosition,
    ) -> bool:
        """检查该持仓在建仓时是否为连续2小时卖量暴涨（从 AE Server 移植）
        
        判断逻辑：
        1. 获取信号发生时间（第1小时）
        2. 建仓时间 = 信号时间 + 1小时（第2小时）
        3. 检查信号小时和建仓小时是否都有卖量>=10倍
        4. 如果是，返回True（连续确认）
        
        Args:
            client: Binance客户端
            pos: 持仓信息
        
        Returns:
            bool: 是否为连续2小时确认
        """
        symbol = pos.symbol
        try:
            # 从持仓获取信号时间（需要在 TrackedPosition 中添加此字段）
            # 如果没有信号时间，使用建仓时间往前推1小时作为估算
            if not pos.entry_fill_time:
                logger.debug(f"❌ {symbol} 无entry_fill_time，无法判断连续确认")
                return False
            
            # 估算信号时间 = 建仓时间 - 1小时
            signal_dt = pos.entry_fill_time - timedelta(hours=1)
            entry_dt = pos.entry_fill_time
            
            # 步骤1：获取昨日平均小时卖量
            # 获取昨日日K线
            yesterday = signal_dt.date() - timedelta(days=1)
            yesterday_start = int(datetime.combine(yesterday, datetime.min.time()).replace(tzinfo=timezone.utc).timestamp() * 1000)
            yesterday_end = int(datetime.combine(yesterday, datetime.max.time()).replace(tzinfo=timezone.utc).timestamp() * 1000)
            
            klines_daily = await client.get_klines(
                symbol=symbol,
                interval='1d',
                start_time=yesterday_start,
                end_time=yesterday_end,
                limit=1
            )
            
            if not klines_daily:
                logger.debug(f"❌ {symbol} 昨日数据缺失，无法判断连续确认")
                return False
            
            # 计算昨日平均小时卖量
            volume = float(klines_daily[0].volume)
            active_buy_volume = float(klines_daily[0].taker_buy_base_volume)
            total_sell = volume - active_buy_volume
            yesterday_avg_hour_sell = total_sell / 24.0
            
            if yesterday_avg_hour_sell <= 0:
                logger.debug(f"❌ {symbol} 昨日平均卖量为0，无法判断连续确认")
                return False
            
            # 步骤2：从API获取信号小时和建仓小时的K线数据
            signal_hour_ms = int(signal_dt.timestamp() * 1000)
            entry_hour_ms = int(entry_dt.timestamp() * 1000)
            
            # 获取2小时的K线数据
            klines = await client.get_klines(
                symbol=symbol,
                interval='1h',
                start_time=signal_hour_ms,
                end_time=entry_hour_ms,
                limit=2
            )
            
            if len(klines) < 2:
                logger.debug(f"❌ {symbol} 小时数据不足（{len(klines)}条），无法判断连续确认")
                return False
            
            # 计算每小时的卖量倍数
            threshold = 10.0  # 10倍阈值
            ratios = []
            hour_times = []
            
            for kline in klines:
                hour_volume = float(kline.volume)
                hour_active_buy = float(kline.taker_buy_base_volume)
                hour_sell_volume = hour_volume - hour_active_buy
                ratio = hour_sell_volume / yesterday_avg_hour_sell
                ratios.append(ratio)
                hour_times.append(datetime.fromtimestamp(int(kline.open_time)/1000, tz=timezone.utc).strftime('%H:%M'))
            
            # 判断两个小时都>=10倍
            if len(ratios) >= 2 and all(r >= threshold for r in ratios[-2:]):
                logger.info(
                    f"✅ {symbol} 确认为连续2小时卖量暴涨：\n"
                    f"  • 信号小时({hour_times[-2]}): {ratios[-2]:.2f}x\n"
                    f"  • 建仓小时({hour_times[-1]}): {ratios[-1]:.2f}x\n"
                    f"  • 阈值: {threshold}x"
                )
                return True
            else:
                logger.debug(f"❌ {symbol} 非连续确认（倍数: 信号{ratios[-2]:.2f}x, 建仓{ratios[-1]:.2f}x < {threshold}x）")
                return False
        
        except Exception as e:
            logger.warning(f"⚠️ {symbol} 检查连续确认失败: {e}")
            import traceback
            logger.debug(f"异常堆栈:\n{traceback.format_exc()}")
            return False
