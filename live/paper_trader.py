"""Paper Trader — main service orchestrating scanner, executor, and monitor.

Runs three concurrent async loops:
  1. LiveSurgeScanner → signal_queue (every hour)
  2. Signal consumer → PaperOrderExecutor (entry logic)
  3. PositionMonitor (every 30s, exit logic)
  4. Equity snapshots (every hour)
"""

import asyncio
import logging
import signal
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from rich.console import Console
from rich.table import Table

from .models import utc_now
from .live_config import LiveTradingConfig
from .live_scanner import LiveSurgeScanner
from .paper_executor import PaperOrderExecutor
from .paper_store import PaperStore
from .position_monitor import PositionMonitor
from .binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)


class PaperTrader:
    """Paper Trading main service.

    Orchestrates signal scanning, order execution, and position monitoring
    into a single async application with graceful shutdown.
    """

    def __init__(
        self,
        config: Optional[LiveTradingConfig] = None,
        verbose: bool = True,
    ):
        self.config = config or LiveTradingConfig()
        self.verbose = verbose
        self.console = Console()
        self._running = False
        self._main_task: Optional[asyncio.Task] = None

        # Shared Binance client
        self.client = BinanceFuturesClient()

        # Signal queue (scanner → executor)
        self.signal_queue: asyncio.Queue = asyncio.Queue()

        # Components
        self.store = PaperStore(self.config.paper_db_path)
        self.executor = PaperOrderExecutor(
            config=self.config,
            store=self.store,
            client=self.client,
            console=self.console,
        )
        self.scanner = LiveSurgeScanner(
            config=self.config,
            signal_queue=self.signal_queue,
            client=self.client,
            console=self.console,
        )
        self.monitor = PositionMonitor(
            config=self.config,
            store=self.store,
            executor=self.executor,
            client=self.client,
            console=self.console,
        )

        # Live executor + monitor + notifier (only created when live_mode=True)
        self.live_executor = None
        self.live_monitor = None
        self.notifier = None
        self.ws_stream = None
        self.tg_bot = None
        if self.config.live_mode:
            from .live_executor import LiveOrderExecutor
            from .live_position_monitor import LivePositionMonitor
            from .notifier import TelegramNotifier
            from .ws_stream import BinanceUserStream
            from .telegram_bot import TelegramBot
            self.notifier = TelegramNotifier()
            self.live_executor = LiveOrderExecutor(
                client=self.client,
                leverage=self.config.leverage,
            )
            self.live_monitor = LivePositionMonitor(
                client=self.client,
                executor=self.live_executor,
                config=self.config,
                notifier=self.notifier,
                store=self.store,
            )
            # WebSocket user data stream (real-time fills)
            self.ws_stream = BinanceUserStream(client=self.client)
            # Telegram bot for remote control
            self.tg_bot = TelegramBot(
                bot_token=self.notifier.bot_token,
                chat_id=self.notifier.chat_id,
                paper_trader=self,
            )

    async def start(self):
        """Start all sub-services concurrently."""
        self._running = True

        # Restore state from persistent store
        self.executor.restore_state()

        # Register signal handlers — cancel the main task directly
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_shutdown)

        # BinanceFuturesClient requires async with to init HTTP session
        async with self.client:
            try:
                self._main_task = asyncio.current_task()

                # Recover existing positions from exchange (live mode)
                if self.live_monitor:
                    await self.live_monitor.recover_positions()

                # Print banner (needs client for live account data)
                if self.verbose:
                    await self._print_banner()

                tasks = [
                    self.scanner.run_forever(),
                    self._process_signals(),
                    self.monitor.run_forever(),
                    self._snapshot_equity_periodically(),
                ]
                if self.live_monitor:
                    tasks.append(self.live_monitor.run_forever())
                    tasks.append(self._daily_pnl_report())
                if self.ws_stream:
                    self.ws_stream.on_order_update = self.live_monitor.handle_order_update
                    tasks.append(self.ws_stream.run_forever())
                if self.tg_bot and self.tg_bot.enabled:
                    tasks.append(self.tg_bot.run_forever())
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("PaperTrader cancelled")
            finally:
                self._cleanup()

    def _handle_shutdown(self):
        """Signal handler: cancel the main task to stop all coroutines immediately."""
        self.console.print("\n[bold yellow]⏹ Shutting down Paper Trader...[/bold yellow]")
        self._running = False
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()

    def _cleanup(self):
        """Close non-async resources (client is closed by async with)."""
        self.executor.cleanup()
        self.store.close()
        self.console.print("[dim]Resources cleaned up.[/dim]")

    # ------------------------------------------------------------------
    # Signal Consumer
    # ------------------------------------------------------------------

    async def _process_signals(self):
        """Consume signals from the queue, hold in pending pool 60s, then enter."""
        while self._running:
            try:
                # Wait for first signal with timeout to allow shutdown checks
                try:
                    sig = await asyncio.wait_for(self.signal_queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue

                # Collect all signals currently in the queue into a pending batch
                pending = [sig]
                while not self.signal_queue.empty():
                    try:
                        pending.append(self.signal_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                self.console.print(
                    f"\n[cyan]📡 {len(pending)} signal(s) detected, "
                    f"entering pending pool (60s delay)...[/cyan]"
                )
                for s in pending:
                    self.console.print(
                        f"  [dim]• {s.symbol} surge={s.surge_ratio:.1f}x "
                        f"signal_price={s.price:.6f}[/dim]"
                    )

                # Wait 60 seconds before executing entries
                await asyncio.sleep(60)

                # Execute all pending signals
                for s in pending:
                    if not self._running:
                        break
                    self.console.print(
                        f"\n[cyan]📡 Executing entry: {s.symbol}[/cyan] "
                        f"(surge: {s.surge_ratio:.1f}x)"
                    )
                    if self.config.live_mode and self.live_executor:
                        await self._execute_live_entry(s)
                    else:
                        await self.executor.execute_entry(s)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Signal processing error: %s", e, exc_info=True)

    async def _execute_live_entry(self, signal):
        """Execute a live entry: risk filter → position sizing → real order."""
        symbol = signal.symbol
        now = utc_now()

        # ── Guard checks (reuse paper executor's state) ─────────────
        existing = self.store.get_position(symbol)
        if existing:
            self.console.print(f"  [dim]Skip {symbol}: already in position[/dim]")
            return
        if self.store.position_count() >= self.config.max_positions:
            self.console.print(f"  [dim]Skip {symbol}: max positions reached[/dim]")
            return

        # ── Get real-time price ──────────────────────────────────
        try:
            ticker = await self.client.get_ticker_price(symbol)
            entry_price = ticker.price
        except Exception as e:
            logger.warning("Failed to get price for %s: %s", symbol, e)
            return

        signal_price = Decimal(str(signal.price))

        # ── Risk filters ───────────────────────────────────────
        if self.executor.risk_filters:
            try:
                from .paper_store import SignalEvent
                result = await self.executor.risk_filters.check_all(
                    symbol, now, entry_price, signal_price
                )
                if not result.should_trade:
                    import json as _json
                    self.store.save_signal_event(SignalEvent(
                        timestamp=now.isoformat(),
                        symbol=symbol,
                        surge_ratio=signal.surge_ratio,
                        price=str(entry_price),
                        accepted=False,
                        reject_reason=result.reason,
                        risk_metrics_json=_json.dumps(
                            result.metrics or {}, default=str
                        ),
                    ))
                    self.console.print(
                        f"  [yellow]FILTERED[/yellow] {symbol}: {result.reason}"
                    )
                    return
            except Exception as e:
                logger.warning("Risk filter error for %s (fail-open): %s", symbol, e)

        # ── Daily loss limit check (live mode only) ──────────────
        if self.config.live_mode and self.config.daily_loss_limit_usdt > 0:
            try:
                daily_pnl = await self.client.get_daily_realized_pnl()
                logger.info("📊 今日已实现盈亏: %s USDT", daily_pnl)
                if daily_pnl <= -self.config.daily_loss_limit_usdt:
                    self.console.print(
                        f"  [red]🛑 每日亏损限额已达 ({daily_pnl} USDT ≤ -{self.config.daily_loss_limit_usdt})"
                        f" — 停止开新仓[/red]"
                    )
                    logger.warning(
                        "每日亏损限额触发: %s USDT, 限额 %s USDT",
                        daily_pnl, self.config.daily_loss_limit_usdt,
                    )
                    if self.notifier:
                        await self.notifier.notify_daily_loss_limit(
                            str(daily_pnl), str(self.config.daily_loss_limit_usdt)
                        )
                    return
            except Exception as e:
                logger.warning("查询每日盈亏失败 (fail-open): %s", e)

        # ── Position sizing ─────────────────────────────────────
        MIN_MARGIN = Decimal("100")
        if self.config.live_mode and self.config.live_fixed_margin_usdt > 0:
            # Live mode: use fixed margin amount
            margin = self.config.live_fixed_margin_usdt
            logger.info("实盘固定保证金: %s USDT", margin)
        else:
            # Paper mode: percentage-based
            capital = self.executor.capital
            margin = capital * self.config.position_size_pct
            if margin < MIN_MARGIN:
                if capital >= MIN_MARGIN:
                    margin = MIN_MARGIN
                    logger.info("保证金 %.2f 不足 → 使用最低 %s USDT", float(margin), MIN_MARGIN)
                else:
                    self.console.print(
                        f"  [dim]Skip {symbol}: 余额不足最低保证金 {MIN_MARGIN} USDT[/dim]"
                    )
                    return
        quantity = margin * self.config.leverage / entry_price

        # ── Place live order ────────────────────────────────────
        try:
            order_result = await self.live_executor.open_position(
                symbol=symbol,
                price=entry_price,
                quantity=quantity,
                side="SHORT",
                tp_pct=self.config.strong_tp_pct,
                sl_pct=self.config.stop_loss_pct,
            )
            if order_result.get("entry_order"):
                from .paper_store import SignalEvent
                self.store.save_signal_event(SignalEvent(
                    timestamp=now.isoformat(),
                    symbol=symbol,
                    surge_ratio=signal.surge_ratio,
                    price=str(entry_price),
                    accepted=True,
                ))
                self.console.print(
                    f"  [green]🟢 LIVE ENTRY[/green] {symbol} SHORT @ {entry_price} "
                    f"(qty: {quantity:.4f})"
                )
                # Track position in live monitor
                if self.live_monitor and order_result.get("deferred_tp_sl"):
                    self.live_monitor.track(
                        symbol=symbol,
                        entry_order_id=order_result["entry_order"].order_id,
                        side="SHORT",
                        quantity=str(quantity),
                        deferred_tp_sl=order_result["deferred_tp_sl"],
                    )
                # Telegram notification
                if self.notifier:
                    d = order_result.get("deferred_tp_sl", {})
                    await self.notifier.notify_entry_placed(
                        symbol=symbol, side="SHORT",
                        price=str(entry_price), qty=f"{quantity:.4f}",
                        margin=str(margin),
                        order_id=str(order_result["entry_order"].order_id),
                    )
            elif order_result.get("error"):
                self.console.print(
                    f"  [red]LIVE ORDER FAILED[/red] {symbol}: {order_result['error']}"
                )
        except Exception as e:
            logger.error("实盘下单失败 %s: %s", symbol, e, exc_info=True)

    # ------------------------------------------------------------------
    # Equity Snapshots
    # ------------------------------------------------------------------

    async def _snapshot_equity_periodically(self):
        """Record equity snapshots every hour."""
        while self._running:
            try:
                positions = self.store.get_open_positions()
                equity = self.executor.capital
                # Add unrealized PnL for open positions
                for pos in positions:
                    try:
                        ticker = await self.client.get_ticker_price(pos.symbol)
                        entry_p = Decimal(pos.entry_price)
                        size = Decimal(pos.size)
                        unrealized = (entry_p - ticker.price) * size  # SHORT
                        equity += unrealized
                    except Exception:
                        pass

                self.store.save_equity_snapshot(
                    equity=equity,
                    cash=self.executor.capital,
                    open_positions=len(positions),
                )
            except Exception as e:
                logger.error("Equity snapshot error: %s", e)

            await asyncio.sleep(3600)  # Every hour

    async def _daily_pnl_report(self):
        """Send P&L summary to Telegram periodically (every 4 hours) in live mode."""
        REPORT_INTERVAL = 4 * 3600  # 4 hours
        while self._running:
            await asyncio.sleep(REPORT_INTERVAL)
            if not self.notifier or not self.notifier.enabled:
                continue
            try:
                bal = await self.client.get_account_balance()
                daily_pnl = await self.client.get_daily_realized_pnl()

                # Count open positions
                all_pos = await self.client.get_position_risk()
                open_count = sum(1 for p in all_pos if float(p.position_amt) != 0)

                # Count today's live trades
                from datetime import datetime, timezone
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                all_live = self.store.get_live_trades(limit=9999)
                today_trades = sum(
                    1 for t in all_live
                    if t.timestamp and t.timestamp.startswith(today)
                )

                await self.notifier.notify_daily_summary(
                    total_balance=f"{bal['total_balance']:,.2f}",
                    daily_pnl=f"{daily_pnl:+,.2f}",
                    unrealized_pnl=f"{bal['unrealized_pnl']:+,.2f}",
                    open_positions=open_count,
                    trades_today=today_trades,
                )
                logger.info("📊 已推送每日盈亏报告")
            except Exception as e:
                logger.warning("推送盈亏报告失败: %s", e)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    async def _print_banner(self):
        """Print startup banner with real account data in live mode."""
        mode = "🔴 实盘模式 (LIVE)" if self.config.live_mode else "🟢 模拟模式 (PAPER)"
        self.console.print()
        self.console.print("[bold cyan]" + "=" * 60 + "[/bold cyan]")
        self.console.print(f"[bold cyan]🚀 Trader — Surge Short V2 | {mode}[/bold cyan]")
        self.console.print("[bold cyan]" + "=" * 60 + "[/bold cyan]")

        if self.config.live_mode:
            # Fetch real account data from Binance
            try:
                bal = await self.client.get_account_balance()
                total = bal["total_balance"]
                available = bal["available_balance"]
                unrealized = bal["unrealized_pnl"]
                self.console.print(f"  Account:      [bold]${total:,.2f}[/bold] USDT")
                self.console.print(f"  Available:    ${available:,.2f} USDT")
                if unrealized != 0:
                    color = "green" if unrealized > 0 else "red"
                    self.console.print(f"  Unrealized:   [{color}]{unrealized:+,.2f}[/{color}] USDT")
            except Exception as e:
                self.console.print(f"  Account:      [red]获取失败: {e}[/red]")

            self.console.print(f"  Fixed margin: {self.config.live_fixed_margin_usdt} USDT / 笔")
            self.console.print(f"  Daily limit:  {self.config.daily_loss_limit_usdt} USDT")
        else:
            self.console.print(f"  Capital:      ${self.executor.capital:,.2f}")
            self.console.print(f"  Position:     {self.config.position_size_pct:.1%}")

        self.console.print(f"  Leverage:     {self.config.leverage}x")
        self.console.print(f"  Max pos:      {self.config.max_positions}")
        self.console.print(f"  TP:           {self.config.strong_tp_pct}/{self.config.medium_tp_pct}/{self.config.weak_tp_pct}%")
        self.console.print(f"  SL:           {self.config.stop_loss_pct}%")
        self.console.print(f"  Max hold:     {self.config.max_hold_hours}h")
        self.console.print(f"  Surge thr:    {self.config.surge_threshold}x")
        self.console.print(f"  Monitor intv: {self.config.monitor_interval_seconds}s")

        if self.config.live_mode:
            # Show real position count from Binance
            try:
                all_pos = await self.client.get_position_risk()
                live_count = sum(1 for p in all_pos if float(p.position_amt) != 0)
                self.console.print(f"  Live pos:     {live_count}")
            except Exception:
                self.console.print(f"  Live pos:     [dim]获取失败[/dim]")
            live_trades = self.store.get_live_trades(limit=9999)
            self.console.print(f"  Live trades:  {len(live_trades)}")
        else:
            open_positions = self.store.position_count()
            total_trades = self.store.get_trade_count()
            self.console.print(f"  Open pos:     {open_positions}")
            self.console.print(f"  Total trades: {total_trades}")

        self.console.print("[bold cyan]" + "=" * 60 + "[/bold cyan]")
        self.console.print()


# ------------------------------------------------------------------
# Status / Trades Display Helpers
# ------------------------------------------------------------------

def print_status(config: Optional[LiveTradingConfig] = None):
    """Print current paper trading status."""
    cfg = config or LiveTradingConfig()
    console = Console()
    store = PaperStore(cfg.paper_db_path)

    # Capital
    capital = store.get_state("capital", str(cfg.initial_capital))
    console.print(f"\n[bold]💰 Capital: ${Decimal(capital):,.2f}[/bold]")

    # Open positions
    positions = store.get_open_positions()
    if positions:
        table = Table(title=f"📊 Open Positions ({len(positions)})")
        table.add_column("Symbol", style="cyan")
        table.add_column("Entry Price", justify="right")
        table.add_column("Entry Time", style="dim")
        table.add_column("Margin", justify="right")
        table.add_column("TP%", justify="right")
        table.add_column("Strength", style="yellow")
        table.add_column("Status")
        table.add_column("Surge", justify="right", style="red")

        for pos in positions:
            table.add_row(
                pos.symbol,
                f"${Decimal(pos.entry_price):,.6f}",
                pos.entry_time[:19],
                f"${Decimal(pos.margin):,.2f}",
                f"{pos.tp_pct:.0f}%",
                pos.strength,
                pos.status,
                f"{pos.signal_surge_ratio:.1f}x",
            )
        console.print(table)
    else:
        console.print("[dim]No open positions.[/dim]")

    store.close()


def print_trades(config: Optional[LiveTradingConfig] = None, limit: int = 20):
    """Print recent paper trades."""
    cfg = config or LiveTradingConfig()
    console = Console()
    store = PaperStore(cfg.paper_db_path)

    trades = store.get_trades(limit=limit)
    if trades:
        table = Table(title=f"📈 Recent Trades (last {len(trades)})")
        table.add_column("Symbol", style="cyan")
        table.add_column("Entry", justify="right")
        table.add_column("Exit", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("PnL%", justify="right")
        table.add_column("Hours", justify="right")
        table.add_column("Reason", style="dim")
        table.add_column("Strength", style="yellow")

        for t in trades:
            pnl = Decimal(t.pnl)
            pnl_pct = Decimal(t.pnl_pct)
            color = "green" if pnl > 0 else "red"
            table.add_row(
                t.symbol,
                f"${Decimal(t.entry_price):,.6f}",
                f"${Decimal(t.exit_price):,.6f}",
                f"[{color}]${pnl:+,.2f}[/{color}]",
                f"[{color}]{pnl_pct:+.2f}%[/{color}]",
                f"{t.hold_hours:.1f}",
                t.exit_reason,
                t.coin_strength,
            )
        console.print(table)

        # Summary
        total_pnl = sum(Decimal(t.pnl) for t in trades)
        wins = sum(1 for t in trades if Decimal(t.pnl) > 0)
        console.print(
            f"\n  Total PnL: ${total_pnl:+,.2f}  |  "
            f"Win rate: {wins}/{len(trades)} ({wins/len(trades)*100:.0f}%)"
        )
    else:
        console.print("[dim]No trades yet.[/dim]")

    store.close()


def print_signals(config: Optional[LiveTradingConfig] = None, limit: int = 50):
    """Print signal history."""
    cfg = config or LiveTradingConfig()
    console = Console()
    store = PaperStore(cfg.paper_db_path)

    events = store.get_signal_events(limit=limit)
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

    store.close()
