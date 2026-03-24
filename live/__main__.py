"""duo-live entry point.

Usage:
    python -m live run [--margin N] [--loss-limit N] [--auto-trade]  # 启动实盘交易
    python -m live status                              # 查看账户状态
    python -m live live-trades [N]                     # 查看实盘交易记录
    python -m live order <symbol> <price> [qty]        # 手动下单
        [--long] [--tp N] [--sl N] [--leverage N] [--margin N]
    python -m live orders [symbol]                     # 查看挂单
    python -m live positions [symbol]                  # 查看持仓
    python -m live close <symbol>                      # 市价平仓
    python -m live tp <symbol> <price>                 # 手动挂止盈
    python -m live sl <symbol> <price>                 # 手动挂止损
    python -m live cancel <symbol> <id>                # 取消单个订单
    python -m live cancel-all <symbol>                 # 取消全部订单
    python -m live test-notify [message]               # 测试 Telegram 通知
"""

import asyncio
import logging
import sys
from decimal import Decimal

from dotenv import load_dotenv

from .live_config import LiveTradingConfig
from .trader import LiveTrader


def _parse_flags(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Parse positional args and --key/--flag options from argv."""
    positional: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            key = a.lstrip("-")
            # Boolean flag (no value) or key=value
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[key] = args[i + 1]
                i += 2
            else:
                flags[key] = "true"
                i += 1
        else:
            positional.append(a)
            i += 1
    return positional, flags


def _run_order(symbol: str, price: str, quantity: str | None = None,
               margin: float | None = None,
               side: str = "SHORT", tp_pct: float = 33.0,
               sl_pct: float = 18.0, leverage: int = 4):
    """Execute a live order with TP/SL.

    Specify either quantity OR margin (USDT). If margin is given,
    quantity = margin × leverage ÷ price.
    """
    load_dotenv()
    from .binance_client import BinanceFuturesClient
    from .live_executor import LiveOrderExecutor

    price_d = Decimal(price)

    if quantity is not None:
        qty_d = Decimal(quantity)
    elif margin is not None:
        qty_d = Decimal(str(margin)) * leverage / price_d
    else:
        raise ValueError("Must specify either quantity or --margin")

    print(f"📊 下单参数: {symbol.upper()} {side}")
    print(f"   价格={price_d}, 数量={qty_d:.6f}")
    if margin:
        print(f"   保证金={margin} USDT, 杠杆={leverage}x")
    print(f"   止盈={tp_pct}%, 止损={sl_pct}%")
    print()

    async def _execute():
        async with BinanceFuturesClient() as client:
            executor = LiveOrderExecutor(client, leverage=leverage)
            result = await executor.open_position(
                symbol=symbol.upper(),
                price=price_d,
                quantity=qty_d,
                side=side,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
            )
            return result

    result = asyncio.run(_execute())

    # Print summary
    side_label = "做多" if side == "LONG" else "做空"
    if result.get("entry_order"):
        entry = result["entry_order"]
        print(f"\n✅ {side_label}入场单: orderId={entry.order_id}, status={entry.status}")
    if result.get("deferred_tp_sl"):
        d = result["deferred_tp_sl"]
        print(f"📌 止盈 {d['tp_price']} / 止损 {d['sl_price']} — 入场成交后自动挂出")
    if result.get("error"):
        print(f"\n❌ 错误: {result['error']}")
        return

    # Start monitor to wait for entry fill and auto-place TP/SL
    if result.get("entry_order") and result.get("deferred_tp_sl"):
        print("\n🔍 等待入场成交并挂出 TP/SL... (Ctrl+C 退出，TP/SL 需手动设置)")

        async def _monitor():
            from .live_position_monitor import LivePositionMonitor
            async with BinanceFuturesClient() as client:
                executor = LiveOrderExecutor(client, leverage=leverage)
                mon = LivePositionMonitor(client, executor, poll_interval=10)
                mon.track(
                    symbol=result["entry_order"].symbol,
                    entry_order_id=result["entry_order"].order_id,
                    side=side,
                    quantity=result["deferred_tp_sl"]["quantity"],
                    deferred_tp_sl=result["deferred_tp_sl"],
                )
                # Poll until TP/SL placed or position closed/canceled
                while mon.tracked_count > 0:
                    await mon._check_all()
                    pos = mon._positions.get(result["entry_order"].symbol)
                    if not pos:
                        break
                    if pos.closed:
                        print("\n⚠️ 入场单已取消/过期")
                        break
                    if pos.tp_sl_placed:
                        print(f"\n✅ TP/SL 已挂出 (tp={pos.tp_algo_id}, sl={pos.sl_algo_id})")
                        print("   可安全退出，TP/SL 由交易所管理")
                        break
                    await asyncio.sleep(10)

        try:
            asyncio.run(_monitor())
        except KeyboardInterrupt:
            print("\n⏹️ 监控已停止 (入场单仍在挂单中，TP/SL 需手动设置)")


def main():
    # ── Logging ──────────────────────────────────────────────────────
    import os
    from logging.handlers import RotatingFileHandler

    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "duo-live.log")

    log_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console: INFO
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_fmt)

    # File: DEBUG, 10MB × 5 backups
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_fmt)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.INFO)

    config = LiveTradingConfig.load_from_file()

    # ── Sub-commands ─────────────────────────────────────────────────
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"

    try:
        _dispatch(cmd, config)
    except Exception as e:
        # Friendly error for known exceptions
        err_name = type(e).__name__
        if err_name in ("BinanceConnectionError", "BinanceAPIError"):
            print(f"\n❌ {e}")
        else:
            print(f"\n❌ {err_name}: {e}")
        sys.exit(1)


def _dispatch(cmd: str, config: LiveTradingConfig):
    """Route CLI sub-commands."""
    if cmd == "status":
        load_dotenv()
        from .binance_client import BinanceFuturesClient
        from rich.console import Console
        from rich.panel import Panel

        console = Console()

        async def _status():
            async with BinanceFuturesClient() as client:
                bal = await client.get_account_balance()
                daily_pnl = await client.get_daily_realized_pnl()
                all_pos = await client.get_position_risk()
                open_count = sum(1 for p in all_pos if float(p.position_amt) != 0)

                total = bal["total_balance"]
                avail = bal["available_balance"]
                unreal = bal["unrealized_pnl"]

                pnl_color = "green" if daily_pnl >= 0 else "red"
                unreal_color = "green" if unreal >= 0 else "red"

                console.print()
                console.print(Panel.fit(
                    f"💰 总余额:     [bold]{total:,.2f}[/bold] USDT\n"
                    f"💵 可用余额:   [bold]{avail:,.2f}[/bold] USDT\n"
                    f"📈 今日盈亏:   [{pnl_color}]{daily_pnl:+,.2f}[/{pnl_color}] USDT\n"
                    f"📊 未实现盈亏: [{unreal_color}]{unreal:+,.2f}[/{unreal_color}] USDT\n"
                    f"📌 持仓数:     {open_count}",
                    title="🔴 实盘账户状态",
                ))
                console.print()

        asyncio.run(_status())

    elif cmd == "trades":
        load_dotenv()
        from .binance_client import BinanceFuturesClient
        from .api import _pair_trades
        from rich.console import Console
        from rich.table import Table
        from datetime import datetime, timezone

        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        console = Console()

        async def _trades():
            async with BinanceFuturesClient() as client:
                raw = await client.get_user_trades(limit=limit * 3)
                trades = _pair_trades(raw)[:limit]
                if not trades:
                    console.print("[dim]暂无实盘交易记录[/dim]")
                    return

                table = Table(title=f"📋 实盘交易记录 (最近 {len(trades)} 笔)", show_lines=True)
                table.add_column("平仓时间", style="dim")
                table.add_column("币种", style="bold")
                table.add_column("方向", justify="center")
                table.add_column("入场价", justify="right")
                table.add_column("出场价", justify="right")
                table.add_column("盈亏", justify="right")

                total_pnl = 0.0
                for t in trades:
                    pnl = t["pnl_usdt"]
                    total_pnl += pnl
                    pnl_color = "green" if pnl >= 0 else "red"
                    side_color = "red" if t["side"] == "SHORT" else "green"
                    exit_ts = datetime.fromtimestamp(
                        t["exit_time"] / 1000, tz=timezone.utc
                    ).strftime("%m-%d %H:%M") if t["exit_time"] else ""
                    table.add_row(
                        exit_ts,
                        t["symbol"],
                        f"[{side_color}]{t['side']}[/{side_color}]",
                        f"{t['entry_price']:.4f}",
                        f"{t['exit_price']:.4f}",
                        f"[{pnl_color}]{pnl:+,.2f}[/{pnl_color}]",
                    )
                console.print(table)
                total_color = "green" if total_pnl >= 0 else "red"
                console.print(f"\n  合计盈亏: [{total_color}]{total_pnl:+,.2f}[/{total_color}] USDT")

        asyncio.run(_trades())

    elif cmd == "signals":
        from rich.console import Console
        from rich.table import Table
        from .store import TradeStore
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        store = TradeStore(config.db_path)
        events = store.get_signal_events(limit=limit)
        store.close()
        console = Console()
        if events:
            table = Table(title=f"📡 Signal History (last {len(events)})")
            table.add_column("Time", style="dim")
            table.add_column("Symbol", style="cyan")
            table.add_column("Surge", justify="right", style="red")
            table.add_column("Price", justify="right")
            table.add_column("Result")
            table.add_column("Reason", style="dim")

            accepted = 0
            for e in events:
                if e.accepted:
                    accepted += 1
                    result = "[green]✅ ENTRY[/green]"
                    reason = ""
                else:
                    result = "[yellow]❌ FILTERED[/yellow]"
                    reason = e.reject_reason or ""
                table.add_row(
                    e.timestamp[:19],
                    e.symbol,
                    f"{e.surge_ratio:.1f}x",
                    e.price,
                    result,
                    reason,
                )
            console.print(table)
            console.print(
                f"\n  Total: {len(events)}  |  "
                f"Accepted: {accepted}  |  "
                f"Rejected: {len(events) - accepted}"
            )
        else:
            console.print("[dim]No signals detected yet.[/dim]")

    elif cmd == "order":
        # Two modes:
        #   python -m live order <symbol> <price> <quantity> [flags]   # 指定数量
        #   python -m live order <symbol> <price> --margin <USDT>      # 指定保证金
        pos_args, flags = _parse_flags(sys.argv[2:])
        leverage = int(flags.get("leverage", config.leverage))
        side = "LONG" if "long" in flags else "SHORT"
        tp_pct = float(flags.get("tp", 34.0))  # default from RollingLiveConfig.tp_initial
        sl_pct = float(flags.get("sl", 44.0))  # default from RollingLiveConfig.sl_threshold

        if "margin" in flags and len(pos_args) >= 2:
            # Mode 2: price + margin
            _run_order(
                symbol=pos_args[0], price=pos_args[1],
                margin=float(flags["margin"]),
                side=side, tp_pct=tp_pct, sl_pct=sl_pct, leverage=leverage,
            )
        elif len(pos_args) >= 3:
            # Mode 1: price + quantity
            _run_order(
                symbol=pos_args[0], price=pos_args[1], quantity=pos_args[2],
                side=side, tp_pct=tp_pct, sl_pct=sl_pct, leverage=leverage,
            )
        else:
            print("用法(二选一):")
            print("  python -m live order <symbol> <price> <quantity> [--long] [--tp N] [--sl N] [--leverage N]")
            print("  python -m live order <symbol> <price> --margin <USDT> [--long] [--tp N] [--sl N] [--leverage N]")
            print()
            print("示例:")
            print("  python -m live order ETHUSDT 2500 0.2                    # 价格+数量")
            print("  python -m live order ETHUSDT 2500 --margin 100           # 价格+保证金(100U)")
            print("  python -m live order ETHUSDT 2500 --margin 100 --long    # 做多")
            sys.exit(1)

    elif cmd == "orders":
        from .live_queries import show_orders
        sym = sys.argv[2] if len(sys.argv) > 2 else None
        show_orders(sym)

    elif cmd == "positions":
        from .live_queries import show_positions
        sym = sys.argv[2] if len(sys.argv) > 2 else None
        show_positions(sym)

    elif cmd == "close":
        # python -m live close <symbol>
        if len(sys.argv) < 3:
            print("用法: python -m live close <symbol>")
            print("示例: python -m live close ETHUSDT")
            sys.exit(1)
        load_dotenv()
        from .binance_client import BinanceFuturesClient

        sym = sys.argv[2].upper()

        async def _close():
            async with BinanceFuturesClient() as client:
                # Find the position
                positions = await client.get_position_risk(sym)
                pos = None
                for p in positions:
                    if float(p.position_amt) != 0:
                        pos = p
                        break
                if not pos:
                    print(f"⚠️ {sym} 没有持仓")
                    return

                amt = float(pos.position_amt)
                qty = str(abs(amt))
                # positionAmt > 0 = LONG, < 0 = SHORT
                close_side = "SELL" if amt > 0 else "BUY"
                direction = "LONG" if amt > 0 else "SHORT"

                print(f"📊 平仓: {sym} {direction}")
                print(f"   数量={qty}, 标记价={pos.mark_price}, 未实现盈亏={pos.unrealized_profit}")

                result = await client.place_market_close(
                    symbol=sym, side=close_side,
                    quantity=qty, position_side=pos.position_side,
                )
                print(f"\n✅ 市价平仓成功: orderId={result.order_id}, status={result.status}")

                # Cancel remaining TP/SL algo orders
                try:
                    algos = await client.get_open_algo_orders(sym)
                    for a in algos:
                        await client.cancel_algo_order(sym, algo_id=a.algo_id)
                        print(f"🗑️ 已撤销条件单: algoId={a.algo_id}")
                except Exception:
                    pass

        asyncio.run(_close())

    elif cmd in ("tp", "sl"):
        # python -m live tp <symbol> <price>
        # python -m live sl <symbol> <price>
        if len(sys.argv) < 4:
            label = "止盈" if cmd == "tp" else "止损"
            print(f"用法: python -m live {cmd} <symbol> <price>")
            print(f"示例: python -m live {cmd} ETHUSDT 2100")
            print(f"  自动检测持仓方向和数量, 挂{label}条件单")
            sys.exit(1)
        load_dotenv()
        from .binance_client import BinanceFuturesClient

        sym = sys.argv[2].upper()
        trigger_price = sys.argv[3]

        async def _tp_sl():
            async with BinanceFuturesClient() as client:
                # Auto-detect position
                positions = await client.get_position_risk(sym)
                pos = None
                for p in positions:
                    if float(p.position_amt) != 0:
                        pos = p
                        break
                if not pos:
                    print(f"⚠️ {sym} 没有持仓, 无法设置{cmd.upper()}")
                    return

                amt = float(pos.position_amt)
                qty = str(abs(amt))
                is_long = amt > 0
                close_side = "SELL" if is_long else "BUY"
                direction = "LONG" if is_long else "SHORT"

                if cmd == "tp":
                    algo_type = "TAKE_PROFIT_MARKET"
                    label = "止盈"
                else:
                    algo_type = "STOP_MARKET"
                    label = "止损"

                print(f"📋 挂{label}单: {sym} {direction}, 数量={qty}, 触发价={trigger_price}")

                result = await client.place_algo_order(
                    symbol=sym,
                    side=close_side,
                    positionSide=pos.position_side,
                    type=algo_type,
                    triggerPrice=trigger_price,
                    quantity=qty,
                    reduceOnly="true",
                    priceProtect="true",
                    workingType="CONTRACT_PRICE",
                )
                print(f"✅ {label}单已挂出: algoId={result.algo_id}, triggerPrice={trigger_price}")

        asyncio.run(_tp_sl())

    elif cmd == "cancel":
        # python -m live cancel <symbol> <orderId|algoId>
        if len(sys.argv) < 4:
            print("用法: python -m live cancel <symbol> <orderId或algoId>")
            print("示例: python -m live cancel ETHUSDT 8389766096695338750")
            sys.exit(1)
        load_dotenv()
        from .binance_client import BinanceFuturesClient, BinanceAPIError
        sym, oid = sys.argv[2].upper(), int(sys.argv[3])

        async def _cancel():
            async with BinanceFuturesClient() as client:
                # Try regular order first, then algo order
                try:
                    r = await client.cancel_order(sym, order_id=oid)
                    print(f"✅ 已撤销普通订单: orderId={r.order_id}, status={r.status}")
                    return
                except BinanceAPIError:
                    pass
                try:
                    await client.cancel_algo_order(sym, algo_id=oid)
                    print(f"✅ 已撤销条件委托: algoId={oid}")
                except BinanceAPIError as e:
                    print(f"❌ 撤销失败: {e}")
        asyncio.run(_cancel())

    elif cmd == "cancel-all":
        # python -m live cancel-all <symbol>
        if len(sys.argv) < 3:
            print("用法: python -m live cancel-all <symbol>")
            print("示例: python -m live cancel-all ETHUSDT")
            sys.exit(1)
        load_dotenv()
        from .binance_client import BinanceFuturesClient

        sym = sys.argv[2].upper()
        async def _cancel_all():
            async with BinanceFuturesClient() as client:
                # Cancel regular orders
                regular = await client.get_open_orders(sym)
                for o in regular:
                    try:
                        await client.cancel_order(sym, order_id=o.order_id)
                        print(f"🗑️ 撤销普通订单: {o.order_id} ({o.orig_type or o.type})")
                    except Exception as e:
                        print(f"❌ 撤销失败 {o.order_id}: {e}")
                # Cancel algo orders
                algo = await client.get_open_algo_orders(sym)
                for o in algo:
                    try:
                        await client.cancel_algo_order(sym, algo_id=o.algo_id)
                        print(f"🗑️ 撤销条件委托: {o.algo_id} ({o.order_type})")
                    except Exception as e:
                        print(f"❌ 撤销失败 {o.algo_id}: {e}")
                total = len(regular) + len(algo)
                if total == 0:
                    print("没有需要撤销的订单")
                else:
                    print(f"\n✅ 共撤销 {total} 笔订单")
        asyncio.run(_cancel_all())

    elif cmd == "test-notify":
        # python -m live test-notify [message]
        load_dotenv()
        from .notifier import TelegramNotifier
        msg = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "🤖 duo-live 通知测试成功!"
        notifier = TelegramNotifier()
        if not notifier.enabled:
            print("❌ 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")
            print("请在 .env 文件中添加:")
            print("  TELEGRAM_BOT_TOKEN=你的BotToken")
            print("  TELEGRAM_CHAT_ID=你的ChatID")
            sys.exit(1)
        ok = asyncio.run(notifier.send(msg))
        if ok:
            print("✅ 消息已发送!")
        else:
            print("❌ 发送失败，请检查 token 和 chat_id")

    elif cmd == "run":
        # python -m live run [--margin 50] [--loss-limit 100] [--auto-trade]
        _, run_flags = _parse_flags(sys.argv[2:])
        load_dotenv()
        if "margin" in run_flags:
            config.live_fixed_margin_usdt = Decimal(run_flags["margin"])
        if "loss-limit" in run_flags:
            config.daily_loss_limit_usdt = Decimal(run_flags["loss-limit"])

        auto_trade = "auto-trade" in run_flags

        # ── Strategy: Rolling R24 ──────────────────────────────────
        from .rolling_config import RollingLiveConfig
        from .rolling_live_strategy import RollingLiveStrategy
        rolling_config = RollingLiveConfig()
        strategy = RollingLiveStrategy(config=rolling_config)

        # ── Startup confirmation ──────────────────────────────
        print()
        print("=" * 50)
        print("  ⚠️  实盘模式 — 将使用真实资金交易")
        print("=" * 50)

        print(f"  策略:       🔄 Rolling R24 (24h 滚动涨幅)")
        print(f"  涨幅阈值:   {rolling_config.min_pct_chg}%")
        print(f"  Top N:      {rolling_config.top_n}")
        print(f"  扫描间隔:   {rolling_config.scan_interval_hours}h")
        print(f"  保证金:     {config.live_fixed_margin_usdt} USDT / 笔")
        print(f"  杠杆:       {config.leverage}x")
        print(f"  每日亏损限额: {config.daily_loss_limit_usdt} USDT")
        print(f"  止盈:       {rolling_config.tp_initial*100:.0f}% → {rolling_config.tp_reduced*100:.0f}% (>{rolling_config.tp_hours_threshold}h)")
        print(f"  止损:       {rolling_config.sl_threshold*100:.0f}%")
        if rolling_config.enable_trailing_stop:
            print(f"  追踪止损:   激活 {rolling_config.trailing_activation_pct*100:.0f}%, 距离 {rolling_config.trailing_distance_pct*100:.0f}%")
        print(f"  最大持仓时间: {rolling_config.max_hold_days}d")
        print(f"  最大持仓数:  {config.max_positions}")
        print(f"  新币过滤:   {rolling_config.min_listed_days}d")
        print(f"  信号冷却:   {rolling_config.signal_cooldown_hours}h")

        print(f"  自动交易:   {'开启' if auto_trade else '关闭 (可在前端开启)'}")
        print()
        print("  🚀 启动中...")
        print()
        

        trader = LiveTrader(config=config, strategy=strategy)
        trader.auto_trade_enabled = auto_trade
        asyncio.run(trader.start())

    else:
        print(f"Unknown command: {cmd}")
        print()
        print("Usage: python -m live <command> [options]")
        print()
        print("Commands:")
        print("  run                     启动实盘交易")
        print("    --margin N            固定保证金 (USDT, 默认5)")
        print("    --loss-limit N        每日亏损限额 (USDT, 默认50)")
        print("    --auto-trade          启动时开启自动交易 (默认关闭)")

        print("  status                  查看账户状态")
        print("  trades [N]              查看实盘交易记录 (默认50条)")
        print("  signals [N]             查看信号历史 (默认50条)")
        print("  order <sym> <price>     手动下单")
        print("    [qty] [--long] [--tp N] [--sl N] [--leverage N] [--margin N]")
        print("  orders [symbol]         查看挂单")
        print("  positions [symbol]      查看持仓")
        print("  close <symbol>          市价平仓")
        print("  tp <symbol> <price>     手动挂止盈")
        print("  sl <symbol> <price>     手动挂止损")
        print("  cancel <sym> <id>       取消单个订单")
        print("  cancel-all <symbol>     取消全部订单")
        print("  test-notify [message]   测试 Telegram 通知")
        sys.exit(1)


if __name__ == "__main__":
    main()
