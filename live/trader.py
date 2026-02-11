"""Live Trader — main service orchestrating scanner, executor, and monitor.

Runs concurrent async loops:
  1. LiveSurgeScanner → signal_queue (every hour)
  2. Signal consumer → LiveOrderExecutor (entry logic)
  3. LivePositionMonitor (deferred TP/SL after fill)
  4. Memory watchdog
  5. Periodic P&L reports
"""

import asyncio
import logging
import os
import resource
import signal
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from rich.console import Console

from .models import utc_now
from .live_config import LiveTradingConfig
from .live_scanner import LiveSurgeScanner
from .live_executor import LiveOrderExecutor
from .live_position_monitor import LivePositionMonitor
from .store import TradeStore, SignalEvent
from .binance_client import BinanceFuturesClient
from .notifier import TelegramNotifier
from .ws_stream import BinanceUserStream

logger = logging.getLogger(__name__)


class LiveTrader:
    """Live Trading main service.

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
        self.auto_trade_enabled = False  # 自动交易开关 (默认关闭)
        self._main_task: Optional[asyncio.Task] = None

        # Shared Binance client
        self.client = BinanceFuturesClient()

        # Signal queue (scanner → executor)
        self.signal_queue: asyncio.Queue = asyncio.Queue()

        # Persistence (signal events + live trades)
        self.store = TradeStore(self.config.db_path)

        # Scanner
        self.scanner = LiveSurgeScanner(
            config=self.config,
            signal_queue=self.signal_queue,
            client=self.client,
            console=self.console,
        )

        # Notifier
        self.notifier = TelegramNotifier()

        # Live executor
        self.live_executor = LiveOrderExecutor(
            client=self.client,
            leverage=self.config.leverage,
        )

        # Risk filters (uses same Binance client)
        self.risk_filters: Optional[object] = None
        if self.config.enable_risk_filters:
            from .risk_filters import RiskFilters
            self.risk_filters = RiskFilters(self.client)

        # Live position monitor
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
        from dotenv import load_dotenv
        from .telegram_bot import TelegramBot
        load_dotenv()
        self.tg_bot = TelegramBot(
            bot_token=self.notifier.bot_token if self.notifier else os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=self.notifier.chat_id if self.notifier else os.getenv("TELEGRAM_CHAT_ID", ""),
            trader=self,
        )

    async def start(self):
        """Start all sub-services concurrently."""
        self._running = True

        # Register signal handlers — cancel the main task directly
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_shutdown)

        # BinanceFuturesClient requires async with to init HTTP session
        async with self.client:
            try:
                self._main_task = asyncio.current_task()

                # Recover existing positions from exchange
                await self.live_monitor.recover_positions()

                # Print banner (needs client for live account data)
                if self.verbose:
                    await self._print_banner()

                tasks = [
                    self.scanner.run_forever(),
                    self._process_signals(),
                    self.live_monitor.run_forever(),
                    self._daily_pnl_report(),
                    self._memory_watchdog(),
                ]

                # WebSocket user data stream
                self.ws_stream.on_order_update = self.live_monitor.handle_order_update
                tasks.append(self.ws_stream.run_forever())

                # Telegram bot
                if self.tg_bot and self.tg_bot.enabled:
                    tasks.append(self.tg_bot.run_forever())

                # ── FastAPI dashboard backend ──────────────────────
                try:
                    import uvicorn
                    from .api import create_app

                    api_app = create_app(self)
                    api_config = uvicorn.Config(
                        api_app, host="0.0.0.0", port=8899,
                        log_level="warning",
                    )
                    api_server = uvicorn.Server(api_config)
                    tasks.append(api_server.serve())
                    logger.info("🌐 Dashboard API 启动: http://0.0.0.0:8899/docs")
                except ImportError:
                    logger.warning("uvicorn not installed, skipping API server")

                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("LiveTrader cancelled")
            finally:
                self._cleanup()

    def _handle_shutdown(self):
        """Signal handler: cancel the main task to stop all coroutines immediately."""
        self.console.print("\n[bold yellow]⏹ Shutting down Live Trader...[/bold yellow]")
        self._running = False
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()

    def _cleanup(self):
        """Close non-async resources (client is closed by async with)."""
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
                await asyncio.sleep(10)

                # Execute pending signals ONE BY ONE (serialize live entries)
                # Sort by surge ratio descending — strongest signals first
                pending.sort(key=lambda s: s.surge_ratio, reverse=True)
                live_pending: set[str] = set()  # track in-flight symbols
                for i, s in enumerate(pending):
                    if not self._running:
                        break

                    # ── Auto-trade gate ──────────────────────
                    if not self.auto_trade_enabled:
                        self.console.print(
                            f"  [yellow]⏸ Auto-trade OFF[/yellow] — "
                            f"skip {s.symbol} (surge: {s.surge_ratio:.1f}x)"
                        )
                        self.store.save_signal_event(SignalEvent(
                            timestamp=utc_now().isoformat(),
                            symbol=s.symbol,
                            surge_ratio=s.surge_ratio,
                            price=str(s.price),
                            accepted=False,
                            reject_reason="auto_trade_disabled",
                        ))
                        continue

                    self.console.print(
                        f"\n[cyan]📡 Executing entry: {s.symbol}[/cyan] "
                        f"(surge: {s.surge_ratio:.1f}x)"
                    )
                    success = await self._execute_live_entry(
                        s, live_pending=live_pending,
                    )
                    if success:
                        live_pending.add(s.symbol)
                        # Wait between entries so exchange registers
                        # the position before the next guard check
                        if i < len(pending) - 1:
                            await asyncio.sleep(2)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Signal processing error: %s", e, exc_info=True)

    async def _execute_live_entry(self, signal, *, live_pending: set[str] | None = None) -> bool:
        """Execute a live entry: risk filter → position sizing → real order.

        Returns True if an order was placed successfully, False otherwise.
        `live_pending` tracks symbols with orders already placed in the current
        batch, so the guard check can account for them even before the exchange
        API reflects them.
        """
        symbol = signal.symbol
        now = utc_now()
        pending = live_pending or set()

        # ── Guard checks (exchange positions + in-flight orders) ──
        try:
            all_pos = await self.client.get_position_risk()
            open_pos = [p for p in all_pos if float(p.position_amt) != 0]
            open_symbols = {p.symbol for p in open_pos}

            # Combine exchange positions with pending (in-flight) symbols
            combined_symbols = open_symbols | pending
            combined_count = len(open_symbols | pending)

            if symbol in combined_symbols:
                self.console.print(f"  [dim]Skip {symbol}: already in position (exchange/pending)[/dim]")
                self.store.save_signal_event(SignalEvent(
                    timestamp=now.isoformat(), symbol=symbol,
                    surge_ratio=signal.surge_ratio, price=str(signal.price),
                    accepted=False, reject_reason="already in position",
                ))
                return False
            if combined_count >= self.config.max_positions:
                reason = (f"max positions reached ({len(open_pos)} exchange"
                          f" + {len(pending)} pending ≥ {self.config.max_positions})")
                self.console.print(f"  [dim]Skip {symbol}: {reason}[/dim]")
                self.store.save_signal_event(SignalEvent(
                    timestamp=now.isoformat(), symbol=symbol,
                    surge_ratio=signal.surge_ratio, price=str(signal.price),
                    accepted=False, reject_reason=reason,
                ))
                return False
        except Exception as e:
            logger.warning("Failed to check exchange positions: %s", e)
            return False  # fail-closed for safety

        # ── Get real-time price ──────────────────────────────────
        try:
            ticker = await self.client.get_ticker_price(symbol)
            entry_price = ticker.price
        except Exception as e:
            logger.warning("Failed to get price for %s: %s", symbol, e)
            self.store.save_signal_event(SignalEvent(
                timestamp=now.isoformat(), symbol=symbol,
                surge_ratio=signal.surge_ratio, price=str(signal.price),
                accepted=False, reject_reason=f"price fetch failed: {e}",
            ))
            return False

        signal_price = Decimal(str(signal.price))

        # ── Risk filters ───────────────────────────────────────
        if self.risk_filters:
            try:
                result = await self.risk_filters.check_all(
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
                    return False
            except Exception as e:
                logger.warning("Risk filter error for %s (fail-open): %s", symbol, e)

        # ── Daily loss limit check ───────────────────────────────
        if self.config.daily_loss_limit_usdt > 0:
            try:
                daily_pnl = await self.client.get_daily_realized_pnl()
                logger.info("📊 今日已实现盈亏: %s USDT", daily_pnl)
                if daily_pnl <= -self.config.daily_loss_limit_usdt:
                    reason = f"daily loss limit ({daily_pnl} ≤ -{self.config.daily_loss_limit_usdt})"
                    self.console.print(
                        f"  [red]🛑 每日亏损限额已达 ({daily_pnl} USDT ≤ -{self.config.daily_loss_limit_usdt})"
                        f" — 停止开新仓[/red]"
                    )
                    logger.warning(
                        "每日亏损限额触发: %s USDT, 限额 %s USDT",
                        daily_pnl, self.config.daily_loss_limit_usdt,
                    )
                    self.store.save_signal_event(SignalEvent(
                        timestamp=now.isoformat(), symbol=symbol,
                        surge_ratio=signal.surge_ratio, price=str(entry_price),
                        accepted=False, reject_reason=reason,
                    ))
                    if self.notifier:
                        await self.notifier.notify_daily_loss_limit(
                            str(daily_pnl), str(self.config.daily_loss_limit_usdt)
                        )
                    return False
            except Exception as e:
                logger.warning("查询每日盈亏失败 (fail-open): %s", e)

        # ── Position sizing ────────────────────────────────────────
        if self.config.margin_mode == "percent":
            try:
                account_info = await self.client.get_account_info()
                available = Decimal(str(account_info.get("availableBalance", "0")))
                margin = available * Decimal(str(self.config.margin_pct)) / Decimal("100")
                margin = max(margin, Decimal("1"))  # 最少 1 USDT
                logger.info("百分比保证金: %.2f USDT (%.1f%% of %.2f)",
                            margin, self.config.margin_pct, available)
            except Exception as e:
                logger.warning("获取余额失败, 降级为固定保证金: %s", e)
                margin = self.config.live_fixed_margin_usdt
        else:
            margin = self.config.live_fixed_margin_usdt
            logger.info("固定保证金: %s USDT", margin)
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
                if order_result.get("deferred_tp_sl"):
                    self.live_monitor.track(
                        symbol=symbol,
                        entry_order_id=order_result["entry_order"].order_id,
                        side="SHORT",
                        quantity=str(quantity),
                        deferred_tp_sl=order_result["deferred_tp_sl"],
                    )
                # Telegram notification
                if self.notifier:
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
            return False

        return True  # order was placed

    # ------------------------------------------------------------------
    # Periodic Tasks
    # ------------------------------------------------------------------

    async def _memory_watchdog(self):
        """Monitor process memory; warn at 500 MB, auto-exit at 800 MB."""
        WARN_MB = 500
        KILL_MB = 800
        CHECK_INTERVAL = 300  # 5 minutes
        warned = False

        while self._running:
            await asyncio.sleep(CHECK_INTERVAL)
            try:
                # macOS: ru_maxrss is in bytes
                rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                rss_mb = rss_bytes / (1024 * 1024)

                if rss_mb >= KILL_MB:
                    msg = (
                        f"🚨 内存超限 {rss_mb:.0f} MB ≥ {KILL_MB} MB，"
                        f"进程即将自动退出重启"
                    )
                    logger.critical(msg)
                    if self.notifier and self.notifier.enabled:
                        await self.notifier.send(msg)
                    self.console.print(f"\n[red bold]{msg}[/red bold]")
                    # Graceful shutdown — let run_forever.sh restart us
                    os._exit(1)

                elif rss_mb >= WARN_MB and not warned:
                    msg = (
                        f"⚠️ 内存偏高 {rss_mb:.0f} MB (阈值 {WARN_MB} MB)，"
                        f"请关注"
                    )
                    logger.warning(msg)
                    if self.notifier and self.notifier.enabled:
                        await self.notifier.send(msg)
                    warned = True

            except Exception as e:
                logger.warning("Memory watchdog error: %s", e)

    async def _daily_pnl_report(self):
        """Send P&L summary to Telegram periodically (every 4 hours)."""
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
        """Print startup banner with real account data."""
        self.console.print()
        self.console.print("[bold cyan]" + "=" * 60 + "[/bold cyan]")
        self.console.print("[bold cyan]🚀 Trader — Surge Short V2 | 🔴 实盘模式 (LIVE)[/bold cyan]")
        self.console.print("[bold cyan]" + "=" * 60 + "[/bold cyan]")

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
        self.console.print(f"  Leverage:     {self.config.leverage}x")
        self.console.print(f"  Max pos:      {self.config.max_positions}")
        self.console.print(f"  TP:           {self.config.strong_tp_pct}/{self.config.medium_tp_pct}/{self.config.weak_tp_pct}%")
        self.console.print(f"  SL:           {self.config.stop_loss_pct}%")
        self.console.print(f"  Max hold:     {self.config.max_hold_hours}h")
        self.console.print(f"  Surge thr:    {self.config.surge_threshold}x")
        self.console.print(f"  Monitor intv: {self.config.monitor_interval_seconds}s")
        auto_status = "[green]开启[/green]" if self.auto_trade_enabled else "[red]关闭[/red]"
        self.console.print(f"  Auto trade:   {auto_status}")

        # Show real position count from Binance
        try:
            all_pos = await self.client.get_position_risk()
            live_count = sum(1 for p in all_pos if float(p.position_amt) != 0)
            self.console.print(f"  Live pos:     {live_count}")
        except Exception:
            self.console.print(f"  Live pos:     [dim]获取失败[/dim]")
        live_trades = self.store.get_live_trades(limit=9999)
        self.console.print(f"  Live trades:  {len(live_trades)}")

        self.console.print("[bold cyan]" + "=" * 60 + "[/bold cyan]")
        self.console.print()
