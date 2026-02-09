"""Tests for WebSocket ORDER_TRADE_UPDATE event handling.

Validates that handle_order_update correctly:
  - Detects entry fills
  - Uses `ot` (original order type) for TP/SL detection
  - Reads `rp` (realized PnL)
  - Handles EXPIRED/CANCELED events
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from live.live_position_monitor import LivePositionMonitor, TrackedPosition
from live.live_config import LiveTradingConfig


@pytest.fixture
def config():
    return LiveTradingConfig()


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.get_position_mode = AsyncMock(return_value=False)  # one-way mode
    client.cancel_algo_order = AsyncMock()
    return client


@pytest.fixture
def monitor(config, mock_client):
    executor = MagicMock()
    mon = LivePositionMonitor(
        client=mock_client,
        executor=executor,
        config=config,
        notifier=None,
        store=None,
    )
    return mon


def _make_tracked(symbol="BTCUSDT", entry_order_id=12345, side="SHORT"):
    """Create a TrackedPosition for testing."""
    return TrackedPosition(
        symbol=symbol,
        entry_order_id=entry_order_id,
        side=side,
        quantity="0.01",
        deferred_tp_sl={},
    )


def _make_order_event(
    symbol="BTCUSDT",
    order_id=12345,
    exec_type="TRADE",
    status="FILLED",
    avg_price="100000",
    orig_type="LIMIT",
    client_id="entry_abc123",
    realized_pnl="0",
    pos_side="SHORT",
):
    """Build a Binance ORDER_TRADE_UPDATE event dict."""
    return {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1700000000000,
        "T": 1700000000000,
        "o": {
            "s": symbol,
            "i": order_id,
            "c": client_id,
            "S": "SELL",
            "o": "MARKET",
            "ot": orig_type,
            "f": "GTC",
            "q": "0.01",
            "p": "0",
            "ap": avg_price,
            "sp": "0",
            "x": exec_type,
            "X": status,
            "l": "0.01",
            "z": "0.01",
            "L": avg_price,
            "N": "USDT",
            "n": "0.04",
            "T": 1700000000000,
            "t": 1,
            "rp": realized_pnl,
            "ps": pos_side,
            "wt": "CONTRACT_PRICE",
            "R": False,
            "m": False,
        },
    }


class TestEntryFill:
    """Test entry order fill detection."""

    @pytest.mark.asyncio
    async def test_entry_fill_sets_price_and_time(self, monitor):
        pos = _make_tracked(entry_order_id=12345)
        monitor._positions["BTCUSDT"] = pos

        event = _make_order_event(order_id=12345, avg_price="99500.50")
        await monitor.handle_order_update(event)

        assert pos.entry_filled is True
        assert pos.entry_price == Decimal("99500.50")
        assert pos.entry_fill_time is not None

    @pytest.mark.asyncio
    async def test_entry_fill_wrong_order_id_ignored(self, monitor):
        pos = _make_tracked(entry_order_id=12345)
        monitor._positions["BTCUSDT"] = pos

        event = _make_order_event(order_id=99999, avg_price="99500")
        await monitor.handle_order_update(event)

        assert pos.entry_filled is False
        assert pos.entry_price is None

    @pytest.mark.asyncio
    async def test_unknown_symbol_ignored(self, monitor):
        """Event for a symbol we're not tracking should be silently ignored."""
        event = _make_order_event(symbol="ETHUSDT", order_id=12345)
        # Should not raise
        await monitor.handle_order_update(event)


class TestTPSLDetection:
    """Test TP/SL detection using `ot` (original order type) field."""

    @pytest.mark.asyncio
    async def test_tp_detected_by_ot_field(self, monitor, mock_client):
        pos = _make_tracked()
        pos.entry_filled = True
        pos.entry_price = Decimal("100000")
        pos.entry_fill_time = datetime.now(timezone.utc)
        pos.tp_algo_id = 111
        pos.sl_algo_id = 222
        pos.tp_sl_placed = True
        monitor._positions["BTCUSDT"] = pos

        # TP fill event — `ot` = TAKE_PROFIT_MARKET
        event = _make_order_event(
            order_id=999,
            orig_type="TAKE_PROFIT_MARKET",
            avg_price="67000",
            realized_pnl="330.00",
            client_id="tp_abc123",
        )
        await monitor.handle_order_update(event)

        assert pos.tp_triggered is True
        assert pos.closed is True
        # SL should be cancelled
        mock_client.cancel_algo_order.assert_called_once_with("BTCUSDT", 222)

    @pytest.mark.asyncio
    async def test_sl_detected_by_ot_field(self, monitor, mock_client):
        pos = _make_tracked()
        pos.entry_filled = True
        pos.entry_price = Decimal("100000")
        pos.entry_fill_time = datetime.now(timezone.utc)
        pos.tp_algo_id = 111
        pos.sl_algo_id = 222
        pos.tp_sl_placed = True
        monitor._positions["BTCUSDT"] = pos

        # SL fill event — `ot` = STOP_MARKET
        event = _make_order_event(
            order_id=888,
            orig_type="STOP_MARKET",
            avg_price="118000",
            realized_pnl="-180.00",
            client_id="sl_abc123",
        )
        await monitor.handle_order_update(event)

        assert pos.sl_triggered is True
        assert pos.closed is True
        # TP should be cancelled
        mock_client.cancel_algo_order.assert_called_once_with("BTCUSDT", 111)

    @pytest.mark.asyncio
    async def test_tp_detected_by_client_id_fallback(self, monitor, mock_client):
        """Even if ot is unknown, client_order_id prefix `tp_` should work."""
        pos = _make_tracked()
        pos.entry_filled = True
        pos.entry_price = Decimal("100000")
        pos.entry_fill_time = datetime.now(timezone.utc)
        pos.tp_algo_id = 111
        pos.tp_sl_placed = True
        monitor._positions["BTCUSDT"] = pos

        event = _make_order_event(
            order_id=999,
            orig_type="MARKET",  # ot doesn't reveal TP
            client_id="tp_xyz789",
        )
        await monitor.handle_order_update(event)

        assert pos.tp_triggered is True


class TestExpiredCanceled:
    """Test EXPIRED/CANCELED event handling."""

    @pytest.mark.asyncio
    async def test_entry_expired_logged(self, monitor):
        pos = _make_tracked(entry_order_id=12345)
        monitor._positions["BTCUSDT"] = pos

        event = _make_order_event(
            order_id=12345,
            exec_type="EXPIRED",
            status="EXPIRED",
        )

        # Should not crash — just logs warning
        await monitor.handle_order_update(event)
        assert pos.entry_filled is False

    @pytest.mark.asyncio
    async def test_entry_canceled_logged(self, monitor):
        pos = _make_tracked(entry_order_id=12345)
        monitor._positions["BTCUSDT"] = pos

        event = _make_order_event(
            order_id=12345,
            exec_type="CANCELED",
            status="CANCELED",
        )
        await monitor.handle_order_update(event)
        assert pos.entry_filled is False
