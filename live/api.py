"""FastAPI backend for duo-live trading dashboard.

Runs in-process with LiveTrader, sharing state and Binance client.
All endpoints are prefixed with /api/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Pydantic response models ────────────────────────────────────────

class StatusResponse(BaseModel):
    mode: str  # "live"
    total_balance: float
    available_balance: float
    unrealized_pnl: float
    daily_pnl: float
    open_positions: int
    auto_trade_enabled: bool
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
    liquidation_price: float = 0
    margin: float = 0
    margin_ratio: float = 0


class LiveTradeItem(BaseModel):
    symbol: str
    side: str            # LONG / SHORT
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    quantity: float
    pnl_usdt: float
    leverage: int = 0


class SignalItem(BaseModel):
    timestamp: str
    symbol: str
    surge_ratio: float
    price: str
    accepted: bool
    reject_reason: str


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
    margin_usdt: float = 0
    quantity: Optional[float] = None  # Direct quantity (overrides margin calc)
    price: Optional[float] = None  # Required for LIMIT
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None
    leverage: int = 3
    trading_password: str = ""


class ConfigResponse(BaseModel):
    leverage: int
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
    margin_mode: str
    margin_pct: float


class AutoTradeRequest(BaseModel):
    enabled: bool


class UpdateConfigRequest(BaseModel):
    leverage: int | None = None
    max_positions: int | None = None
    max_entries_per_day: int | None = None
    live_fixed_margin_usdt: float | None = None
    daily_loss_limit_usdt: float | None = None
    margin_mode: str | None = None
    margin_pct: float | None = None


def _pair_trades(raw_fills: list[dict]) -> list[dict]:
    """Pair entry and exit fills into complete round-trip trades.

    Entry fill: realizedPnl == 0 (opening position)
    Exit fill:  realizedPnl != 0 (closing position, has PnL)
    Groups by symbol, pairs chronologically.
    """
    from collections import defaultdict

    fills = sorted(raw_fills, key=lambda f: int(f.get("time", 0)))
    open_entries: dict[str, list[dict]] = defaultdict(list)
    completed: list[dict] = []

    for f in fills:
        symbol = f.get("symbol", "")
        pnl = float(f.get("realizedPnl", "0"))
        price = float(f.get("price", "0"))
        qty = float(f.get("qty", "0"))
        ts_ms = int(f.get("time", 0))
        side_raw = f.get("side", "")  # BUY or SELL from Binance

        if abs(pnl) < 0.0001:
            # Entry fill
            open_entries[symbol].append({
                "price": price, "qty": qty,
                "time": ts_ms, "side_raw": side_raw,
            })
        else:
            # Exit fill — pair with earliest entry for this symbol
            entry = open_entries[symbol].pop(0) if open_entries[symbol] else None
            pos_side = "SHORT" if side_raw == "BUY" else "LONG"
            entry_price = entry["price"] if entry else 0.0
            entry_time = entry["time"] if entry else ts_ms

            completed.append({
                "symbol": symbol,
                "side": pos_side,
                "entry_price": entry_price,
                "exit_price": price,
                "entry_time": entry_time,
                "exit_time": ts_ms,
                "quantity": qty,
                "pnl_usdt": pnl,
            })

    completed.sort(key=lambda t: t["exit_time"], reverse=True)
    return completed


# ── App factory ──────────────────────────────────────────────────────

def create_app(trader) -> FastAPI:
    """Create FastAPI app with trader reference.

    Args:
        trader: LiveTrader instance (shared, same process).
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

    # ── REST Endpoints ───────────────────────────────────────────

    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
        """Account overview: balance, P&L, position count."""
        now = datetime.now(timezone.utc)
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
                auto_trade_enabled=trader.auto_trade_enabled,
                timestamp=now.isoformat(),
            )
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.get("/api/positions", response_model=list[PositionItem])
    async def get_positions():
        """List open positions from exchange."""
        try:
            all_pos = await trader.client.get_position_risk()
            items = []
            for p in all_pos:
                amt = float(p.position_amt)
                if amt == 0:
                    continue
                liq = float(p.liquidation_price)
                margin = float(p.isolated_margin)
                upnl = float(p.unrealized_profit)
                # margin_ratio: how much of margin is used
                # (margin + upnl) / margin — lower means closer to liquidation
                if margin > 0:
                    margin_ratio = round((margin + upnl) / margin * 100, 2)
                else:
                    margin_ratio = 0
                items.append(PositionItem(
                    symbol=p.symbol,
                    side="LONG" if amt > 0 else "SHORT",
                    entry_price=float(p.entry_price),
                    quantity=abs(amt),
                    unrealized_pnl=upnl,
                    leverage=int(p.leverage),
                    liquidation_price=liq,
                    margin=margin,
                    margin_ratio=margin_ratio,
                ))
            return items
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.get("/api/trades", response_model=list[LiveTradeItem])
    async def get_trades(limit: int = Query(100, ge=1, le=1000)):
        """Get completed trades (entry+exit paired) from Binance."""
        try:
            # Fetch enough raw fills to pair (entries + exits)
            raw = await trader.client.get_user_trades(limit=limit * 3)
            paired = _pair_trades(raw)
            result = []
            for t in paired[:limit]:
                entry_ts = datetime.fromtimestamp(
                    t["entry_time"] / 1000, tz=timezone.utc
                ).isoformat() if t["entry_time"] else ""
                exit_ts = datetime.fromtimestamp(
                    t["exit_time"] / 1000, tz=timezone.utc
                ).isoformat() if t["exit_time"] else ""
                result.append(LiveTradeItem(
                    symbol=t["symbol"],
                    side=t["side"],
                    entry_price=t["entry_price"],
                    exit_price=t["exit_price"],
                    entry_time=entry_ts,
                    exit_time=exit_ts,
                    quantity=t["quantity"],
                    pnl_usdt=t["pnl_usdt"],
                ))
            return result
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.get("/api/signals", response_model=list[SignalItem])
    async def get_signals(limit: int = Query(100, ge=1, le=5000)):
        """Get all signal events."""
        store = trader.store
        if not store:
            return []

        events = store.get_signal_events(limit=limit)
        result = [
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
        result.sort(key=lambda s: (s.timestamp, s.surge_ratio), reverse=True)
        return result

    @app.get("/api/config", response_model=ConfigResponse)
    async def get_config():
        """Get current trading config."""
        c = trader.config
        return ConfigResponse(
            leverage=c.leverage,
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
            margin_mode=c.margin_mode,
            margin_pct=c.margin_pct,
        )

    @app.post("/api/config")
    async def update_config(req: UpdateConfigRequest):
        """Update mutable config fields and persist to disk."""
        from decimal import Decimal as D
        c = trader.config
        if req.leverage is not None:
            c.leverage = req.leverage
        if req.max_positions is not None:
            c.max_positions = req.max_positions
        if req.max_entries_per_day is not None:
            c.max_entries_per_day = req.max_entries_per_day
        if req.live_fixed_margin_usdt is not None:
            c.live_fixed_margin_usdt = D(str(req.live_fixed_margin_usdt))
        if req.daily_loss_limit_usdt is not None:
            c.daily_loss_limit_usdt = D(str(req.daily_loss_limit_usdt))
        if req.margin_mode is not None and req.margin_mode in ("fixed", "percent"):
            c.margin_mode = req.margin_mode
        if req.margin_pct is not None:
            c.margin_pct = req.margin_pct
        c.save_to_file()
        logger.info("配置已更新 (via API)")
        return {
            "leverage": c.leverage,
            "max_positions": c.max_positions,
            "max_entries_per_day": c.max_entries_per_day,
            "live_fixed_margin_usdt": float(c.live_fixed_margin_usdt),
            "daily_loss_limit_usdt": float(c.daily_loss_limit_usdt),
            "margin_mode": c.margin_mode,
            "margin_pct": c.margin_pct,
            "message": "配置已保存",
        }

    # ── Auto-trade toggle ────────────────────────────────────────

    @app.get("/api/auto-trade")
    async def get_auto_trade():
        """Get auto-trade status."""
        return {"enabled": trader.auto_trade_enabled}

    @app.post("/api/auto-trade")
    async def set_auto_trade(req: AutoTradeRequest):
        """Toggle auto-trade on/off."""
        trader.auto_trade_enabled = req.enabled
        status = "开启" if req.enabled else "关闭"
        logger.info("自动交易已%s (via API)", status)
        return {"enabled": trader.auto_trade_enabled, "message": f"自动交易已{status}"}

    @app.get("/api/klines/{symbol}", response_model=list[KlineItem])
    async def get_klines(
        symbol: str,
        interval: str = Query("5m", pattern="^(1m|3m|5m|15m|30m|1h|2h|4h|1d|1w|1M)$"),
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

    @app.get("/api/tickers")
    async def get_tickers():
        """Get real-time prices and 24h change for all symbols (batch)."""
        try:
            data = await trader.client._request("GET", "/fapi/v1/ticker/24hr")
            return {
                item["symbol"]: {
                    "price": float(item["lastPrice"]),
                    "change_pct": float(item["priceChangePercent"]),
                }
                for item in data
                if "symbol" in item
            }
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
        # Password check
        expected_pw = os.environ.get("TRADING_PASSWORD", "")
        if expected_pw and req.trading_password != expected_pw:
            raise HTTPException(403, detail="交易密码错误")

        if not req.quantity and not req.margin_usdt:
            raise HTTPException(400, detail="请输入保证金或数量")

        try:
            # Set leverage
            await trader.client.set_leverage(req.symbol.upper(), req.leverage)

            # Get current price
            ticker = await trader.client.get_ticker_price(req.symbol.upper())
            current_price = float(ticker.price)
            price = req.price if req.order_type == "LIMIT" else current_price

            # Get precision
            info = await trader.client.get_exchange_info()
            qty_precision = 3
            for s in info.symbols:
                if s.symbol == req.symbol.upper():
                    qty_precision = s.quantity_precision
                    break

            # Calculate quantity: direct input or margin-based
            if req.quantity:
                quantity = round(req.quantity, qty_precision)
            else:
                quantity_raw = (req.margin_usdt * req.leverage) / price
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

            # Place TP/SL if requested
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
