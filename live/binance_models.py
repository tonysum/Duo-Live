"""Binance Futures API Pydantic models.

Models for both market data and authenticated trade/account responses:
  - ExchangeInfoResponse (symbol discovery)
  - Kline (candlestick data)
  - TickerPrice (real-time price)
  - OrderResponse (order placement / query)
  - PositionRisk (position information)
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


# =============================================================================
# Enumerations
# =============================================================================


class ContractType(str, Enum):
    PERPETUAL = "PERPETUAL"
    CURRENT_MONTH = "CURRENT_MONTH"
    NEXT_MONTH = "NEXT_MONTH"
    CURRENT_QUARTER = "CURRENT_QUARTER"
    NEXT_QUARTER = "NEXT_QUARTER"
    PERPETUAL_DELIVERING = "PERPETUAL_DELIVERING"
    TRADIFI_PERPETUAL = "TRADIFI_PERPETUAL"


class ContractStatus(str, Enum):
    PENDING_TRADING = "PENDING_TRADING"
    TRADING = "TRADING"
    PRE_DELIVERING = "PRE_DELIVERING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    PRE_SETTLE = "PRE_SETTLE"
    SETTLING = "SETTLING"
    CLOSE = "CLOSE"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    NEW_INSURANCE = "NEW_INSURANCE"
    NEW_ADL = "NEW_ADL"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"
    GTD = "GTD"


class FilterType(str, Enum):
    PRICE_FILTER = "PRICE_FILTER"
    LOT_SIZE = "LOT_SIZE"
    MARKET_LOT_SIZE = "MARKET_LOT_SIZE"
    MAX_NUM_ORDERS = "MAX_NUM_ORDERS"
    MAX_NUM_ALGO_ORDERS = "MAX_NUM_ALGO_ORDERS"
    MIN_NOTIONAL = "MIN_NOTIONAL"
    PERCENT_PRICE = "PERCENT_PRICE"
    POSITION_RISK_CONTROL = "POSITION_RISK_CONTROL"


class RateLimitType(str, Enum):
    REQUEST_WEIGHT = "REQUEST_WEIGHT"
    ORDERS = "ORDERS"


class RateLimitInterval(str, Enum):
    MINUTE = "MINUTE"
    SECOND = "SECOND"
    DAY = "DAY"


# =============================================================================
# Base Model
# =============================================================================


class BinanceBaseModel(BaseModel):
    """Base model — ignores unknown fields for forward compatibility."""

    model_config = {"populate_by_name": True, "str_strip_whitespace": True, "extra": "ignore"}


# =============================================================================
# Exchange Information
# =============================================================================


class RateLimit(BinanceBaseModel):
    rate_limit_type: RateLimitType = Field(alias="rateLimitType")
    interval: RateLimitInterval
    interval_num: int = Field(alias="intervalNum")
    limit: int


class SymbolFilter(BinanceBaseModel):
    filter_type: FilterType = Field(alias="filterType")
    min_price: Decimal | None = Field(default=None, alias="minPrice")
    max_price: Decimal | None = Field(default=None, alias="maxPrice")
    tick_size: Decimal | None = Field(default=None, alias="tickSize")
    min_qty: Decimal | None = Field(default=None, alias="minQty")
    max_qty: Decimal | None = Field(default=None, alias="maxQty")
    step_size: Decimal | None = Field(default=None, alias="stepSize")
    limit: int | None = None
    notional: Decimal | None = None
    multiplier_up: Decimal | None = Field(default=None, alias="multiplierUp")
    multiplier_down: Decimal | None = Field(default=None, alias="multiplierDown")
    multiplier_decimal: int | None = Field(default=None, alias="multiplierDecimal")


class Asset(BinanceBaseModel):
    asset: str
    margin_available: bool = Field(alias="marginAvailable")
    auto_asset_exchange: Decimal | None = Field(default=None, alias="autoAssetExchange")


class SymbolInfo(BinanceBaseModel):
    symbol: str
    pair: str
    contract_type: ContractType = Field(alias="contractType")
    delivery_date: int = Field(alias="deliveryDate")
    onboard_date: int = Field(alias="onboardDate")
    status: ContractStatus
    maint_margin_percent: Decimal = Field(alias="maintMarginPercent")
    required_margin_percent: Decimal = Field(alias="requiredMarginPercent")
    base_asset: str = Field(alias="baseAsset")
    quote_asset: str = Field(alias="quoteAsset")
    margin_asset: str = Field(alias="marginAsset")
    price_precision: int = Field(alias="pricePrecision")
    quantity_precision: int = Field(alias="quantityPrecision")
    base_asset_precision: int = Field(alias="baseAssetPrecision")
    quote_precision: int = Field(alias="quotePrecision")
    underlying_type: str = Field(alias="underlyingType")
    underlying_sub_type: list[str] = Field(default_factory=list, alias="underlyingSubType")
    settle_plan: int = Field(default=0, alias="settlePlan")
    trigger_protect: Decimal = Field(default=Decimal("0"), alias="triggerProtect")
    liquidation_fee: Decimal = Field(default=Decimal("0"), alias="liquidationFee")
    market_take_bound: Decimal = Field(default=Decimal("0"), alias="marketTakeBound")
    filters: list[SymbolFilter] = Field(default_factory=list)
    order_types: list[OrderType] = Field(default_factory=list, alias="OrderType")
    time_in_force: list[TimeInForce] = Field(default_factory=list, alias="timeInForce")


class ExchangeInfoResponse(BinanceBaseModel):
    timezone: str
    server_time: int = Field(alias="serverTime")
    futures_type: str | None = Field(default=None, alias="futuresType")
    rate_limits: list[RateLimit] = Field(alias="rateLimits")
    exchange_filters: list = Field(default_factory=list, alias="exchangeFilters")
    assets: list[Asset]
    symbols: list[SymbolInfo]


# =============================================================================
# Kline / Candlestick
# =============================================================================


class Kline(BinanceBaseModel):
    """Kline/Candlestick data — parsed from API array format."""

    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: int
    quote_asset_volume: Decimal
    number_of_trades: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal

    @classmethod
    def from_array(cls, data: list) -> Kline:
        return cls(
            open_time=data[0],
            open=Decimal(data[1]),
            high=Decimal(data[2]),
            low=Decimal(data[3]),
            close=Decimal(data[4]),
            volume=Decimal(data[5]),
            close_time=data[6],
            quote_asset_volume=Decimal(data[7]),
            number_of_trades=data[8],
            taker_buy_base_volume=Decimal(data[9]),
            taker_buy_quote_volume=Decimal(data[10]),
        )


# =============================================================================
# Ticker
# =============================================================================


class TickerPrice(BinanceBaseModel):
    """Symbol price ticker."""

    symbol: str
    price: Decimal
    time: int


# =============================================================================
# Order Response
# =============================================================================


class OrderResponse(BinanceBaseModel):
    """Response from order placement / query."""

    order_id: int = Field(alias="orderId")
    symbol: str
    status: str
    client_order_id: str = Field(default="", alias="clientOrderId")
    price: Decimal = Decimal("0")
    avg_price: Decimal = Field(default=Decimal("0"), alias="avgPrice")
    orig_qty: Decimal = Field(default=Decimal("0"), alias="origQty")
    executed_qty: Decimal = Field(default=Decimal("0"), alias="executedQty")
    cum_quote: Decimal = Field(default=Decimal("0"), alias="cumQuote")
    type: str = ""
    orig_type: str = Field(default="", alias="origType")
    side: str = ""
    position_side: str = Field(default="BOTH", alias="positionSide")
    stop_price: Decimal = Field(default=Decimal("0"), alias="stopPrice")
    time_in_force: str = Field(default="GTC", alias="timeInForce")
    reduce_only: bool = Field(default=False, alias="reduceOnly")
    close_position: bool = Field(default=False, alias="closePosition")
    working_type: str = Field(default="CONTRACT_PRICE", alias="workingType")
    price_protect: bool = Field(default=False, alias="priceProtect")
    update_time: int = Field(default=0, alias="updateTime")


# =============================================================================
# Position Information
# =============================================================================


class PositionRisk(BinanceBaseModel):
    """Position risk / information from GET /fapi/v2/positionRisk."""

    symbol: str
    position_side: str = Field(default="BOTH", alias="positionSide")
    position_amt: Decimal = Field(default=Decimal("0"), alias="positionAmt")
    entry_price: Decimal = Field(default=Decimal("0"), alias="entryPrice")
    break_even_price: Decimal = Field(default=Decimal("0"), alias="breakEvenPrice")
    mark_price: Decimal = Field(default=Decimal("0"), alias="markPrice")
    unrealized_profit: Decimal = Field(default=Decimal("0"), alias="unRealizedProfit")
    liquidation_price: Decimal = Field(default=Decimal("0"), alias="liquidationPrice")
    leverage: int = Field(default=1)
    margin_type: str = Field(default="cross", alias="marginType")
    isolated_margin: Decimal = Field(default=Decimal("0"), alias="isolatedMargin")
    notional: Decimal = Field(default=Decimal("0"))
    max_notional_value: Decimal = Field(default=Decimal("0"), alias="maxNotionalValue")
    update_time: int = Field(default=0, alias="updateTime")


# =============================================================================
# Algo Order Response (conditional orders: STOP_MARKET, TAKE_PROFIT_MARKET)
# =============================================================================


class AlgoOrderResponse(BinanceBaseModel):
    """Response from Algo Order API (POST /fapi/v1/algoOrder)."""

    algo_id: int = Field(alias="algoId")
    client_algo_id: str = Field(default="", alias="clientAlgoId")
    algo_type: str = Field(default="CONDITIONAL", alias="algoType")
    order_type: str = Field(default="", alias="orderType")
    symbol: str = ""
    side: str = ""
    position_side: str = Field(default="BOTH", alias="positionSide")
    algo_status: str = Field(default="NEW", alias="algoStatus")
    trigger_price: Decimal = Field(default=Decimal("0"), alias="triggerPrice")
    price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    close_position: bool = Field(default=False, alias="closePosition")
    price_protect: bool = Field(default=False, alias="priceProtect")
    reduce_only: bool = Field(default=False, alias="reduceOnly")
    working_type: str = Field(default="CONTRACT_PRICE", alias="workingType")
    time_in_force: str = Field(default="GTC", alias="timeInForce")
    create_time: int = Field(default=0, alias="createTime")
    update_time: int = Field(default=0, alias="updateTime")

