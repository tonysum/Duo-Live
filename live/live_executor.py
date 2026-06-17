"""Live order executor — real order placement with TP/SL conditional orders.

Places a SHORT entry (LIMIT SELL) with automatic take-profit and stop-loss:
  - Entry: LIMIT SELL @ given price
  - TP: TAKE_PROFIT (limit) @ entry × (1 − tp_pct/100), trigger=price
  - SL: STOP (limit) @ entry × (1 + sl_pct/100), trigger=price

Automatically detects position mode (one-way vs hedge) and adapts positionSide.
"""

from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any

from .binance_client import BinanceFuturesClient, BinanceAPIError
from .binance_models import AlgoOrderResponse, OrderResponse

logger = logging.getLogger(__name__)

TP_ALGO_TYPE = "TAKE_PROFIT"
SL_ALGO_TYPE = "STOP"


def is_tp_algo_type(order_type: str) -> bool:
    return order_type in (TP_ALGO_TYPE, "TAKE_PROFIT_MARKET")


def is_sl_algo_type(order_type: str) -> bool:
    return order_type in (SL_ALGO_TYPE, "STOP_MARKET")


class LiveOrderExecutor:
    """Execute live orders on Binance Futures with TP/SL."""

    # Re-query position mode after this many seconds (handles live mode switches)
    _POSITION_MODE_TTL: int = 3600

    def __init__(self, client: BinanceFuturesClient, leverage: int = 4):
        self.client = client
        self.leverage = leverage
        self._precision_cache: dict[str, tuple[int, int]] = {}  # symbol → (price_prec, qty_prec)
        self._precision_cache_loaded: bool = False  # True after first bulk load
        self._position_mode: bool | None = None  # True=hedge, False=one-way
        self._position_mode_ts: float = 0.0  # epoch of last fetch

    async def _get_precision(self, symbol: str) -> tuple[int, int]:
        """Get (price_precision, quantity_precision) for a symbol.

        I: On first call, bulk-loads ALL symbols into cache with a single
        exchange_info request, avoiding one request per unknown symbol.
        """
        if not self._precision_cache_loaded:
            info = await self.client.get_exchange_info()
            for s in info.symbols:
                self._precision_cache[s.symbol] = (s.price_precision, s.quantity_precision)
            self._precision_cache_loaded = True
            logger.debug("精度缓存已加载: %d 个交易对", len(self._precision_cache))

        prec = self._precision_cache.get(symbol)
        if prec is None:
            raise ValueError(f"Symbol {symbol} not found in exchange info")
        return prec

    async def _get_position_side(self, side: str = "SHORT") -> str:
        """Get the correct positionSide based on account mode.

        Returns "SHORT"/"LONG" for hedge mode, "BOTH" for one-way mode.
        J: Result is cached with a 1h TTL so live mode changes are picked up.
        """
        now = time.monotonic()
        if self._position_mode is None or now - self._position_mode_ts > self._POSITION_MODE_TTL:
            self._position_mode = await self.client.get_position_mode()
            self._position_mode_ts = now
            mode_name = "双向持仓 (Hedge)" if self._position_mode else "单向持仓 (One-way)"
            logger.info("📌 持仓模式: %s", mode_name)
        return side.upper() if self._position_mode else "BOTH"

    def _round_price(self, price: Decimal, precision: int) -> Decimal:
        """Round price to the correct precision."""
        if precision == 0:
            return price.quantize(Decimal("1"), rounding=ROUND_DOWN)
        fmt = Decimal("1e-{}".format(precision))
        return price.quantize(fmt, rounding=ROUND_DOWN)

    def _round_qty(self, qty: Decimal, precision: int) -> Decimal:
        """Round quantity to the correct precision."""
        if precision == 0:
            return qty.quantize(Decimal("1"), rounding=ROUND_DOWN)
        fmt = Decimal("1e-{}".format(precision))
        return qty.quantize(fmt, rounding=ROUND_DOWN)

    async def open_position(
        self,
        symbol: str,
        price: Decimal,
        quantity: Decimal,
        side: str = "SHORT",
        tp_pct: float = 33.0,
        sl_pct: float = 18.0,
    ) -> dict[str, Any]:
        """Open a position with take-profit and stop-loss.

        Args:
            symbol: Trading pair, e.g. "BTCUSDT"
            price: Limit entry price
            quantity: Contract quantity
            side: "SHORT" or "LONG"
            tp_pct: Take-profit percentage (default 33%)
            sl_pct: Stop-loss percentage (default 18%)

        Returns:
            Dict with keys: entry_order, tp_order, sl_order.
        """
        side = side.upper()
        if side not in ("SHORT", "LONG"):
            raise ValueError(f"side must be 'SHORT' or 'LONG', got '{side}'")

        is_long = side == "LONG"

        # ── 0. Preparation ────────────────────────────────────────────
        price_prec, qty_prec = await self._get_precision(symbol)
        pos_side = await self._get_position_side(side)

        # Set margin type to ISOLATED (限制单仓最大亏损)
        try:
            await self.client.set_margin_type(symbol, "ISOLATED")
            logger.info("⚙️ 保证金模式已设为逐仓: %s", symbol)
        except BinanceAPIError as e:
            if e.code == -4046:
                logger.debug("逐仓模式已设置, 无需修改: %s", symbol)
            else:
                logger.warning("⚠️ 设置逐仓模式失败: %s", e)

        # Set leverage
        try:
            lev_resp = await self.client.set_leverage(symbol, self.leverage)
            logger.info("⚙️ 杠杆已设置: %s × %s", symbol, lev_resp.get("leverage", self.leverage))
        except BinanceAPIError as e:
            logger.warning("⚠️ 设置杠杆失败 (可忽略如果已设置): %s", e)

        # Round inputs
        entry_price = self._round_price(price, price_prec)
        entry_qty = self._round_qty(quantity, qty_prec)

        # Direction-dependent parameters
        # LONG:  entry=BUY,  close=SELL, TP above (+tp_pct), SL below (-sl_pct)
        # SHORT: entry=SELL, close=BUY,  TP below (-tp_pct), SL above (+sl_pct)
        entry_side = "BUY" if is_long else "SELL"
        close_side = "SELL" if is_long else "BUY"
        tp_mult = Decimal("1") + Decimal(str(tp_pct)) / Decimal("100") if is_long \
            else Decimal("1") - Decimal(str(tp_pct)) / Decimal("100")
        sl_mult = Decimal("1") - Decimal(str(sl_pct)) / Decimal("100") if is_long \
            else Decimal("1") + Decimal(str(sl_pct)) / Decimal("100")

        tp_price = self._round_price(entry_price * tp_mult, price_prec)
        sl_price = self._round_price(entry_price * sl_mult, price_prec)

        side_label = "LONG 📈" if is_long else "SHORT 📉"

        # Generate unique prefix for order tracking
        order_prefix = uuid.uuid4().hex[:8]

        logger.info(
            "📋 下单计划: %s %s @ %s, 数量=%s, TP=%s (%s%%), SL=%s (%s%%), positionSide=%s",
            symbol, side_label, entry_price, entry_qty,
            tp_price, tp_pct, sl_price, sl_pct, pos_side,
        )
        logger.info("📌 TP/SL 将在入场单成交后自动挂出")

        result: dict[str, Any] = {"side": side}

        # ── 1. Entry order: LIMIT (only) ──────────────────────────────
        try:
            entry_order = await self.client.place_order(
                symbol=symbol,
                side=entry_side,
                positionSide=pos_side,
                type="LIMIT",
                timeInForce="GTC",
                quantity=str(entry_qty),
                price=str(entry_price),
                newClientOrderId=f"entry_{order_prefix}",
            )
            result["entry_order"] = entry_order
            logger.info(
                "✅ 入场单已提交: orderId=%s, status=%s",
                entry_order.order_id, entry_order.status,
            )
        except (BinanceAPIError, Exception) as e:
            logger.error("❌ 入场单失败: %s", e)
            result["entry_order"] = None
            result["error"] = str(e)
            return result

        # ── 2. Return deferred TP/SL parameters ──────────────────────
        # LivePositionMonitor will place TP/SL after entry is FILLED
        result["deferred_tp_sl"] = {
            "symbol": symbol,
            "close_side": close_side,
            "pos_side": pos_side,
            "tp_price": str(tp_price),
            "sl_price": str(sl_price),
            "quantity": str(entry_qty),
            "order_prefix": order_prefix,
        }

        return result

    async def place_tp_sl_algo_order(
        self,
        symbol: str,
        close_side: str,
        pos_side: str,
        kind: str,
        trigger_price: str,
        quantity: str,
        client_algo_id: str,
    ) -> AlgoOrderResponse:
        """Place a limit TP/SL conditional algo order (TAKE_PROFIT or STOP)."""
        if kind not in ("tp", "sl"):
            raise ValueError(f"kind must be 'tp' or 'sl', got '{kind}'")
        algo_type = TP_ALGO_TYPE if kind == "tp" else SL_ALGO_TYPE
        label = "止盈" if kind == "tp" else "止损"
        order = await self.client.place_algo_order(
            symbol=symbol,
            side=close_side,
            positionSide=pos_side,
            type=algo_type,
            triggerPrice=trigger_price,
            price=trigger_price,
            timeInForce="GTC",
            quantity=quantity,
            reduceOnly="true",
            priceProtect="true",
            workingType="CONTRACT_PRICE",
            clientAlgoId=client_algo_id,
        )
        logger.info(
            "✅ %s限价单已挂出: algoId=%s, triggerPrice=%s, price=%s",
            label, order.algo_id, trigger_price, trigger_price,
        )
        return order

    async def place_tp_sl(
        self,
        symbol: str,
        close_side: str,
        pos_side: str,
        tp_price: str,
        sl_price: str,
        quantity: str,
        order_prefix: str,
    ) -> dict[str, Any]:
        """Place TP/SL limit algo orders (called by monitor after entry fills).

        Returns dict with tp_order and sl_order.
        """
        result: dict[str, Any] = {}

        try:
            tp_order = await self.place_tp_sl_algo_order(
                symbol=symbol,
                close_side=close_side,
                pos_side=pos_side,
                kind="tp",
                trigger_price=tp_price,
                quantity=quantity,
                client_algo_id=f"tp_{order_prefix}",
            )
            result["tp_order"] = tp_order
        except (BinanceAPIError, Exception) as e:
            logger.error("❌ 止盈单失败: %s", e)
            result["tp_order"] = None
            result["tp_error"] = str(e)

        try:
            sl_order = await self.place_tp_sl_algo_order(
                symbol=symbol,
                close_side=close_side,
                pos_side=pos_side,
                kind="sl",
                trigger_price=sl_price,
                quantity=quantity,
                client_algo_id=f"sl_{order_prefix}",
            )
            result["sl_order"] = sl_order
        except (BinanceAPIError, Exception) as e:
            logger.error("❌ 止损单失败: %s", e)
            result["sl_order"] = None
            result["sl_error"] = str(e)

        return result

    async def open_short_with_tp_sl(
        self,
        symbol: str,
        price: Decimal,
        quantity: Decimal,
        tp_pct: float = 33.0,
        sl_pct: float = 18.0,
    ) -> dict[str, Any]:
        """Backward-compatible wrapper — opens a SHORT position."""
        return await self.open_position(
            symbol=symbol, price=price, quantity=quantity,
            side="SHORT", tp_pct=tp_pct, sl_pct=sl_pct,
        )


