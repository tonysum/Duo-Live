"""Tests for TP/SL false trigger detection fix.

Validates that when an algo order disappears from the open list,
the monitor checks exchange position status before deciding:
  - Position closed (amt=0) → Real trigger, mark closed
  - Position still open (amt≠0) → Manual cancel, auto re-place order
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


def _make_monitor(config, **overrides):
    client = AsyncMock()
    executor = MagicMock()
    notifier = AsyncMock()
    notifier.notify_tp_triggered = AsyncMock()
    notifier.notify_sl_triggered = AsyncMock()
    notifier.send = AsyncMock()
    mon = LivePositionMonitor(
        client=client,
        executor=executor,
        config=config,
        notifier=notifier,
        **overrides,
    )
    return mon


def _make_filled_pos(
    symbol="BTCUSDT", side="SHORT", tp_algo=100, sl_algo=200,
    entry_price=Decimal("50000"), tp_pct=33.0,
) -> TrackedPosition:
    return TrackedPosition(
        symbol=symbol,
        entry_order_id=1,
        side=side,
        quantity="0.01",
        deferred_tp_sl={},
        entry_filled=True,
        entry_price=entry_price,
        entry_fill_time=datetime.now(timezone.utc) - timedelta(hours=1),
        tp_sl_placed=True,
        tp_algo_id=tp_algo,
        sl_algo_id=sl_algo,
        current_tp_pct=tp_pct,
    )


def _mock_position_risk(symbol, position_amt):
    """Create a mock PositionRisk object."""
    pr = MagicMock()
    pr.symbol = symbol
    pr.position_amt = str(position_amt)
    return pr


# ──────────────────────────────────────────────────────────────────
# _get_exchange_position_amt
# ──────────────────────────────────────────────────────────────────

class TestGetExchangePositionAmt:
    @pytest.mark.asyncio
    async def test_returns_position_amt(self, config):
        mon = _make_monitor(config)
        pr = _mock_position_risk("BTCUSDT", -0.01)
        mon.client.get_position_risk = AsyncMock(return_value=[pr])

        result = await mon._get_exchange_position_amt("BTCUSDT")
        assert result == 0.01

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_position(self, config):
        mon = _make_monitor(config)
        pr = _mock_position_risk("BTCUSDT", 0)
        mon.client.get_position_risk = AsyncMock(return_value=[pr])

        result = await mon._get_exchange_position_amt("BTCUSDT")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_when_empty_list(self, config):
        mon = _make_monitor(config)
        mon.client.get_position_risk = AsyncMock(return_value=[])

        result = await mon._get_exchange_position_amt("BTCUSDT")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_nonzero_on_api_error(self, config):
        """Fail-safe: if API fails, assume position exists."""
        mon = _make_monitor(config)
        mon.client.get_position_risk = AsyncMock(side_effect=Exception("network"))

        result = await mon._get_exchange_position_amt("BTCUSDT")
        assert result == 1.0  # conservative fallback


# ──────────────────────────────────────────────────────────────────
# TP trigger detection
# ──────────────────────────────────────────────────────────────────

class TestTPTriggerDetection:
    @pytest.mark.asyncio
    async def test_tp_triggered_real(self, config):
        """TP disappears + position amt == 0 → real trigger, mark closed."""
        mon = _make_monitor(config)
        pos = _make_filled_pos()
        mon._positions["BTCUSDT"] = pos

        # TP algo not in open list (disappeared)
        mon.client.get_open_algo_orders = AsyncMock(return_value=[])
        # Position is closed on exchange
        pr = _mock_position_risk("BTCUSDT", 0)
        mon.client.get_position_risk = AsyncMock(return_value=[pr])

        # Stub strategy to avoid strategy evaluation
        mon.strategy = None
        mon._update_dynamic_tp = AsyncMock()

        await mon._check_position(pos)

        assert pos.tp_triggered is True
        assert pos.closed is True
        mon.notifier.notify_tp_triggered.assert_called_once()

    @pytest.mark.asyncio
    async def test_tp_cancelled_manual(self, config):
        """TP disappears + position amt != 0 → manual cancel, re-place TP.

        Note: when both TP and SL disappear simultaneously (empty algo list),
        both are detected and re-placed in the same cycle (if/if, not if/elif).
        """
        mon = _make_monitor(config)
        pos = _make_filled_pos()
        mon._positions["BTCUSDT"] = pos

        # No algo orders in open list (both TP and SL disappeared)
        mon.client.get_open_algo_orders = AsyncMock(return_value=[])
        # Position still exists on exchange
        pr = _mock_position_risk("BTCUSDT", -0.01)
        mon.client.get_position_risk = AsyncMock(return_value=[pr])

        # Mock _re_place_single_order to verify it's called
        mon._re_place_single_order = AsyncMock()
        mon.strategy = None
        mon._update_dynamic_tp = AsyncMock()

        await mon._check_position(pos)

        assert pos.tp_triggered is False
        assert pos.closed is False
        # Both TP and SL should be re-placed when both disappear
        assert mon._re_place_single_order.call_count == 2
        calls = [c.args for c in mon._re_place_single_order.call_args_list]
        assert (pos, "tp") in calls
        assert (pos, "sl") in calls


# ──────────────────────────────────────────────────────────────────
# SL trigger detection
# ──────────────────────────────────────────────────────────────────

class TestSLTriggerDetection:
    @pytest.mark.asyncio
    async def test_sl_triggered_real(self, config):
        """SL disappears + position amt == 0 → real trigger, mark closed."""
        mon = _make_monitor(config)
        pos = _make_filled_pos()
        mon._positions["BTCUSDT"] = pos

        # Only TP is still open (SL disappeared)
        tp_algo = MagicMock()
        tp_algo.algo_id = 100
        mon.client.get_open_algo_orders = AsyncMock(return_value=[tp_algo])
        # Position is closed on exchange
        pr = _mock_position_risk("BTCUSDT", 0)
        mon.client.get_position_risk = AsyncMock(return_value=[pr])
        mon.client.cancel_algo_order = AsyncMock()

        mon.strategy = None
        mon._update_dynamic_tp = AsyncMock()

        await mon._check_position(pos)

        assert pos.sl_triggered is True
        assert pos.closed is True
        mon.notifier.notify_sl_triggered.assert_called_once()
        # TP should be cancelled since it was still open
        mon.client.cancel_algo_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_sl_cancelled_manual(self, config):
        """SL disappears + position amt != 0 → manual cancel, re-place SL."""
        mon = _make_monitor(config)
        pos = _make_filled_pos()
        mon._positions["BTCUSDT"] = pos

        # Only TP is still open (SL disappeared)
        tp_algo = MagicMock()
        tp_algo.algo_id = 100
        mon.client.get_open_algo_orders = AsyncMock(return_value=[tp_algo])
        # Position still exists on exchange
        pr = _mock_position_risk("BTCUSDT", -0.01)
        mon.client.get_position_risk = AsyncMock(return_value=[pr])

        mon._re_place_single_order = AsyncMock()
        mon.strategy = None
        mon._update_dynamic_tp = AsyncMock()

        await mon._check_position(pos)

        assert pos.sl_triggered is False
        assert pos.closed is False
        mon._re_place_single_order.assert_called_once_with(pos, "sl")


# ──────────────────────────────────────────────────────────────────
# _re_place_single_order
# ──────────────────────────────────────────────────────────────────

class TestRePlaceSingleOrder:
    @pytest.mark.asyncio
    async def test_re_place_tp_short(self, config):
        """Re-place TP for a SHORT position."""
        mon = _make_monitor(config)
        pos = _make_filled_pos(entry_price=Decimal("50000"), tp_pct=33.0)
        mon._round_trigger_price = AsyncMock(side_effect=lambda s, p: p)
        mon._round_quantity = AsyncMock(side_effect=lambda s, q: q)
        mon.client.get_position_mode = AsyncMock(return_value=False)

        new_order = MagicMock()
        new_order.algo_id = 999
        mon.client.place_algo_order = AsyncMock(return_value=new_order)

        await mon._re_place_single_order(pos, "tp")

        assert pos.tp_algo_id == 999
        mon.client.place_algo_order.assert_called_once()
        call_kwargs = mon.client.place_algo_order.call_args.kwargs
        assert call_kwargs["type"] == "TAKE_PROFIT_MARKET"
        assert call_kwargs["side"] == "BUY"  # Close side for SHORT

    @pytest.mark.asyncio
    async def test_re_place_sl_long(self, config):
        """Re-place SL for a LONG position."""
        mon = _make_monitor(config)
        pos = _make_filled_pos(
            side="LONG", entry_price=Decimal("50000"),
        )
        mon._round_trigger_price = AsyncMock(side_effect=lambda s, p: p)
        mon._round_quantity = AsyncMock(side_effect=lambda s, q: q)
        mon.client.get_position_mode = AsyncMock(return_value=False)

        new_order = MagicMock()
        new_order.algo_id = 888
        mon.client.place_algo_order = AsyncMock(return_value=new_order)

        await mon._re_place_single_order(pos, "sl")

        assert pos.sl_algo_id == 888
        call_kwargs = mon.client.place_algo_order.call_args.kwargs
        assert call_kwargs["type"] == "STOP_MARKET"
        assert call_kwargs["side"] == "SELL"  # Close side for LONG

    @pytest.mark.asyncio
    async def test_re_place_fails_gracefully(self, config):
        """If re-place fails, position remains open and unmodified."""
        mon = _make_monitor(config)
        pos = _make_filled_pos()
        old_tp_id = pos.tp_algo_id

        mon._round_trigger_price = AsyncMock(side_effect=lambda s, p: p)
        mon._round_quantity = AsyncMock(side_effect=lambda s, q: q)
        mon.client.get_position_mode = AsyncMock(return_value=False)
        mon.client.place_algo_order = AsyncMock(side_effect=Exception("API error"))

        await mon._re_place_single_order(pos, "tp")

        # TP algo ID should NOT have been updated
        assert pos.tp_algo_id == old_tp_id
        assert pos.closed is False
        # Emergency notification should be sent
        mon.notifier.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_entry_price_skips(self, config):
        """If no entry price, skip re-place."""
        mon = _make_monitor(config)
        pos = _make_filled_pos()
        pos.entry_price = None

        await mon._re_place_single_order(pos, "tp")

        # Nothing should have been called
        mon.client.place_algo_order = AsyncMock()
        mon.client.place_algo_order.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Fallback: algo_id=None (e.g. failed _replace_tp_order)
# ──────────────────────────────────────────────────────────────────

class TestAlgoIdNoneFallback:
    @pytest.mark.asyncio
    async def test_tp_algo_id_none_triggers_replace(self, config):
        """tp_algo_id=None + tp_sl_placed=True → auto re-place TP."""
        mon = _make_monitor(config)
        pos = _make_filled_pos()
        pos.tp_algo_id = None  # Lost due to failed replacement

        mon._positions["BTCUSDT"] = pos
        # SL still in open list so Section 2 doesn't catch it
        sl_algo = MagicMock()
        sl_algo.algo_id = 200
        mon.client.get_open_algo_orders = AsyncMock(return_value=[sl_algo])
        mon._re_place_single_order = AsyncMock()
        mon.strategy = None
        mon._update_dynamic_tp = AsyncMock()

        await mon._check_position(pos)

        assert pos.closed is False
        # Should attempt to re-place TP
        assert any(
            call.args == (pos, "tp")
            for call in mon._re_place_single_order.call_args_list
        )

    @pytest.mark.asyncio
    async def test_sl_algo_id_none_triggers_replace(self, config):
        """sl_algo_id=None + tp_sl_placed=True → auto re-place SL."""
        mon = _make_monitor(config)
        pos = _make_filled_pos()
        pos.sl_algo_id = None  # Lost

        mon._positions["BTCUSDT"] = pos
        # TP is still open
        tp_algo = MagicMock()
        tp_algo.algo_id = 100
        mon.client.get_open_algo_orders = AsyncMock(return_value=[tp_algo])
        mon._re_place_single_order = AsyncMock()
        mon.strategy = None
        mon._update_dynamic_tp = AsyncMock()

        await mon._check_position(pos)

        assert pos.closed is False
        assert any(
            call.args == (pos, "sl")
            for call in mon._re_place_single_order.call_args_list
        )

    @pytest.mark.asyncio
    async def test_both_present_no_fallback(self, config):
        """Both algo IDs present → no fallback needed."""
        mon = _make_monitor(config)
        pos = _make_filled_pos(tp_algo=100, sl_algo=200)

        mon._positions["BTCUSDT"] = pos
        # Both still open
        tp_algo = MagicMock()
        tp_algo.algo_id = 100
        sl_algo = MagicMock()
        sl_algo.algo_id = 200
        mon.client.get_open_algo_orders = AsyncMock(return_value=[tp_algo, sl_algo])
        mon._re_place_single_order = AsyncMock()
        mon.strategy = None
        mon._update_dynamic_tp = AsyncMock()

        await mon._check_position(pos)

        mon._re_place_single_order.assert_not_called()
