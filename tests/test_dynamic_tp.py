"""Tests for dynamic TP evaluation (2h/12h coin strength).

Validates that _update_dynamic_tp correctly:
  - Skips if hold_hours < 2
  - Evaluates strength at 2h → strong (33%) or medium (21%)
  - Evaluates strength at 12h → strong (33%) or weak (10%)
  - Only evaluates once per checkpoint
  - Calls _replace_tp_order when TP changes
"""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from live.live_position_monitor import LivePositionMonitor, TrackedPosition
from live.live_config import LiveTradingConfig


@pytest.fixture
def config():
    return LiveTradingConfig()


@pytest.fixture
def monitor(config):
    client = AsyncMock()
    executor = MagicMock()
    mon = LivePositionMonitor(
        client=client, executor=executor, config=config,
    )
    return mon


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


class TestDynamicTPSkip:
    @pytest.mark.asyncio
    async def test_skip_if_no_entry_fill_time(self, monitor):
        pos = _make_pos(hours_held=3)
        pos.entry_fill_time = None
        await monitor._update_dynamic_tp(pos, datetime.now(timezone.utc))
        assert pos.evaluated_2h is False

    @pytest.mark.asyncio
    async def test_skip_if_under_2h(self, monitor):
        pos = _make_pos(hours_held=1.5)
        await monitor._update_dynamic_tp(pos, datetime.now(timezone.utc))
        assert pos.evaluated_2h is False
        assert pos.current_tp_pct == 33.0


class TestDynamicTP2H:
    @pytest.mark.asyncio
    async def test_2h_strong(self, monitor):
        """High drop ratio → strong → keep 33%."""
        pos = _make_pos(hours_held=2.5)
        # Mock klines: 70% of candles dropped > threshold → strong
        monitor._calc_5m_drop_ratio = AsyncMock(return_value=0.70)
        monitor._replace_tp_order = AsyncMock()

        await monitor._update_dynamic_tp(pos, datetime.now(timezone.utc))

        assert pos.evaluated_2h is True
        assert pos.strength == "strong"
        assert pos.current_tp_pct == 33.0
        # TP didn't change (33 → 33), no replacement
        monitor._replace_tp_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_2h_medium(self, monitor):
        """Low drop ratio → medium → adjust to 21%."""
        pos = _make_pos(hours_held=2.5)
        monitor._calc_5m_drop_ratio = AsyncMock(return_value=0.30)
        monitor._replace_tp_order = AsyncMock()

        await monitor._update_dynamic_tp(pos, datetime.now(timezone.utc))

        assert pos.evaluated_2h is True
        assert pos.strength == "medium"
        assert pos.current_tp_pct == 21.0
        # TP changed (33 → 21), should replace
        monitor._replace_tp_order.assert_called_once_with(pos)

    @pytest.mark.asyncio
    async def test_2h_only_evaluates_once(self, monitor):
        """Even if called multiple times, 2h eval only runs once."""
        pos = _make_pos(hours_held=3)
        pos.evaluated_2h = True  # Already done
        monitor._calc_5m_drop_ratio = AsyncMock(return_value=0.30)
        monitor._replace_tp_order = AsyncMock()

        await monitor._update_dynamic_tp(pos, datetime.now(timezone.utc))

        monitor._calc_5m_drop_ratio.assert_not_called()


class TestDynamicTP12H:
    @pytest.mark.asyncio
    async def test_12h_weak(self, monitor):
        """Low drop ratio at 12h → weak → adjust to 10%."""
        pos = _make_pos(hours_held=13)
        pos.evaluated_2h = True  # 2h already done
        pos.current_tp_pct = 21.0
        monitor._calc_5m_drop_ratio = AsyncMock(return_value=0.30)
        monitor._replace_tp_order = AsyncMock()

        await monitor._update_dynamic_tp(pos, datetime.now(timezone.utc))

        assert pos.evaluated_12h is True
        assert pos.strength == "weak"
        assert pos.current_tp_pct == 10.0
        monitor._replace_tp_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_12h_strong(self, monitor):
        """High drop ratio at 12h → strong → back to 33%."""
        pos = _make_pos(hours_held=13)
        pos.evaluated_2h = True
        pos.current_tp_pct = 21.0
        monitor._calc_5m_drop_ratio = AsyncMock(return_value=0.70)
        monitor._replace_tp_order = AsyncMock()

        await monitor._update_dynamic_tp(pos, datetime.now(timezone.utc))

        assert pos.evaluated_12h is True
        assert pos.strength == "strong"
        assert pos.current_tp_pct == 33.0
        monitor._replace_tp_order.assert_called_once()


class TestCalc5mDropRatio:
    @pytest.mark.asyncio
    async def test_normal_calculation(self, monitor):
        """Verify drop ratio calculation with mock klines."""
        # Mock kline objects
        klines = []
        for close_price in [99000, 95000, 93000, 97000, 92000]:
            k = MagicMock()
            k.close = Decimal(str(close_price))
            klines.append(k)

        monitor.client.get_klines = AsyncMock(return_value=klines)

        entry_price = Decimal("100000")
        threshold = 0.055  # 5.5% drop

        result = await monitor._calc_5m_drop_ratio(
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
    async def test_returns_none_on_empty_klines(self, monitor):
        monitor.client.get_klines = AsyncMock(return_value=[])
        result = await monitor._calc_5m_drop_ratio(
            "BTCUSDT",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
            Decimal("100000"),
            0.05,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, monitor):
        monitor.client.get_klines = AsyncMock(side_effect=Exception("network"))
        result = await monitor._calc_5m_drop_ratio(
            "BTCUSDT",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
            Decimal("100000"),
            0.05,
        )
        assert result is None
