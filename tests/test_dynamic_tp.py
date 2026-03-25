"""Tests for RollingLiveStrategy.evaluate_position (R24 TP decay, max hold, trailing)."""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from live.live_config import LiveTradingConfig
from live.live_position_monitor import TrackedPosition
from live.rolling_config import RollingLiveConfig
from live.rolling_live_strategy import RollingLiveStrategy


@pytest.fixture
def config():
    return LiveTradingConfig()


@pytest.fixture
def rolling_cfg():
    return RollingLiveConfig()


@pytest.fixture
def strategy(rolling_cfg):
    return RollingLiveStrategy(config=rolling_cfg)


def _make_pos(
    hours_held: float,
    tp_pct: float,
    *,
    tp_sl_placed: bool = True,
) -> TrackedPosition:
    now = datetime.now(timezone.utc)
    return TrackedPosition(
        symbol="BTCUSDT",
        entry_order_id=1,
        side="SHORT",
        quantity="0.01",
        deferred_tp_sl={},
        entry_filled=True,
        entry_price=Decimal("100000"),
        entry_fill_time=now - timedelta(hours=hours_held),
        tp_sl_placed=tp_sl_placed,
        tp_algo_id=111,
        current_tp_pct=tp_pct,
    )


class TestR24EvalBasics:
    @pytest.mark.asyncio
    async def test_hold_if_no_entry_fill_time(self, strategy, config):
        pos = _make_pos(hours_held=3, tp_pct=34.0)
        pos.entry_fill_time = None
        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )
        assert action.action == "hold"

    @pytest.mark.asyncio
    async def test_max_hold_close(self, strategy, config):
        rc = strategy.config
        max_h = rc.max_hold_days * 24 + 1
        pos = _make_pos(hours_held=max_h, tp_pct=34.0)
        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )
        assert action.action == "close"
        assert action.reason == "max_hold_time"

    @pytest.mark.asyncio
    async def test_tp_decay_after_threshold_hours(self, strategy, config):
        """After tp_hours_threshold, target TP becomes tp_reduced (%)."""
        rc = strategy.config
        pos = _make_pos(
            hours_held=float(rc.tp_hours_threshold) + 1.0,
            tp_pct=rc.tp_initial * 100,
        )
        target = rc.tp_reduced * 100
        action = await strategy.evaluate_position(
            AsyncMock(), pos, config, datetime.now(timezone.utc),
        )
        assert action.action == "adjust_tp"
        assert abs(action.new_tp_pct - target) < 0.01
        assert action.new_strength == "reduced"

    @pytest.mark.asyncio
    async def test_hold_when_tp_already_matches_initial(self, strategy, config):
        rc = strategy.config
        pos = _make_pos(
            hours_held=float(rc.tp_hours_threshold) - 1.0,
            tp_pct=rc.tp_initial * 100,
        )
        client = AsyncMock()
        t = MagicMock()
        t.price = "95000"
        client.get_ticker_price = AsyncMock(return_value=t)
        action = await strategy.evaluate_position(
            client, pos, config, datetime.now(timezone.utc),
        )
        # Matches initial TP and below trailing/add thresholds → hold
        assert action.action == "hold"


class TestR24Trailing:
    @pytest.mark.asyncio
    async def test_trailing_close_on_bounce(self, strategy, config):
        """After activation drop, price bouncing above trailing line closes."""
        rc = strategy.config
        entry_f = 100000.0
        pos = _make_pos(hours_held=1.0, tp_pct=rc.tp_initial * 100)
        pos.entry_price = Decimal(str(entry_f))
        # Need drop_from_entry >= trailing_activation_pct
        low = entry_f * (1.0 - rc.trailing_activation_pct - 0.02)
        pos.lowest_price = Decimal(str(low))
        trailing_line = low * (1.0 + rc.trailing_distance_pct)
        bounce = trailing_line * 1.001

        client = AsyncMock()
        t = MagicMock()
        t.price = str(bounce)
        client.get_ticker_price = AsyncMock(return_value=t)

        action = await strategy.evaluate_position(
            client, pos, config, datetime.now(timezone.utc),
        )
        assert action.action == "close"
        assert action.reason == "trailing_stop"
