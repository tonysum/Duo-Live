"""Live query helpers — display orders and positions with rich tables."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .binance_client import BinanceFuturesClient
from .binance_models import AlgoOrderResponse, OrderResponse, PositionRisk


def _format_time(ts_ms: int) -> str:
    """Format millisecond timestamp to readable string."""
    if ts_ms == 0:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _format_decimal(val: Decimal, strip: bool = True) -> str:
    """Format Decimal, stripping trailing zeros."""
    if val == 0:
        return "0"
    s = f"{val:f}"
    if strip and "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


# ─────────────────────────────────────────────────────────────────────
# Show open orders (regular + algo/conditional)
# ─────────────────────────────────────────────────────────────────────

async def _fetch_all_orders(symbol: str | None = None):
    """Fetch both regular and algo orders."""
    load_dotenv()
    async with BinanceFuturesClient() as client:
        regular = await client.get_open_orders(symbol)
        algo = await client.get_open_algo_orders(symbol)
    return regular, algo


def show_orders(symbol: str | None = None) -> None:
    """Print all open orders (regular + conditional) in rich tables."""
    regular, algo = asyncio.run(_fetch_all_orders(symbol))
    console = Console()

    total = len(regular) + len(algo)
    if total == 0:
        console.print("[dim]没有挂单[/dim]")
        return

    # ── Regular orders ────────────────────────────────────────────
    if regular:
        table = Table(title=f"📋 普通挂单 — {symbol or '全部'}", show_lines=True)
        table.add_column("OrderId", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="bold")
        table.add_column("类型", style="magenta")
        table.add_column("方向")
        table.add_column("数量")
        table.add_column("价格")
        table.add_column("触发价")
        table.add_column("状态", style="green")
        table.add_column("更新时间")

        for o in regular:
            side_color = "red" if o.side == "SELL" else "green"
            table.add_row(
                str(o.order_id),
                o.symbol,
                o.orig_type or o.type,
                f"[{side_color}]{o.side}[/{side_color}]",
                _format_decimal(o.orig_qty),
                _format_decimal(o.price) if o.price else "—",
                _format_decimal(o.stop_price) if o.stop_price else "—",
                o.status,
                _format_time(o.update_time),
            )
        console.print(table)

    # ── Algo / conditional orders ─────────────────────────────────
    if algo:
        table = Table(title=f"📋 条件委托 — {symbol or '全部'}", show_lines=True)
        table.add_column("AlgoId", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="bold")
        table.add_column("类型", style="magenta")
        table.add_column("方向")
        table.add_column("数量")
        table.add_column("触发价")
        table.add_column("状态", style="green")
        table.add_column("创建时间")

        for o in algo:
            side_color = "red" if o.side == "SELL" else "green"
            table.add_row(
                str(o.algo_id),
                o.symbol,
                o.order_type,
                f"[{side_color}]{o.side}[/{side_color}]",
                _format_decimal(o.quantity),
                _format_decimal(o.trigger_price) if o.trigger_price else "—",
                o.algo_status,
                _format_time(o.create_time),
            )
        console.print(table)

    console.print(f"\n[dim]共 {len(regular)} 笔普通挂单 + {len(algo)} 笔条件委托[/dim]")


# ─────────────────────────────────────────────────────────────────────
# Show positions
# ─────────────────────────────────────────────────────────────────────

async def _fetch_positions(symbol: str | None = None) -> list[PositionRisk]:
    load_dotenv()
    async with BinanceFuturesClient() as client:
        positions = await client.get_position_risk(symbol)
    # Filter: only show positions with non-zero amount
    return [p for p in positions if p.position_amt != 0]


def show_positions(symbol: str | None = None) -> None:
    """Print current positions in a rich table."""
    positions = asyncio.run(_fetch_positions(symbol))
    console = Console()

    if not positions:
        console.print("[dim]没有持仓[/dim]")
        return

    table = Table(
        title=f"📊 当前持仓 — {symbol or '全部'}",
        show_lines=True,
    )
    table.add_column("Symbol", style="bold")
    table.add_column("方向")
    table.add_column("数量")
    table.add_column("开仓价")
    table.add_column("标记价")
    table.add_column("未实现盈亏", justify="right")
    table.add_column("强平价")
    table.add_column("杠杆")
    table.add_column("保证金类型")

    for p in positions:
        # Color PnL
        pnl = p.unrealized_profit
        pnl_str = _format_decimal(pnl)
        if pnl > 0:
            pnl_display = f"[green]+{pnl_str}[/green]"
        elif pnl < 0:
            pnl_display = f"[red]{pnl_str}[/red]"
        else:
            pnl_display = pnl_str

        # Direction
        amt = p.position_amt
        direction = "LONG" if amt > 0 else "SHORT"
        dir_color = "green" if amt > 0 else "red"

        table.add_row(
            p.symbol,
            f"[{dir_color}]{p.position_side or direction}[/{dir_color}]",
            _format_decimal(abs(amt)),
            _format_decimal(p.entry_price),
            _format_decimal(p.mark_price),
            pnl_display,
            _format_decimal(p.liquidation_price) if p.liquidation_price else "—",
            str(p.leverage),
            p.margin_type,
        )

    console.print(table)
    console.print(f"[dim]共 {len(positions)} 个持仓[/dim]")


# ─────────────────────────────────────────────────────────────────────
# Show single order detail
# ─────────────────────────────────────────────────────────────────────

async def _fetch_order(symbol: str, order_id: int) -> OrderResponse:
    load_dotenv()
    async with BinanceFuturesClient() as client:
        return await client.query_order(symbol, order_id=order_id)


def show_order_detail(symbol: str, order_id: int) -> None:
    """Print detailed info for a single order."""
    order = asyncio.run(_fetch_order(symbol, order_id))
    console = Console()

    console.print(f"\n[bold]📋 订单详情 — {order.symbol}[/bold]")
    console.print(f"  Order ID:     {order.order_id}")
    console.print(f"  Client ID:    {order.client_order_id}")
    console.print(f"  类型:          {order.orig_type or order.type}")
    console.print(f"  方向:          {order.side}")
    console.print(f"  持仓方向:      {order.position_side}")
    console.print(f"  数量:          {_format_decimal(order.orig_qty)}")
    console.print(f"  已成交:        {_format_decimal(order.executed_qty)}")
    console.print(f"  价格:          {_format_decimal(order.price)}")
    console.print(f"  触发价:        {_format_decimal(order.stop_price)}")
    console.print(f"  均价:          {_format_decimal(order.avg_price)}")
    console.print(f"  状态:          {order.status}")
    console.print(f"  TIF:           {order.time_in_force}")
    console.print(f"  Reduce Only:   {order.reduce_only}")
    console.print(f"  Close Position:{order.close_position}")
    console.print(f"  更新时间:      {_format_time(order.update_time)}")
    console.print()
