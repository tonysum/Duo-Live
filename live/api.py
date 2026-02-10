"""FastAPI backend for duo-live trading dashboard.

Runs in-process with PaperTrader, sharing state and Binance client.
All endpoints are prefixed with /api/.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Pydantic response models ────────────────────────────────────────

class StatusResponse(BaseModel):
    mode: str  # "live" or "paper"
    total_balance: float
    available_balance: float
    unrealized_pnl: float
    daily_pnl: float
    open_positions: int
    timestamp: str


class PositionItem(BaseModel):
    symbol: str
    side: str
    entry_price: float
    quantity: float
    unrealized_pnl: float
    leverage: int = 0
    tp_pct: float = 0
    strength: str = ""


class TradeItem(BaseModel):
    symbol: str
    side: str
    entry_price: str
    exit_price: str
    pnl: str
    pnl_pct: str
    exit_reason: str
    entry_time: str
    exit_time: str
    hold_hours: float
    tp_pct_used: float
    strength: str


class LiveTradeItem(BaseModel):
    symbol: str
    side: str
    event: str
    entry_price: str
    exit_price: str
    quantity: str
    pnl_usdt: str
    pnl_pct: str
    timestamp: str


class SignalItem(BaseModel):
    timestamp: str
    symbol: str
    surge_ratio: float
    price: str
    accepted: bool
    reject_reason: str


class EquityPoint(BaseModel):
    timestamp: str
    equity: float
    cash: float
    open_positions: int


class KlineItem(BaseModel):
    time: int  # Unix timestamp in seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


class OrderRequest(BaseModel):
    symbol: str
    side: str  # "BUY" or "SELL"
    order_type: str  # "MARKET" or "LIMIT"
    margin_usdt: float
    price: Optional[float] = None  # Required for LIMIT
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None
    leverage: int = 3


class ConfigResponse(BaseModel):
    live_mode: bool
    leverage: int
    position_size_pct: float
    max_positions: int
    max_entries_per_day: int
    stop_loss_pct: float
    strong_tp_pct: float
    medium_tp_pct: float
    weak_tp_pct: float
    max_hold_hours: int
    surge_threshold: float
    live_fixed_margin_usdt: float
    daily_loss_limit_usdt: float


# ── App factory ──────────────────────────────────────────────────────

def create_app(trader) -> FastAPI:
    """Create FastAPI app with trader reference.

    Args:
        trader: PaperTrader instance (shared, same process).
    """
    app = FastAPI(
        title="Duo-Live Trading API",
        description="Real-time trading dashboard API",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store trader reference
    app.state.trader = trader

    # WebSocket connections for broadcasting
    ws_clients: list[WebSocket] = []

    # ── Helper ───────────────────────────────────────────────────

    def _is_live() -> bool:
        return bool(trader.live_monitor)

    # ── REST Endpoints ───────────────────────────────────────────

    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
        """Account overview: balance, P&L, position count."""
        now = datetime.now(timezone.utc)

        if _is_live():
            try:
                bal = await trader.client.get_account_balance()
                daily_pnl = await trader.client.get_daily_realized_pnl()
                all_pos = await trader.client.get_position_risk()
                open_count = sum(1 for p in all_pos if float(p.position_amt) != 0)

                return StatusResponse(
                    mode="live",
                    total_balance=bal["total_balance"],
                    available_balance=bal["available_balance"],
                    unrealized_pnl=bal["unrealized_pnl"],
                    daily_pnl=float(daily_pnl),
                    open_positions=open_count,
                    timestamp=now.isoformat(),
                )
            except Exception as e:
                raise HTTPException(500, detail=str(e))
        else:
            # Paper mode
            store = trader.store
            positions = store.get_open_positions() if store else []
            trades = store.get_trades(limit=9999) if store else []

            today = now.strftime("%Y-%m-%d")
            today_trades = [t for t in trades if t.exit_time and t.exit_time.startswith(today)]
            today_pnl = sum(float(t.pnl) for t in today_trades)

            capital = float(trader.executor.capital) if trader.executor else 0

            return StatusResponse(
                mode="paper",
                total_balance=capital,
                available_balance=capital,
                unrealized_pnl=0,
                daily_pnl=today_pnl,
                open_positions=len(positions),
                timestamp=now.isoformat(),
            )

    @app.get("/api/positions", response_model=list[PositionItem])
    async def get_positions():
        """List open positions."""
        if _is_live():
            try:
                all_pos = await trader.client.get_position_risk()
                return [
                    PositionItem(
                        symbol=p.symbol,
                        side="LONG" if float(p.position_amt) > 0 else "SHORT",
                        entry_price=float(p.entry_price),
                        quantity=abs(float(p.position_amt)),
                        unrealized_pnl=float(p.unrealized_profit),
                        leverage=int(p.leverage),
                    )
                    for p in all_pos
                    if float(p.position_amt) != 0
                ]
            except Exception as e:
                raise HTTPException(500, detail=str(e))
        else:
            store = trader.store
            positions = store.get_open_positions() if store else []
            return [
                PositionItem(
                    symbol=p.symbol,
                    side=p.side.upper(),
                    entry_price=float(p.entry_price),
                    quantity=float(p.size),
                    unrealized_pnl=0,
                    leverage=p.leverage,
                    tp_pct=p.tp_pct,
                    strength=p.strength,
                )
                for p in positions
            ]

    @app.get("/api/trades")
    async def get_trades(
        limit: int = Query(50, ge=1, le=500),
        mode: str = Query("auto", regex="^(auto|paper|live)$"),
    ):
        """Get trade history."""
        store = trader.store
        if not store:
            return []

        use_live = (mode == "live") or (mode == "auto" and _is_live())

        if use_live:
            trades = store.get_live_trades(limit=limit)
            return [
                LiveTradeItem(
                    symbol=t.symbol,
                    side=t.side,
                    event=t.event,
                    entry_price=t.entry_price,
                    exit_price=t.exit_price,
                    quantity=t.quantity,
                    pnl_usdt=t.pnl_usdt,
                    pnl_pct=t.pnl_pct,
                    timestamp=t.timestamp,
                )
                for t in trades
            ]
        else:
            trades = store.get_trades(limit=limit)
            return [
                TradeItem(
                    symbol=t.symbol,
                    side=t.side,
                    entry_price=t.entry_price,
                    exit_price=t.exit_price,
                    pnl=t.pnl,
                    pnl_pct=t.pnl_pct,
                    exit_reason=t.exit_reason,
                    entry_time=t.entry_time,
                    exit_time=t.exit_time,
                    hold_hours=t.hold_hours,
                    tp_pct_used=t.tp_pct_used,
                    strength=t.coin_strength,
                )
                for t in trades
            ]

    @app.get("/api/equity")
    async def get_equity(limit: int = Query(500, ge=1, le=5000)):
        """Get equity curve data."""
        store = trader.store
        if not store:
            return []

        rows = store.get_equity_curve(limit=limit)
        return [
            {"timestamp": r[0], "equity": float(r[1])}
            for r in rows
        ]

    @app.get("/api/signals", response_model=list[SignalItem])
    async def get_signals(limit: int = Query(100, ge=1, le=1000)):
        """Get signal events."""
        store = trader.store
        if not store:
            return []

        events = store.get_signal_events(limit=limit)
        return [
            SignalItem(
                timestamp=e.timestamp,
                symbol=e.symbol,
                surge_ratio=e.surge_ratio,
                price=e.price,
                accepted=e.accepted,
                reject_reason=e.reject_reason,
            )
            for e in events
        ]

    @app.get("/api/config", response_model=ConfigResponse)
    async def get_config():
        """Get current trading config."""
        c = trader.config
        return ConfigResponse(
            live_mode=c.live_mode,
            leverage=c.leverage,
            position_size_pct=float(c.position_size_pct),
            max_positions=c.max_positions,
            max_entries_per_day=c.max_entries_per_day,
            stop_loss_pct=c.stop_loss_pct,
            strong_tp_pct=c.strong_tp_pct,
            medium_tp_pct=c.medium_tp_pct,
            weak_tp_pct=c.weak_tp_pct,
            max_hold_hours=c.max_hold_hours,
            surge_threshold=c.surge_threshold,
            live_fixed_margin_usdt=float(c.live_fixed_margin_usdt),
            daily_loss_limit_usdt=float(c.daily_loss_limit_usdt),
        )

    @app.get("/api/klines/{symbol}", response_model=list[KlineItem])
    async def get_klines(
        symbol: str,
        interval: str = Query("5m", regex="^(1m|3m|5m|15m|30m|1h|2h|4h|1d)$"),
        limit: int = Query(300, ge=1, le=1500),
    ):
        """Get candlestick data from Binance."""
        try:
            klines = await trader.client.get_klines(
                symbol=symbol.upper(),
                interval=interval,
                limit=limit,
            )
            return [
                KlineItem(
                    time=int(k.open_time / 1000),
                    open=float(k.open),
                    high=float(k.high),
                    low=float(k.low),
                    close=float(k.close),
                    volume=float(k.volume),
                )
                for k in klines
            ]
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.get("/api/ticker/{symbol}")
    async def get_ticker(symbol: str):
        """Get real-time price for a symbol."""
        try:
            ticker = await trader.client.get_ticker_price(symbol.upper())
            return {"symbol": ticker.symbol, "price": float(ticker.price)}
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.get("/api/exchange-info/{symbol}")
    async def get_exchange_info(symbol: str):
        """Get trading rules for a symbol (precision, min qty, etc.)."""
        try:
            info = await trader.client.get_exchange_info()
            sym_info = None
            for s in info.symbols:
                if s.symbol == symbol.upper():
                    sym_info = s
                    break

            if not sym_info:
                raise HTTPException(404, detail=f"Symbol {symbol} not found")

            # Extract filters
            filters = {}
            for f in sym_info.filters:
                filters[f["filterType"]] = f

            return {
                "symbol": sym_info.symbol,
                "status": sym_info.status,
                "pricePrecision": sym_info.price_precision,
                "quantityPrecision": sym_info.quantity_precision,
                "filters": filters,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.post("/api/order")
    async def place_order(req: OrderRequest):
        """Place a manual order with optional TP/SL."""
        if not _is_live():
            raise HTTPException(400, detail="Manual orders only available in live mode")

        try:
            # Set leverage
            await trader.client.set_leverage(req.symbol.upper(), req.leverage)

            # Get current price for quantity calculation
            ticker = await trader.client.get_ticker_price(req.symbol.upper())
            current_price = float(ticker.price)
            price = req.price if req.order_type == "LIMIT" else current_price

            # Calculate quantity: margin * leverage / price
            quantity_raw = (req.margin_usdt * req.leverage) / price

            # Get precision
            info = await trader.client.get_exchange_info()
            qty_precision = 3
            for s in info.symbols:
                if s.symbol == req.symbol.upper():
                    qty_precision = s.quantity_precision
                    break

            quantity = round(quantity_raw, qty_precision)

            # Determine position side
            is_hedge = await trader.client.get_position_mode()
            if is_hedge:
                position_side = "SHORT" if req.side == "SELL" else "LONG"
            else:
                position_side = "BOTH"

            # Place entry order
            order_params = {
                "symbol": req.symbol.upper(),
                "side": req.side,
                "type": req.order_type,
                "quantity": str(quantity),
                "positionSide": position_side,
            }
            if req.order_type == "LIMIT":
                order_params["price"] = str(req.price)
                order_params["timeInForce"] = "GTC"

            result = await trader.client.place_order(**order_params)

            response = {
                "status": "ok",
                "order_id": result.order_id,
                "symbol": result.symbol,
                "side": req.side,
                "quantity": str(quantity),
                "price": str(price),
            }

            # Place TP/SL if requested and using live monitor
            if (req.tp_pct or req.sl_pct) and trader.live_monitor:
                tp_sl = {}
                if req.tp_pct:
                    tp_sl["tp_pct"] = str(req.tp_pct)
                if req.sl_pct:
                    tp_sl["sl_pct"] = str(req.sl_pct)
                tp_sl["entry_price"] = str(price)
                tp_sl["position_side"] = position_side

                # Track via live monitor for TP/SL management
                trader.live_monitor.track(
                    symbol=req.symbol.upper(),
                    entry_order_id=result.order_id,
                    side="SHORT" if req.side == "SELL" else "LONG",
                    quantity=str(quantity),
                    deferred_tp_sl=tp_sl,
                )
                response["tp_sl"] = "deferred (will place after fill)"

            return response

        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.post("/api/close/{symbol}")
    async def close_position(symbol: str):
        """Force close a position."""
        if not _is_live():
            raise HTTPException(400, detail="Close only available in live mode")

        sym = symbol.upper()

        # Try live monitor first
        if trader.live_monitor:
            pos = trader.live_monitor._positions.get(sym)
            if pos:
                await trader.live_monitor._force_close(pos)
                return {"status": "ok", "message": f"Force closed {sym} via monitor"}

        # Direct exchange close
        try:
            all_pos = await trader.client.get_position_risk(sym)
            for p in all_pos:
                amt = float(p.position_amt)
                if amt == 0:
                    continue
                close_side = "SELL" if amt > 0 else "BUY"
                is_hedge = await trader.client.get_position_mode()
                ps = ("LONG" if amt > 0 else "SHORT") if is_hedge else "BOTH"
                await trader.client.place_market_close(
                    symbol=sym,
                    side=close_side,
                    quantity=str(abs(amt)),
                    position_side=ps,
                )
                return {"status": "ok", "message": f"Market closed {sym} ({close_side} {abs(amt)})"}
        except Exception as e:
            raise HTTPException(500, detail=str(e))

        raise HTTPException(404, detail=f"No open position found for {sym}")

    # ── WebSocket ────────────────────────────────────────────────

    @app.websocket("/ws/live")
    async def websocket_live(ws: WebSocket):
        """WebSocket endpoint for real-time updates."""
        await ws.accept()
        ws_clients.append(ws)
        logger.info("WebSocket client connected (%d total)", len(ws_clients))

        try:
            while True:
                # Send periodic status updates
                try:
                    if _is_live():
                        bal = await trader.client.get_account_balance()
                        all_pos = await trader.client.get_position_risk()
                        open_pos = [p for p in all_pos if float(p.position_amt) != 0]

                        data = {
                            "type": "status",
                            "total_balance": bal["total_balance"],
                            "unrealized_pnl": bal["unrealized_pnl"],
                            "positions": [
                                {
                                    "symbol": p.symbol,
                                    "side": "LONG" if float(p.position_amt) > 0 else "SHORT",
                                    "unrealized_pnl": float(p.unrealized_profit),
                                    "entry_price": float(p.entry_price),
                                    "quantity": abs(float(p.position_amt)),
                                }
                                for p in open_pos
                            ],
                        }
                    else:
                        positions = trader.store.get_open_positions() if trader.store else []
                        data = {
                            "type": "status",
                            "total_balance": float(trader.executor.capital) if trader.executor else 0,
                            "unrealized_pnl": 0,
                            "positions": [
                                {
                                    "symbol": p.symbol,
                                    "side": p.side.upper(),
                                    "unrealized_pnl": 0,
                                    "entry_price": float(p.entry_price),
                                    "quantity": float(p.size),
                                }
                                for p in positions
                            ],
                        }

                    await ws.send_json(data)
                except Exception as e:
                    logger.debug("WS status error: %s", e)

                await asyncio.sleep(5)

        except WebSocketDisconnect:
            pass
        finally:
            if ws in ws_clients:
                ws_clients.remove(ws)
            logger.info("WebSocket client disconnected (%d remaining)", len(ws_clients))

    return app
