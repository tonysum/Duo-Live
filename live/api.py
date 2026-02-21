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

    Handles split exits correctly: if a position of 581 qty is closed in two
    fills (185 + 396), they are merged into one trade with:
      - quantity-weighted average exit price
      - summed PnL
      - exit_time of the last fill

    Entry/exit detection:
      Uses positionSide + side combo (hedge mode) or realizedPnl fallback
      (one-way mode where positionSide == "BOTH").

      Hedge mode:  LONG side BUY  → open long entry
                   LONG side SELL → close long exit
                   SHORT side SELL → open short entry
                   SHORT side BUY  → close short exit
      One-way mode fallback: pnl ≈ 0 → entry, pnl != 0 → exit
    """
    fills = sorted(raw_fills, key=lambda f: int(f.get("time", 0)))

    # open_position[symbol] = accumulated position state
    open_position: dict[str, dict] = {}
    completed: list[dict] = []

    def _is_entry(f: dict) -> bool:
        """Return True if this fill opens a position."""
        pos_side = f.get("positionSide", "BOTH")
        side_raw = f.get("side", "")
        if pos_side == "LONG":
            return side_raw == "BUY"
        if pos_side == "SHORT":
            return side_raw == "SELL"
        # One-way mode (positionSide == "BOTH") — fall back to pnl heuristic
        return abs(float(f.get("realizedPnl", "0"))) < 0.0001

    def _position_side(f: dict) -> str:
        """Determine the logical side of the position this fill belongs to."""
        pos_side = f.get("positionSide", "BOTH")
        if pos_side == "LONG":
            return "LONG"
        if pos_side == "SHORT":
            return "SHORT"
        # One-way: opening side determined by the fill's side field
        side_raw = f.get("side", "")
        return "SHORT" if side_raw == "SELL" else "LONG"

    for f in fills:
        symbol   = f.get("symbol", "")
        pnl      = float(f.get("realizedPnl", "0"))
        price    = float(f.get("price", "0"))
        qty      = float(f.get("qty", "0"))
        ts_ms    = int(f.get("time", 0))
        side_raw = f.get("side", "")   # BUY or SELL from Binance

        if _is_entry(f):
            # ── Entry fill ──────────────────────────────────────
            # Finalize any incomplete open position for this symbol first
            if symbol in open_position:
                op = open_position.pop(symbol)
                if op["exits"]:
                    completed.append(_finalize_trade(op))

            open_position[symbol] = {
                "symbol":      symbol,
                "side":        _position_side(f),
                "entry_price": price,
                "entry_qty":   qty,
                "entry_time":  ts_ms,
                "exits":       [],
                "exit_qty":    0.0,
            }

        else:
            # ── Exit fill ────────────────────────────────────────
            if symbol not in open_position:
                # Entry fill is outside the 1000-fill fetch window.
                # Keep the record so PnL is visible; entry_time=0 means unknown.
                pos_side = f.get("positionSide", "BOTH")
                if pos_side == "LONG":
                    pos_label = "LONG"
                elif pos_side == "SHORT":
                    pos_label = "SHORT"
                else:
                    pos_label = "SHORT" if side_raw == "BUY" else "LONG"
                completed.append({
                    "symbol":      symbol,
                    "side":        pos_label,
                    "entry_price": 0.0,
                    "exit_price":  price,
                    "entry_time":  0,      # 0 = unknown, not the exit time
                    "exit_time":   ts_ms,
                    "quantity":    qty,
                    "pnl_usdt":    pnl,
                })
                continue

            op = open_position[symbol]
            op["exits"].append({"qty": qty, "price": price, "pnl": pnl, "time": ts_ms})
            op["exit_qty"] += qty

            # Fully closed when exit qty >= entry qty (allow tiny float drift)
            if op["exit_qty"] >= op["entry_qty"] - 0.001:
                completed.append(_finalize_trade(op))
                del open_position[symbol]

    # Finalize remaining positions (partially exited or still open)
    for op in open_position.values():
        if op["exits"]:
            completed.append(_finalize_trade(op))

    completed.sort(key=lambda t: t["exit_time"], reverse=True)
    return completed


def _finalize_trade(op: dict) -> dict:
    """Merge all exit fills of one position into a single trade record."""
    exits = op["exits"]
    total_qty = sum(e["qty"] for e in exits)
    total_pnl = sum(e["pnl"] for e in exits)
    avg_exit  = (
        sum(e["qty"] * e["price"] for e in exits) / total_qty
        if total_qty > 0 else 0.0
    )
    return {
        "symbol":      op["symbol"],
        "side":        op["side"],
        "entry_price": op["entry_price"],
        "exit_price":  round(avg_exit, 8),
        "entry_time":  op["entry_time"],
        "exit_time":   max(e["time"] for e in exits),
        "quantity":    op["entry_qty"],   # original position size
        "pnl_usdt":    round(total_pnl, 6),
    }



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

    # CORS - allow specific origins from env or localhost + all localhost variants
    cors_env = os.environ.get("CORS_ORIGINS", "*")
    allowed_origins = ["*"] if cors_env == "*" else cors_env.split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
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
            bal, daily_pnl, all_pos = await asyncio.gather(
                trader.client.get_account_balance(),
                trader.client.get_daily_realized_pnl(),
                trader.client.get_position_risk(),
            )
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
            # Concurrent fetch — cuts latency in half
            all_pos, acct = await asyncio.gather(
                trader.client.get_position_risk(),
                trader.client.get_account_info(),
            )
            maint_map: dict[str, float] = {}
            for ap in acct.get("positions", []):
                sym = ap.get("symbol", "")
                mm = float(ap.get("maintMargin", "0"))
                if mm > 0:
                    maint_map[sym] = mm

            items = []
            for p in all_pos:
                amt = float(p.position_amt)
                if amt == 0:
                    continue
                liq = float(p.liquidation_price)
                margin = float(p.isolated_margin)
                upnl = float(p.unrealized_profit)

                # Binance margin ratio = maintMargin / marginBalance * 100%
                # marginBalance = isolatedMargin (which includes wallet + upnl for isolated)
                maint_margin = maint_map.get(p.symbol, 0)
                margin_balance = margin  # isolatedMargin already includes unrealized PnL
                if margin_balance > 0 and maint_margin > 0:
                    margin_ratio = round(maint_margin / margin_balance * 100, 2)
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

    @app.get("/api/orders")
    async def get_orders():
        """List all open orders (regular + algo/conditional)."""
        try:
            # Concurrent fetch — cuts latency in half
            regular, algo = await asyncio.gather(
                trader.client.get_open_orders(),
                trader.client.get_open_algo_orders(),
            )

            items = []
            for o in regular:
                items.append({
                    "id": o.order_id,
                    "symbol": o.symbol,
                    "type": o.orig_type or o.type,
                    "side": o.side,
                    "position_side": o.position_side,
                    "price": float(o.price) if o.price else 0,
                    "stop_price": float(o.stop_price) if o.stop_price else 0,
                    "quantity": float(o.orig_qty),
                    "filled_qty": float(o.executed_qty),
                    "status": o.status,
                    "time": o.update_time,
                    "is_algo": False,
                })
            for a in algo:
                items.append({
                    "id": a.algo_id,
                    "symbol": a.symbol,
                    "type": a.order_type,
                    "side": a.side,
                    "position_side": a.position_side,
                    "price": 0,
                    "stop_price": float(a.trigger_price),
                    "quantity": float(a.quantity),
                    "filled_qty": 0,
                    "status": a.algo_status,
                    "time": a.create_time,
                    "is_algo": True,
                })
            # Sort by time descending
            items.sort(key=lambda x: x["time"], reverse=True)
            return items
        except Exception as e:
            raise HTTPException(500, detail=str(e))

    @app.get("/api/trades", response_model=list[LiveTradeItem])
    async def get_trades(
        limit: int = Query(100, ge=1, le=1000),
        days: int = Query(30, ge=1, le=90),
    ):
        """Get completed trades (entry+exit paired) from Binance.

        Fetches all fills from the last `days` days (default 30) using
        startTime pagination, so entry fills are always within the window.
        """
        try:
            # Binance /fapi/v1/userTrades limits each request to max 7 days.
            # Paginate in 7-day windows going backwards to cover `days` total.
            now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
            start_ms = now_ms - days * 86_400_000
            CHUNK_MS = 7 * 86_400_000  # 7 days in ms

            all_fills: list[dict] = []
            chunk_end = now_ms
            while chunk_end > start_ms:
                chunk_start = max(chunk_end - CHUNK_MS, start_ms)
                batch = await trader.client.get_user_trades(
                    start_time=chunk_start,
                    end_time=chunk_end,
                    limit=1000,
                )
                all_fills.extend(batch)
                chunk_end = chunk_start - 1
                if chunk_end <= start_ms:
                    break

            paired = _pair_trades(all_fills)
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

    # ── Logs API ─────────────────────────────────────────────────

    def _get_log_path() -> str:
        """Resolve the log file path (same logic as __main__.py)."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "logs", "duo-live.log")

    def _tail_log(path: str, lines: int = 200, level: str = "", search: str = "") -> list[str]:
        """Read last N lines from log file, optionally filtered."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except FileNotFoundError:
            return []

        result = []
        for line in reversed(all_lines):
            s = line.rstrip()
            if not s:
                continue
            if level and f"[{level.upper()}]" not in s:
                continue
            if search and search.lower() not in s.lower():
                continue
            result.append(s)
            if len(result) >= lines:
                break

        return list(reversed(result))

    @app.get("/api/logs")
    async def get_logs(
        lines: int = Query(200, ge=1, le=5000),
        level: str = Query("", description="Filter by log level: DEBUG/INFO/WARNING/ERROR"),
        search: str = Query("", description="Keyword filter (case-insensitive)"),
    ):
        """Return last N lines of the log file with optional filtering."""
        path = _get_log_path()
        log_lines = _tail_log(path, lines=lines, level=level, search=search)
        return {"lines": log_lines, "total": len(log_lines), "path": path}

    @app.websocket("/ws/logs")
    async def websocket_logs(ws: WebSocket, token: str | None = None):
        """WebSocket that streams new log lines in real time."""
        expected_token = os.environ.get("WS_TOKEN", "")
        if expected_token and token != expected_token:
            await ws.accept()
            await ws.send_json({"error": "Unauthorized"})
            await ws.close(code=4004)
            return

        await ws.accept()
        path = _get_log_path()

        # Track file position
        try:
            file_size = os.path.getsize(path)
        except OSError:
            file_size = 0

        # Send initial tail (last 100 lines)
        initial = _tail_log(path, lines=100)
        await ws.send_json({"type": "init", "lines": initial})

        try:
            while True:
                await asyncio.sleep(1)
                try:
                    new_size = os.path.getsize(path)
                except OSError:
                    continue

                if new_size > file_size:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(file_size)
                        new_raw = f.read(new_size - file_size)
                    file_size = new_size
                    new_lines = [l.rstrip() for l in new_raw.splitlines() if l.strip()]
                    if new_lines:
                        await ws.send_json({"type": "append", "lines": new_lines})
                elif new_size < file_size:
                    # Log rotated — reset position and resend tail
                    file_size = new_size
                    rotated = _tail_log(path, lines=50)
                    await ws.send_json({"type": "init", "lines": rotated})

        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/live")
    async def websocket_live(ws: WebSocket, token: str | None = None):
        """WebSocket endpoint for real-time updates (requires auth)."""
        # Simple token auth via query parameter
        expected_token = os.environ.get("WS_TOKEN", "")
        if expected_token and token != expected_token:
            await ws.accept()
            await ws.send_json({"error": "Unauthorized"})
            await ws.close(code=4004)
            logger.warning("WebSocket auth failed - invalid token")
            return

        await ws.accept()
        ws_clients.append(ws)
        logger.info("WebSocket client connected (%d total)", len(ws_clients))

        try:
            last_hash: str = ""
            while True:
                # Build and send status update only when state has changed
                try:
                    bal = await trader.client.get_account_balance()
                    all_pos = await trader.client.get_position_risk()
                    open_pos = [p for p in all_pos if float(p.position_amt) != 0]

                    pos_data = [
                        {
                            "symbol": p.symbol,
                            "side": "LONG" if float(p.position_amt) > 0 else "SHORT",
                            "unrealized_pnl": float(p.unrealized_profit),
                            "entry_price": float(p.entry_price),
                            "quantity": abs(float(p.position_amt)),
                        }
                        for p in open_pos
                    ]

                    # Compute a lightweight hash: balance rounded to 2dp + position symbols+PnL
                    import hashlib, json as _json
                    state_key = _json.dumps(
                        {
                            "bal": round(bal["total_balance"], 2),
                            "upnl": round(bal["unrealized_pnl"], 4),
                            "pos": [{k: v for k, v in p.items()} for p in pos_data],
                        },
                        sort_keys=True,
                    )
                    new_hash = hashlib.md5(state_key.encode()).hexdigest()

                    if new_hash != last_hash:
                        data = {
                            "type": "status",
                            "total_balance": bal["total_balance"],
                            "unrealized_pnl": bal["unrealized_pnl"],
                            "positions": pos_data,
                        }
                        await ws.send_json(data)
                        last_hash = new_hash

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
