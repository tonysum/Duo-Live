"""Tests for dynamic TP evaluation (2h/12h coin strength).

Tests both the Strategy-based path (via SurgeShortStrategy.evaluate_position())
and the legacy monitor-level _update_dynamic_tp fallback.

Validates that:
  - Skips if hold_hours < 2
  - Evaluates strength at 2h → strong (33%) or medium (21%)
  - Evaluates strength at 12h → strong (33%) or weak (10%)
  - Only evaluates once per checkpoint
  - Returns correct PositionAction for each case
"""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from live.live_position_monitor import LivePositionMonitor, TrackedPosition
from live.live_config import LiveTradingConfig
from live.strategy import SurgeShortStrategy, PositionAction


@pytest.fixture
def config():
    return LiveTradingConfig()


@pytest.fixture
def strategy():
    return SurgeShortStrategy()


@pytest.fixture
def monitor(config, strategy):
    client = AsyncMock()
    executor = MagicMock()
    mon = LivePositionMonitor(
        client=client, executor=executor, config=config,
        strategy=strategy,
    )
    return mon


@pytest.fixture
def monitor_legacy(config):
    """Monitor without strategy (legacy fallback)."""
    client = AsyncMock()
    executor = MagicMock()
    return LivePositionMonitor(
        client=client, executor=executor, config=config,
    )


def _make_pos(hours_held: float = 0, tp_pct: float = 33.0) -> TrackedPosition:
    now = datetime.now(timezone.utc)
    pos = TrackedPosition(
        symbol="BTCUSDT",
        entry_order_id=1,
        side="SHORT",
        quantity="0.01",
        deferred_tp_sl={},
        entry_filled=True,
        entry_price=Decimal("100000"),
        entry_fill_time=now - timedelta(hours=hours_held),
        tp_sl_placed=True,
        tp_algo_id=111,
        current_tp_pct=tp_pct,
    )
    return pos


# ──────────────────────────────────────────────────────────────────
# Tests via SurgeShortStrategy.evaluate_position() directly
# ──────────────────────────────────────────────────────────────────

class TestStrategyEvalSkip:
    @pytest.mark.asyncio
    async def test_skip_if_no_entry_fill_time(self, strategy, config):
        pos = _make_pos(hours_held=3)
        pos.entry_fill_time = None
        client = AsyncMock()
        action = await strategy.evaluate_position(client, pos, config, datetime.now(timezone.utc))
        assert action.action == "hold"
        assert pos.evaluated_2h is False

    @pytest.mark.asyncio
    async def test_skip_if_under_2h(self, strategy, config):
        pos = _make_pos(hours_held=1.5)
        client = AsyncMock()
        action = await strategy.evaluate_position(client, pos, config, datetime.now(timezone.utc))
        assert action.action == "hold"
        assert pos.evaluated_2h is False
        assert pos.current_tp_pct == 33.0


class TestStrategyEval2H:
    @pytest.mark.asyncio
    async def test_2h_strong(self, strategy, config):
        """High drop ratio → strong → keep 33% → hold (no change)."""
        pos = _make_pos(hours_held=2.5)
        strategy._calc_5m_drop_ratio = AsyncMock(return_value=0.70)

        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )

        assert pos.evaluated_2h is True
        assert pos.strength == "strong"
        # TP unchanged (33 → 33), so no adjust
        assert action.action == "hold"

    @pytest.mark.asyncio
    async def test_2h_medium(self, strategy, config):
        """Low drop ratio → medium → adjust to 21%."""
        pos = _make_pos(hours_held=2.5)
        strategy._calc_5m_drop_ratio = AsyncMock(return_value=0.30)

        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )

        assert pos.evaluated_2h is True
        # Strategy returns adjust action
        assert action.action == "adjust_tp"
        assert action.new_tp_pct == 21.0
        assert action.new_strength == "medium"

    @pytest.mark.asyncio
    async def test_2h_only_evaluates_once(self, strategy, config):
        """Even if called multiple times, 2h eval only runs once."""
        pos = _make_pos(hours_held=3)
        pos.evaluated_2h = True  # Already done
        strategy._calc_5m_drop_ratio = AsyncMock(return_value=0.30)

        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )

        strategy._calc_5m_drop_ratio.assert_not_called()
        assert action.action == "hold"


class TestStrategyEval12H:
    @pytest.mark.asyncio
    async def test_12h_weak(self, strategy, config):
        """Low drop ratio at 12h → weak → adjust to 10%."""
        pos = _make_pos(hours_held=13)
        pos.evaluated_2h = True  # 2h already done
        pos.current_tp_pct = 21.0
        strategy._calc_5m_drop_ratio = AsyncMock(return_value=0.30)

        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )

        assert pos.evaluated_12h is True
        assert action.action == "adjust_tp"
        assert action.new_tp_pct == 10.0
        assert action.new_strength == "weak"

    @pytest.mark.asyncio
    async def test_12h_strong(self, strategy, config):
        """High drop ratio at 12h → strong → back to 33%."""
        pos = _make_pos(hours_held=13)
        pos.evaluated_2h = True
        pos.current_tp_pct = 21.0
        strategy._calc_5m_drop_ratio = AsyncMock(return_value=0.70)

        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )

        assert pos.evaluated_12h is True
        assert action.action == "adjust_tp"
        assert action.new_tp_pct == 33.0
        assert action.new_strength == "strong"


class TestStrategyMaxHold:
    @pytest.mark.asyncio
    async def test_max_hold_close(self, strategy, config):
        """Position exceeding max_hold_hours should be closed."""
        pos = _make_pos(hours_held=config.max_hold_hours + 1)
        pos.evaluated_2h = True
        pos.evaluated_12h = True

        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )

        assert action.action == "close"
        assert action.reason == "max_hold_time"


# ──────────────────────────────────────────────────────────────────
# Legacy path tests (monitor._update_dynamic_tp without strategy)
# ──────────────────────────────────────────────────────────────────

class TestLegacyDynamicTP:
    """Verify the legacy code path still works when no strategy is injected."""

    @pytest.mark.asyncio
    async def test_legacy_2h_medium(self, monitor_legacy):
        pos = _make_pos(hours_held=2.5)
        monitor_legacy._calc_5m_drop_ratio = AsyncMock(return_value=0.30)
        monitor_legacy._replace_tp_order = AsyncMock()

        await monitor_legacy._update_dynamic_tp(pos, datetime.now(timezone.utc))

        assert pos.evaluated_2h is True
        assert pos.strength == "medium"
        assert pos.current_tp_pct == 21.0
        monitor_legacy._replace_tp_order.assert_called_once_with(pos)


# ──────────────────────────────────────────────────────────────────
# _calc_5m_drop_ratio tests (strategy-level helper)
# ──────────────────────────────────────────────────────────────────

class TestCalc5mDropRatio:
    @pytest.mark.asyncio
    async def test_normal_calculation(self):
        """Verify drop ratio calculation with mock klines."""
        client = AsyncMock()
        klines = []
        for close_price in [99000, 95000, 93000, 97000, 92000]:
            k = MagicMock()
            k.close = Decimal(str(close_price))
            klines.append(k)

        client.get_klines = AsyncMock(return_value=klines)

        entry_price = Decimal("100000")
        threshold = 0.055  # 5.5% drop

        result = await SurgeShortStrategy._calc_5m_drop_ratio(
            client,
            "BTCUSDT",
            datetime.now(timezone.utc) - timedelta(hours=2),
            datetime.now(timezone.utc),
            entry_price,
            threshold,
        )

        # 95000: -5% (no), 93000: -7% (yes), 92000: -8% (yes) = 2/5 = 0.4
        assert result is not None
        assert abs(result - 0.4) < 0.01

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_klines(self):
        client = AsyncMock()
        client.get_klines = AsyncMock(return_value=[])
        result = await SurgeShortStrategy._calc_5m_drop_ratio(
            client,
            "BTCUSDT",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
            Decimal("100000"),
            0.05,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        client = AsyncMock()
        client.get_klines = AsyncMock(side_effect=Exception("network"))
        result = await SurgeShortStrategy._calc_5m_drop_ratio(
            client,
            "BTCUSDT",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
            Decimal("100000"),
            0.05,
        )
        assert result is None
