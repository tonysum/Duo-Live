"""Binance Futures API Client — market data + authenticated trading.

Market data endpoints (no authentication):
  - get_exchange_info()
  - get_klines()
  - get_ticker_price()

Authenticated endpoints (HMAC-SHA256):
  - place_order()       — regular orders (LIMIT, MARKET)
  - place_algo_order()  — conditional orders (STOP_MARKET, TAKE_PROFIT_MARKET)
  - query_order()
  - get_open_orders()
  - get_position_risk()
  - cancel_order()
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import hmac
import os
import time
import urllib.parse
from decimal import Decimal
from typing import Any

import httpx

from .binance_models import (
    AlgoOrderResponse,
    ExchangeInfoResponse,
    Kline,
    OrderResponse,
    PositionRisk,
    TickerPrice,
)

logger = logging.getLogger(__name__)


class BinanceAPIError(Exception):
    """Binance API error with code and message."""

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Binance API Error [{code}]: {msg}")


class BinanceConnectionError(Exception):
    """Raised when the network connection to Binance fails."""

    def __init__(self, detail: str = ""):
        msg = "无法连接到 Binance API，请检查网络/VPN"
        if detail:
            msg += f" ({detail})"
        super().__init__(msg)


class BinanceFuturesClient:
    """Async client for Binance USDS-Margined Futures API.

    Supports both public market-data and authenticated trade/account endpoints.

    Example:
        async with BinanceFuturesClient() as client:
            ticker = await client.get_ticker_price("BTCUSDT")
            orders = await client.get_open_orders("BTCUSDT")
    """

    BASE_URL = "https://fapi.binance.com"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = self.BASE_URL
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BINANCE_API_SECRET", "")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BinanceFuturesClient:
        headers = {}
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers=headers,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")
        return self._client

    # =========================================================================
    # Request helpers
    # =========================================================================

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add timestamp and HMAC-SHA256 signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    _MAX_RETRIES = 3
    _RETRY_BACKOFF = (1, 2, 4)  # seconds

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        last_exc: Exception | None = None

        for attempt in range(self._MAX_RETRIES):
            prepared = {}
            if params:
                prepared = {k: v for k, v in params.items() if v is not None}

            # Re-sign on each attempt (timestamp must be fresh)
            if signed:
                prepared = self._sign(prepared)

            try:
                if method == "GET":
                    response = await self.client.get(endpoint, params=prepared)
                elif method == "POST":
                    response = await self.client.post(endpoint, params=prepared)
                elif method == "DELETE":
                    response = await self.client.delete(endpoint, params=prepared)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,  # EndOfStream, etc.
                httpx.ReadError,
            ) as e:
                last_exc = e
                if attempt < self._MAX_RETRIES - 1:
                    wait = self._RETRY_BACKOFF[attempt]
                    logger.warning(
                        "⚡ 网络错误 %s (attempt %d/%d), %ds 后重试: %s",
                        endpoint, attempt + 1, self._MAX_RETRIES, wait, e,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise BinanceConnectionError(str(e)) from e

            # Parse JSON body first — Binance returns error details in the body
            # even on 4xx responses
            try:
                data = response.json()
            except Exception:
                response.raise_for_status()
                return {}

            if isinstance(data, dict) and "code" in data:
                code = int(data["code"])
                if code < 0:
                    raise BinanceAPIError(code, data.get("msg", "Unknown error"))

            response.raise_for_status()
            return data

        # Should not reach here, but just in case
        raise BinanceConnectionError(f"Max retries exceeded: {last_exc}")

    # =========================================================================
    # Market Data Endpoints (public, no auth)
    # =========================================================================

    async def get_exchange_info(self) -> ExchangeInfoResponse:
        """Get exchange trading rules and symbol information."""
        data = await self._request("GET", "/fapi/v1/exchangeInfo")
        return ExchangeInfoResponse.model_validate(data)

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int | None = None,
    ) -> list[Kline]:
        """Get kline/candlestick data."""
        params: dict[str, Any] = {"symbol": symbol, "interval": interval}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        if limit:
            params["limit"] = limit
        data = await self._request("GET", "/fapi/v1/klines", params)
        return [Kline.from_array(k) for k in data]

    async def get_premium_index(self, symbol: str) -> dict:
        """Get mark price and premium index (basis rate) for a symbol.

        Returns dict with keys: symbol, markPrice, indexPrice,
        estimatedSettlePrice, lastFundingRate, interestRate,
        nextFundingTime, time.
        """
        return await self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})

    async def get_ticker_price(self, symbol: str | None = None) -> TickerPrice | list[TickerPrice]:
        """Get latest price for symbol(s)."""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", "/fapi/v2/ticker/price", params)
        if isinstance(data, list):
            return [TickerPrice.model_validate(t) for t in data]
        return TickerPrice.model_validate(data)

    # =========================================================================
    # Trade Endpoints (authenticated)
    # =========================================================================

    async def place_order(self, **kwargs: Any) -> OrderResponse:
        """Place a new order. POST /fapi/v1/order

        Required kwargs: symbol, side, type.
        Depending on type: quantity, price, stopPrice, closePosition, etc.
        """
        data = await self._request("POST", "/fapi/v1/order", dict(kwargs), signed=True)
        return OrderResponse.model_validate(data)

    async def query_order(
        self,
        symbol: str,
        order_id: int | None = None,
        orig_client_order_id: str | None = None,
    ) -> OrderResponse:
        """Query an order's status. GET /fapi/v1/order"""
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        data = await self._request("GET", "/fapi/v1/order", params, signed=True)
        return OrderResponse.model_validate(data)

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        """Get all open orders. GET /fapi/v1/openOrders"""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", "/fapi/v1/openOrders", params, signed=True)
        return [OrderResponse.model_validate(o) for o in data]

    async def cancel_order(
        self,
        symbol: str,
        order_id: int | None = None,
        orig_client_order_id: str | None = None,
    ) -> OrderResponse:
        """Cancel an active order. DELETE /fapi/v1/order"""
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        data = await self._request("DELETE", "/fapi/v1/order", params, signed=True)
        return OrderResponse.model_validate(data)

    async def place_market_close(
        self,
        symbol: str,
        side: str,
        quantity: str,
        position_side: str = "BOTH",
    ) -> OrderResponse:
        """Place a MARKET order to close (reduce) an existing position.

        Args:
            symbol: e.g. "BTCUSDT"
            side: "BUY" to close SHORT, "SELL" to close LONG
            quantity: amount to close
            position_side: "BOTH" for one-way, "LONG"/"SHORT" for hedge
        """
        return await self.place_order(
            symbol=symbol,
            side=side,
            positionSide=position_side,
            type="MARKET",
            quantity=quantity,
            reduceOnly="true",
        )

    # =========================================================================
    # Account Endpoints (authenticated)
    # =========================================================================

    async def get_account_balance(self) -> dict[str, Decimal]:
        """Get futures account balance. GET /fapi/v2/balance

        Returns dict with keys: total_balance, available_balance, unrealized_pnl.
        """
        data = await self._request("GET", "/fapi/v2/balance", signed=True)
        for item in data:
            if item.get("asset") == "USDT":
                return {
                    "total_balance": Decimal(item.get("balance", "0")),
                    "available_balance": Decimal(item.get("availableBalance", "0")),
                    "unrealized_pnl": Decimal(item.get("crossUnPnl", "0")),
                }
        return {
            "total_balance": Decimal("0"),
            "available_balance": Decimal("0"),
            "unrealized_pnl": Decimal("0"),
        }

    # ── WebSocket listenKey management ────────────────────────────────

    async def create_listen_key(self) -> str:
        """Create a listenKey for user data stream. POST /fapi/v1/listenKey"""
        data = await self._request("POST", "/fapi/v1/listenKey", signed=False)
        return data["listenKey"]

    async def keepalive_listen_key(self) -> None:
        """Keepalive listenKey (call every 30 min). PUT /fapi/v1/listenKey"""
        # PUT not in _request, use client directly
        await self.client.put("/fapi/v1/listenKey", headers={"X-MBX-APIKEY": self.api_key})

    async def close_listen_key(self) -> None:
        """Close listenKey. DELETE /fapi/v1/listenKey"""
        await self.client.delete("/fapi/v1/listenKey", headers={"X-MBX-APIKEY": self.api_key})

    async def get_position_risk(self, symbol: str | None = None) -> list[PositionRisk]:
        """Get current position information. GET /fapi/v2/positionRisk"""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", "/fapi/v2/positionRisk", params, signed=True)
        return [PositionRisk.model_validate(p) for p in data]

    async def get_position_mode(self) -> bool:
        """Check if account uses Hedge Mode (dual position side).

        Returns True if Hedge Mode (dual), False if One-way Mode.
        GET /fapi/v1/positionSide/dual
        """
        data = await self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
        return data.get("dualSidePosition", False)

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage for a symbol. POST /fapi/v1/leverage"""
        data = await self._request(
            "POST", "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
            signed=True,
        )
        return data

    async def get_income_history(
        self,
        income_type: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Get income history (realized P&L, funding, commission, etc.).

        GET /fapi/v1/income
        incomeType: REALIZED_PNL, FUNDING_FEE, COMMISSION, etc.
        """
        params: dict[str, Any] = {"limit": limit}
        if income_type:
            params["incomeType"] = income_type
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        data = await self._request("GET", "/fapi/v1/income", params, signed=True)
        return data

    async def get_user_trades(
        self,
        symbol: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Get account trade list with fill prices. GET /fapi/v1/userTrades

        Returns list of dicts with keys: symbol, id, orderId, side,
        price, qty, realizedPnl, quoteQty, commission, time, buyer, maker.
        """
        params: dict[str, Any] = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        data = await self._request("GET", "/fapi/v1/userTrades", params, signed=True)
        return data

    async def get_daily_realized_pnl(self) -> Decimal:
        """Get today's total realized P&L (UTC day)."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(start_of_day.timestamp() * 1000)

        records = await self.get_income_history(
            income_type="REALIZED_PNL", start_time=start_ms,
        )
        total = sum(Decimal(r.get("income", "0")) for r in records)
        return total

    # =========================================================================
    # Algo Order Endpoints (conditional orders — STOP_MARKET, TAKE_PROFIT_MARKET)
    # =========================================================================

    async def place_algo_order(self, **kwargs: Any) -> AlgoOrderResponse:
        """Place a conditional (algo) order. POST /fapi/v1/algoOrder

        Required kwargs: symbol, side, type, algoType="CONDITIONAL".
        Depending on type: triggerPrice, quantity, closePosition, etc.
        """
        kwargs.setdefault("algoType", "CONDITIONAL")
        data = await self._request("POST", "/fapi/v1/algoOrder", dict(kwargs), signed=True)
        return AlgoOrderResponse.model_validate(data)

    async def get_open_algo_orders(self, symbol: str | None = None) -> list[AlgoOrderResponse]:
        """Get all open algo (conditional) orders. GET /fapi/v1/openAlgoOrders"""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", "/fapi/v1/openAlgoOrders", params, signed=True)
        # API returns {"orders": [...]}
        orders = data.get("orders", data) if isinstance(data, dict) else data
        return [AlgoOrderResponse.model_validate(o) for o in orders]

    async def cancel_algo_order(self, symbol: str, algo_id: int) -> dict:
        """Cancel an algo order. DELETE /fapi/v1/algoOrder"""
        data = await self._request(
            "DELETE", "/fapi/v1/algoOrder",
            {"symbol": symbol, "algoId": algo_id},
            signed=True,
        )
        return data

