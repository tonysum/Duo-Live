"""模拟盘：不真实下单，按标记价格 + 策略 evaluate_position 与价格触及 TP/SL 模拟平仓。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from .binance_client import BinanceFuturesClient
from .live_config import LiveTradingConfig
from .live_position_monitor import TrackedPosition
from .models import utc_now
from .store import LiveTrade, TradeStore
from .strategy import PositionAction

logger = logging.getLogger(__name__)


def _next_paper_order_id() -> int:
    """Synthetic negative order id (Binance uses positive)."""
    return -int(utc_now().timestamp() * 1000) % 2_000_000_000 - 1


class PaperTradingLoop:
    """轮询 ``paper_positions`` / 内存持仓，不写交易所订单。"""

    def __init__(
        self,
        *,
        client: BinanceFuturesClient,
        config: LiveTradingConfig,
        store: TradeStore,
        strategy_registry: dict[str, Any],
        default_strategy: Any,
        quota_manager: Any,
        on_sl_triggered: Callable[[str, str], None] | None,
        poll_interval: int = 60,
    ):
        self.client = client
        self.config = config
        self.store = store
        self._strategy_registry = strategy_registry
        self._default_strategy = default_strategy
        self.quota_manager = quota_manager
        self.on_sl_triggered = on_sl_triggered
        self.poll_interval = poll_interval
        self._running = False
        self._positions: dict[tuple[str, str], TrackedPosition] = {}

    def _resolve_strategy(self, pos: TrackedPosition) -> Any:
        sid = (pos.strategy_id or "").strip()
        if sid and sid in self._strategy_registry:
            return self._strategy_registry[sid]
        return self._default_strategy

    def load_from_store(self) -> None:
        self._positions.clear()
        for row in self.store.list_paper_positions():
            pos = self._row_to_tracked(row)
            sk = (pos.strategy_id or "r24").strip() or "r24"
            self._positions[(pos.symbol, sk)] = pos
        if self._positions:
            logger.info("Paper: 恢复 %d 笔模拟持仓", len(self._positions))

    def _row_to_tracked(self, row: dict) -> TrackedPosition:
        sid = str(row.get("strategy_id") or "r24")
        try:
            djson = row.get("deferred_tp_sl_json") or "{}"
            deferred = json.loads(djson) if isinstance(djson, str) else {}
        except json.JSONDecodeError:
            deferred = {}
        ep = row.get("entry_fill_time")
        eft = datetime.fromisoformat(ep.replace("Z", "+00:00")) if ep else datetime.now(timezone.utc)
        if eft.tzinfo is None:
            eft = eft.replace(tzinfo=timezone.utc)
        lp = row.get("lowest_price")
        lowest: Decimal | None = None
        if lp and str(lp).strip():
            try:
                lowest = Decimal(str(lp))
            except Exception:
                lowest = None
        return TrackedPosition(
            symbol=str(row["symbol"]),
            entry_order_id=int(row["entry_order_id"]),
            side=str(row["side"]),
            quantity=str(row["quantity"]),
            deferred_tp_sl=deferred,
            entry_filled=True,
            entry_price=Decimal(str(row["entry_price"])),
            entry_fill_time=eft,
            tp_sl_placed=True,
            current_tp_pct=float(row["current_tp_pct"]),
            current_sl_pct=float(row["sl_pct"]),
            strategy_id=sid,
            has_added_position=bool(row.get("has_added_position")),
            lowest_price=lowest,
            closed=False,
        )

    def _persist(self, pos: TrackedPosition) -> None:
        margin = str(pos.deferred_tp_sl.get("margin_usdt", "") or "")
        init_tp = float(pos.deferred_tp_sl.get("initial_tp_pct", pos.current_tp_pct))
        init_sl = float(pos.deferred_tp_sl.get("initial_sl_pct", pos.current_sl_pct))
        self.store.upsert_paper_position(
            symbol=pos.symbol,
            strategy_id=(pos.strategy_id or "r24").strip() or "r24",
            side=pos.side,
            entry_order_id=pos.entry_order_id,
            quantity=str(pos.quantity),
            entry_price=str(pos.entry_price or ""),
            margin_usdt=margin,
            leverage=self.config.leverage,
            tp_pct=init_tp,
            sl_pct=init_sl,
            current_tp_pct=float(pos.current_tp_pct),
            entry_fill_time=pos.entry_fill_time.isoformat() if pos.entry_fill_time else utc_now().isoformat(),
            has_added_position=pos.has_added_position,
            lowest_price=str(pos.lowest_price) if pos.lowest_price is not None else None,
            deferred_tp_sl_json=json.dumps(pos.deferred_tp_sl),
        )

    def register_paper_entry(
        self,
        *,
        symbol: str,
        strategy_id: str,
        side: str,
        entry_price: Decimal,
        quantity: Decimal,
        margin_usdt: Decimal,
        tp_pct: float,
        sl_pct: float,
    ) -> int:
        oid = _next_paper_order_id()
        now = utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        deferred = {
            "margin_usdt": str(margin_usdt),
            "paper": "1",
            "initial_tp_pct": str(tp_pct),
            "initial_sl_pct": str(sl_pct),
        }
        pos = TrackedPosition(
            symbol=symbol,
            entry_order_id=oid,
            side=side,
            quantity=str(quantity),
            deferred_tp_sl=deferred,
            entry_filled=True,
            entry_price=entry_price,
            entry_fill_time=now,
            tp_sl_placed=True,
            current_tp_pct=tp_pct,
            current_sl_pct=sl_pct,
            strategy_id=strategy_id,
        )
        sk = strategy_id.strip() or "r24"
        self._positions[(symbol, sk)] = pos
        self._persist(pos)
        logger.info(
            "📝 PAPER 开仓 %s %s @ %s qty=%s margin=%s TP=%.2f%% SL=%.2f%%",
            symbol, side, entry_price, quantity, margin_usdt, tp_pct, sl_pct,
        )
        return oid

    async def run_forever(self) -> None:
        self._running = True
        self.load_from_store()
        logger.info("PaperTradingLoop 启动 (poll=%ds)", self.poll_interval)
        while self._running:
            await asyncio.sleep(self.poll_interval)
            for key in list(self._positions.keys()):
                pos = self._positions.get(key)
                if not pos or pos.closed:
                    continue
                try:
                    await self._tick_one(pos)
                except Exception as e:
                    logger.warning("Paper tick %s: %s", pos.symbol, e, exc_info=True)

    def stop(self) -> None:
        self._running = False

    async def _tick_one(self, pos: TrackedPosition) -> None:
        now = datetime.now(timezone.utc)
        ticker = await self.client.get_ticker_price(pos.symbol)
        mark = Decimal(str(ticker.price))
        strat = self._resolve_strategy(pos)
        if not strat:
            return

        action: PositionAction = await strat.evaluate_position(
            client=self.client,
            pos=pos,
            config=self.config,
            now=now,
        )

        if action.action == "close":
            await self._close_paper(
                pos, mark, event=action.reason or "strategy_close", reason=action.reason or "",
            )
            return
        if action.action == "adjust_tp":
            pos.current_tp_pct = action.new_tp_pct
            if action.new_strength:
                pos.strength = action.new_strength
            self._persist(pos)
            return
        if action.action == "add_position":
            logger.info("Paper: 跳过模拟加仓 %s (未实现)", pos.symbol)
            return

        hit = self._price_tp_sl_hit(pos, mark)
        if hit:
            await self._close_paper(pos, mark, event=hit, reason=hit)

    def _price_tp_sl_hit(self, pos: TrackedPosition, mark: Decimal) -> str | None:
        """SHORT：跌穿止盈区 / 涨穿止损区（与 current_tp%% / sl%% 一致）。"""
        if pos.side != "SHORT" or not pos.entry_price:
            return None
        ep = float(pos.entry_price)
        m = float(mark)
        tp_r = float(pos.current_tp_pct) / 100.0
        sl_r = float(pos.current_sl_pct) / 100.0
        if ep <= 0:
            return None
        if (ep - m) / ep >= tp_r:
            return "tp"
        if (m - ep) / ep >= sl_r:
            return "sl"
        return None

    async def _close_paper(
        self,
        pos: TrackedPosition,
        exit_price: Decimal,
        *,
        event: str,
        reason: str,
    ) -> None:
        sid = (pos.strategy_id or "r24").strip() or "r24"
        key = (pos.symbol, sid)
        pos.closed = True
        self._positions.pop(key, None)

        entry = float(pos.entry_price or 0)
        ex = float(exit_price)
        qty = float(pos.quantity or 0)
        margin_s = pos.deferred_tp_sl.get("margin_usdt", "0")
        try:
            margin = float(margin_s)
        except ValueError:
            margin = 0.0

        if pos.side == "SHORT" and entry > 0 and qty > 0:
            pnl = (entry - ex) * qty
            pnl_pct = (pnl / margin * 100.0) if margin > 0 else 0.0
        else:
            pnl = 0.0
            pnl_pct = 0.0

        self.store.save_live_trade(
            LiveTrade(
                symbol=pos.symbol,
                side=pos.side,
                event=event,
                entry_price=str(pos.entry_price or ""),
                exit_price=str(exit_price),
                quantity=str(pos.quantity),
                margin_usdt=str(margin),
                leverage=self.config.leverage,
                pnl_usdt=f"{pnl:.6f}",
                pnl_pct=f"{pnl_pct:.4f}",
                order_id=str(pos.entry_order_id),
                is_paper=True,
            )
        )
        self.store.delete_paper_position(pos.symbol, sid)
        self.store.delete_position_attribution(pos.symbol, pos.side)

        q = self.quota_manager.get_quota(sid)
        if q:
            q.decrement_position()
            try:
                q.update_daily_pnl(Decimal(str(pnl)))
            except Exception:
                pass

        if "sl" in (event or "").lower() or event == "sl" or reason == "trailing_stop":
            if self.on_sl_triggered:
                self.on_sl_triggered(pos.symbol, sid)

        logger.info(
            "📝 PAPER 平仓 %s %s @ %s event=%s pnl≈%.4f USDT",
            pos.symbol, pos.side, exit_price, event, pnl,
        )
