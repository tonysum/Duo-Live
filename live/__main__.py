"""duo-live entry point.

Usage:
    python -m live run                                  # 模拟模式启动
    python -m live run --live [--margin N] [--loss-limit N]  # 实盘模式
    python -m live status                               # 查看状态 & 资金
    python -m live trades                               # 查看历史成交
    python -m live signals                              # 查看信号历史
    python -m live live-trades [N]                       # 查看实盘交易记录
    python -m live order <symbol> <price> [qty]          # 手动下单
        [--long] [--tp N] [--sl N] [--leverage N] [--margin N]
    python -m live orders [symbol]                      # 查看挂单
    python -m live positions [symbol]                   # 查看持仓
    python -m live close <symbol>                       # 市价平仓
    python -m live tp <symbol> <price>                  # 手动挂止盈
    python -m live sl <symbol> <price>                  # 手动挂止损
    python -m live cancel <symbol> <id>                 # 取消单个订单
    python -m live cancel-all <symbol>                  # 取消全部订单
    python -m live test-notify [message]                # 测试 Telegram 通知
"""

import asyncio
import logging
import sys
from decimal import Decimal

from dotenv import load_dotenv

from .live_config import LiveTradingConfig
from .paper_trader import PaperTrader, print_status, print_trades, print_signals


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
        print("\n🔍 监控入场单状态... (Ctrl+C 退出监控，TP/SL 需手动设置)")

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
                while mon.tracked_count > 0:
                    await mon._check_all()
                    if mon.tracked_count == 0:
                        break
                    await asyncio.sleep(10)
                print("\n✅ 监控结束")

        try:
            asyncio.run(_monitor())
        except KeyboardInterrupt:
            print("\n⏹️ 监控已停止 (入场单仍在挂单中)")


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

    config = LiveTradingConfig()

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
        print_status(config)

    elif cmd == "trades":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        print_trades(config, limit=limit)

    elif cmd == "signals":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        print_signals(config, limit=limit)

    elif cmd == "live-trades":
        from rich.console import Console
        from rich.table import Table
        from .paper_store import PaperStore
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        store = PaperStore(config.paper_db_path)
        trades = store.get_live_trades(limit=limit)
        store.close()
        console = Console()
        if not trades:
            console.print("[dim]暂无实盘交易记录[/dim]")
        else:
            table = Table(title=f"📋 实盘交易记录 (最近 {len(trades)} 笔)", show_lines=True)
            table.add_column("时间", style="dim")
            table.add_column("币种", style="bold")
            table.add_column("方向")
            table.add_column("事件")
            table.add_column("开仓价", justify="right")
            table.add_column("数量", justify="right")
            table.add_column("保证金", justify="right")
            table.add_column("Order/Algo ID", style="dim")
            event_colors = {"entry": "cyan", "tp": "green", "sl": "red", "timeout": "yellow", "close": "magenta"}
            for t in trades:
                color = event_colors.get(t.event, "white")
                dir_color = "green" if t.side == "LONG" else "red"
                table.add_row(
                    t.timestamp[:19] if t.timestamp else "",
                    t.symbol,
                    f"[{dir_color}]{t.side}[/{dir_color}]",
                    f"[{color}]{t.event.upper()}[/{color}]",
                    t.entry_price,
                    t.quantity,
                    f"{t.margin_usdt}U" if t.margin_usdt else "",
                    t.order_id or t.algo_id or "",
                )
            console.print(table)

    elif cmd == "order":
        # Two modes:
        #   python -m live order <symbol> <price> <quantity> [flags]   # 指定数量
        #   python -m live order <symbol> <price> --margin <USDT>      # 指定保证金
        pos_args, flags = _parse_flags(sys.argv[2:])
        leverage = int(flags.get("leverage", config.leverage))
        side = "LONG" if "long" in flags else "SHORT"
        tp_pct = float(flags.get("tp", config.strong_tp_pct))
        sl_pct = float(flags.get("sl", config.stop_loss_pct))

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
        # python -m live run [--live] [--margin 50] [--loss-limit 100]
        _, run_flags = _parse_flags(sys.argv[2:])
        if "live" in run_flags:
            load_dotenv()
            config.live_mode = True
            if "margin" in run_flags:
                config.live_fixed_margin_usdt = Decimal(run_flags["margin"])
            if "loss-limit" in run_flags:
                config.daily_loss_limit_usdt = Decimal(run_flags["loss-limit"])

            # ── Startup confirmation ──────────────────────────────
            print()
            print("=" * 50)
            print("  ⚠️  实盘模式 — 将使用真实资金交易")
            print("=" * 50)
            print(f"  保证金:     {config.live_fixed_margin_usdt} USDT / 笔")
            print(f"  杠杆:       {config.leverage}x")
            print(f"  每日亏损限额: {config.daily_loss_limit_usdt} USDT")
            print(f"  止盈:       {config.strong_tp_pct}%")
            print(f"  止损:       {config.stop_loss_pct}%")
            print(f"  最大持仓时间: {config.max_hold_hours}h")
            print(f"  最大持仓数:  {config.max_positions}")
            print()
            confirm = input("  输入 yes 确认启动: ").strip().lower()
            if confirm != "yes":
                print("  ❌ 已取消")
                sys.exit(0)
            print()

        trader = PaperTrader(config=config)
        asyncio.run(trader.start())

    else:
        print(f"Unknown command: {cmd}")
        print()
        print("Usage: python -m live <command> [options]")
        print()
        print("Commands:")
        print("  run                     启动交易 (默认模拟模式)")
        print("    --live                实盘模式")
        print("    --margin N            固定保证金 (USDT, 默认100, 0=按比例)")
        print("    --loss-limit N        每日亏损限额 (USDT, 默认200, 0=不限)")
        print("  status                  查看状态 & 资金")
        print("  trades                  查看历史成交")
        print("  signals                 查看信号历史")
        print("  live-trades [N]         查看实盘交易记录 (默认50条)")
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

