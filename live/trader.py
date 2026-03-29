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
from typing import Any, Optional

from rich.console import Console

from .models import utc_now
from .live_config import LiveTradingConfig
from .live_executor import LiveOrderExecutor
from .live_position_monitor import LivePositionMonitor
from .store import TradeStore, SignalEvent
from .binance_client import BinanceFuturesClient
from .notifier import TelegramNotifier
from .ws_stream import BinanceUserStream
from .strategy import Strategy, signal_strategy_id, strategy_registry_key

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
        strategy: Optional[Strategy] = None,
        extra_strategies: Optional[list[Strategy]] = None,
    ):
        self.config = config or LiveTradingConfig()
        self.verbose = verbose
        self.console = Console()
        self._running = False
        self.auto_trade_enabled = False  # 自动交易开关 (默认关闭)
        self._main_task: Optional[asyncio.Task] = None
        self._process_started_at: datetime | None = None  # 本进程进入 trader 主循环的时间 (UTC)

        # Pluggable strategy (required — set by __main__.py)
        if strategy is None:
            raise ValueError("strategy is required")
        self.strategy = strategy
        extras = list(extra_strategies or [])
        self._strategies_ordered: list[Strategy] = [self.strategy] + extras

        # Strategy config shortcut (primary — API /dashboard rolling snapshot)
        self.rolling_config = getattr(self.strategy, 'config', None)

        # Multi-strategy: id → Strategy
        reg: dict[str, Strategy] = {}
        for s in self._strategies_ordered:
            sk = strategy_registry_key(s)
            if sk in reg:
                raise ValueError(
                    f"Duplicate strategy_id {sk!r} — use unique id per config.strategies slot",
                )
            reg[sk] = s
        self._strategy_registry = reg

        # Shared Binance client
        self.client = BinanceFuturesClient()

        # Signal queue (all scanners fan-in → executor)
        self.signal_queue: asyncio.Queue = asyncio.Queue()

        # Persistence (signal events + live trades)
        self.store = TradeStore(self.config.db_path)
        for s in self._strategies_ordered:
            if hasattr(s, '_store'):
                s._store = self.store

        # Scanners — one per strategy, shared queue
        self.scanners: list[Any] = [
            s.create_scanner(
                config=self.config,
                signal_queue=self.signal_queue,
                client=self.client,
                console=self.console,
            )
            for s in self._strategies_ordered
        ]
        self.scanner = self.scanners[0]

        # Notifier
        self.notifier = TelegramNotifier()

        # Live executor
        self.live_executor = LiveOrderExecutor(
            client=self.client,
            leverage=self.config.leverage,
        )

        # Live position monitor
        self.live_monitor = LivePositionMonitor(
            client=self.client,
            executor=self.live_executor,
            config=self.config,
            notifier=self.notifier,
            store=self.store,
            strategy=self.strategy,
            strategy_registry=self._strategy_registry,
            on_sl_triggered=self._dispatch_sl_cooldown,
        )

        # WebSocket user data stream (real-time fills)
        self.ws_stream = BinanceUserStream(client=self.client, notifier=self.notifier)

        # Telegram bot for remote control
        from dotenv import load_dotenv
        from .telegram_bot import TelegramBot
        load_dotenv()
        self.tg_bot = TelegramBot(
            bot_token=self.notifier.bot_token if self.notifier else os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=self.notifier.chat_id if self.notifier else os.getenv("TELEGRAM_CHAT_ID", ""),
            trader=self,
        )

    def _dispatch_sl_cooldown(self, symbol: str, strategy_id: str = "") -> None:
        """After SL, cooldown only the scanner that owns ``strategy_id`` (if known)."""
        for sc in self.scanners:
            sc_sid = getattr(sc, '_strategy_id', None)
            if (
                isinstance(sc_sid, str)
                and sc_sid
                and isinstance(strategy_id, str)
                and strategy_id
                and sc_sid != strategy_id
            ):
                continue
            fn = getattr(sc, 'add_sl_cooldown', None)
            if callable(fn):
                fn(symbol)

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
                self._process_started_at = datetime.now(timezone.utc)

                # Recover existing positions from exchange
                await self.live_monitor.recover_positions()

                # Print banner (needs client for live account data)
                if self.verbose:
                    await self._print_banner()

                tasks = [
                    *[sc.run_forever() for sc in self.scanners],
                    self._process_signals(),
                    self.live_monitor.run_forever(),
                    self._daily_pnl_report(),
                    self._memory_watchdog(),
                ]

                # WebSocket user data stream
                self.ws_stream.on_order_update = self.live_monitor.handle_order_update
                self.ws_stream.on_account_update = self.live_monitor.handle_account_update
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
                        api_app,
                        host="0.0.0.0",
                        port=8899,
                        log_level="warning",
                        # None = 不向浏览器发 RFC ping；避免弱网下 1011 keepalive ping timeout
                        # 刷屏 asyncio ERROR。/ws/live 自带 JSON heartbeat 保活。
                        ws_ping_interval=None,
                        ws_ping_timeout=None,
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

    def _resolve_strategy(self, strategy_id: str) -> Strategy:
        """Return strategy instance for ``strategy_id`` (fallback: primary)."""
        s = self._strategy_registry.get(strategy_id)
        if s is not None:
            return s
        logger.warning("Unknown strategy_id %s — using primary strategy", strategy_id)
        return self.strategy

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
                    f"entering pending pool (10s delay)...[/cyan]"
                )
                logger.info(
                    "📡 %d 个信号进入待执行池 (10s延迟)...", len(pending),
                )
                for s in pending:
                    _sig_sid = signal_strategy_id(s)
                    self.console.print(
                        f"  [dim]• [{_sig_sid}] {s.symbol} surge={s.surge_ratio:.1f}x "
                        f"signal_price={s.price:.6f}[/dim]"
                    )
                    logger.info(
                        "  • [%s] %s surge=%.1fx signal_price=%.6f",
                        _sig_sid, s.symbol, s.surge_ratio, s.price,
                    )

                # Wait 10 seconds before executing entries
                await asyncio.sleep(10)

                # ── Batch pre-filter: check positions ONCE ────────
                # Avoids N separate get_position_risk() calls and
                # prevents race conditions under high signal volume.
                try:
                    all_pos = await self.client.get_position_risk()
                    open_pos = [p for p in all_pos if float(p.position_amt) != 0]
                    open_symbols = {p.symbol for p in open_pos}
                    open_count = len(open_pos)
                except Exception as e:
                    logger.warning("Failed to pre-check positions: %s", e)
                    open_symbols = set()
                    open_count = 0

                # Sort by surge ratio descending — strongest signals first
                pending.sort(key=lambda s: s.surge_ratio, reverse=True)

                # Filter out already-in-position and record rejections
                now_ts = utc_now()
                filtered = []
                for s in pending:
                    if s.symbol in open_symbols:
                        logger.info(
                            "⏭️ [%s] Skip %s: already in position",
                            signal_strategy_id(s), s.symbol,
                        )
                        self.store.save_signal_event(SignalEvent(
                            timestamp=now_ts.isoformat(), symbol=s.symbol,
                            surge_ratio=s.surge_ratio, price=str(s.price),
                            accepted=False, reject_reason="already in position",
                            strategy_id=signal_strategy_id(s),
                        ))
                    else:
                        filtered.append(s)

                # Cap at available position slots
                available_slots = max(0, self.config.max_positions - open_count)
                if available_slots == 0:
                    for s in filtered:
                        reason = (f"max positions reached ({open_count} exchange"
                                  f" ≥ {self.config.max_positions})")
                        logger.info(
                            "⏭️ [%s] Skip %s: %s",
                            signal_strategy_id(s), s.symbol, reason,
                        )
                        self.store.save_signal_event(SignalEvent(
                            timestamp=now_ts.isoformat(), symbol=s.symbol,
                            surge_ratio=s.surge_ratio, price=str(s.price),
                            accepted=False, reject_reason=reason,
                            strategy_id=signal_strategy_id(s),
                        ))
                    filtered = []
                else:
                    # Record overflow signals as rejected
                    for s in filtered[available_slots:]:
                        reason = (f"max positions reached ({open_count} exchange"
                                  f" + {available_slots} slots used"
                                  f" ≥ {self.config.max_positions})")
                        logger.info(
                            "⏭️ [%s] Skip %s: %s",
                            signal_strategy_id(s), s.symbol, reason,
                        )
                        self.store.save_signal_event(SignalEvent(
                            timestamp=now_ts.isoformat(), symbol=s.symbol,
                            surge_ratio=s.surge_ratio, price=str(s.price),
                            accepted=False, reject_reason=reason,
                            strategy_id=signal_strategy_id(s),
                        ))
                    filtered = filtered[:available_slots]

                if filtered:
                    logger.info(
                        "📡 Pre-filter: %d/%d signals → %d to execute "
                        "(open=%d, slots=%d)",
                        len(pending), len(pending), len(filtered),
                        open_count, available_slots,
                    )

                # Execute filtered signals ONE BY ONE
                live_pending: set[str] = set()  # track in-flight symbols
                for i, s in enumerate(filtered):
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
                            strategy_id=signal_strategy_id(s),
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
                        if i < len(filtered) - 1:
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
        sid = signal_strategy_id(signal)
        strat = self._resolve_strategy(sid)

        # ── Guard checks (exchange positions + in-flight orders) ──
        try:
            all_pos = await self.client.get_position_risk()
            open_pos = [p for p in all_pos if float(p.position_amt) != 0]
            open_symbols = {p.symbol for p in open_pos}

            # Combine exchange positions with pending (in-flight) symbols
            combined_symbols = open_symbols | pending
            combined_count = len(open_symbols | pending)

            if symbol in combined_symbols:
                logger.info(
                    "⏭️ [%s] Skip %s: already in position (exchange/pending)",
                    sid, symbol,
                )
                self.store.save_signal_event(SignalEvent(
                    timestamp=now.isoformat(), symbol=symbol,
                    surge_ratio=signal.surge_ratio, price=str(signal.price),
                    accepted=False, reject_reason="already in position",
                    strategy_id=sid,
                ))
                return False
            if combined_count >= self.config.max_positions:
                reason = (f"max positions reached ({len(open_pos)} exchange"
                          f" + {len(pending)} pending ≥ {self.config.max_positions})")
                logger.info("⏭️ [%s] Skip %s: %s", sid, symbol, reason)
                self.store.save_signal_event(SignalEvent(
                    timestamp=now.isoformat(), symbol=symbol,
                    surge_ratio=signal.surge_ratio, price=str(signal.price),
                    accepted=False, reject_reason=reason,
                    strategy_id=sid,
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
            logger.warning("[%s] Failed to get price for %s: %s", sid, symbol, e)
            self.store.save_signal_event(SignalEvent(
                timestamp=now.isoformat(), symbol=symbol,
                surge_ratio=signal.surge_ratio, price=str(signal.price),
                accepted=False, reject_reason=f"price fetch failed: {e}",
                strategy_id=sid,
            ))
            return False

        signal_price = Decimal(str(signal.price))

        # ── Strategy entry filter (risk filters + entry params) ─
        decision = await strat.filter_entry(
            client=self.client,
            signal=signal,
            entry_price=entry_price,
            signal_price=signal_price,
            now=now,
            config=self.config,
        )
        if not decision.should_enter:
            self.store.save_signal_event(SignalEvent(
                timestamp=now.isoformat(),
                symbol=symbol,
                surge_ratio=signal.surge_ratio,
                price=str(entry_price),
                accepted=False,
                reject_reason=decision.reject_reason,
                strategy_id=sid,
            ))
            self.console.print(
                f"  [yellow]FILTERED[/yellow] {symbol}: {decision.reject_reason}"
            )
            return False

        # ── Daily loss limit check ───────────────────────────────
        if self.config.daily_loss_limit_usdt > 0:
            try:
                daily_pnl = await self.client.get_daily_realized_pnl()
                logger.info("[%s] 📊 今日已实现盈亏: %s USDT", sid, daily_pnl)
                if daily_pnl <= -self.config.daily_loss_limit_usdt:
                    reason = f"daily loss limit ({daily_pnl} ≤ -{self.config.daily_loss_limit_usdt})"
                    self.console.print(
                        f"  [red]🛑 每日亏损限额已达 ({daily_pnl} USDT ≤ -{self.config.daily_loss_limit_usdt})"
                        f" — 停止开新仓[/red]"
                    )
                    logger.warning(
                        "[%s] 每日亏损限额触发: %s USDT, 限额 %s USDT",
                        sid, daily_pnl, self.config.daily_loss_limit_usdt,
                    )
                    self.store.save_signal_event(SignalEvent(
                        timestamp=now.isoformat(), symbol=symbol,
                        surge_ratio=signal.surge_ratio, price=str(entry_price),
                        accepted=False, reject_reason=reason,
                        strategy_id=sid,
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
                logger.info("[%s] 百分比保证金: %.2f USDT (%.1f%% of %.2f)",
                            sid, margin, self.config.margin_pct, available)
            except Exception as e:
                logger.warning("[%s] 获取余额失败, 降级为固定保证金: %s", sid, e)
                margin = self.config.live_fixed_margin_usdt
        else:
            margin = self.config.live_fixed_margin_usdt
            logger.info("[%s] 固定保证金: %s USDT", sid, margin)
        quantity = margin * self.config.leverage / entry_price

        # ── Place live order ────────────────────────────────────
        try:
            order_result = await self.live_executor.open_position(
                symbol=symbol,
                price=entry_price,
                quantity=quantity,
                side=decision.side,
                tp_pct=decision.tp_pct,
                sl_pct=decision.sl_pct,
            )
            if order_result.get("entry_order"):
                self.store.save_signal_event(SignalEvent(
                    timestamp=now.isoformat(),
                    symbol=symbol,
                    surge_ratio=signal.surge_ratio,
                    price=str(entry_price),
                    accepted=True,
                    strategy_id=sid,
                ))
                entry_side = decision.side  # already "SHORT" or "LONG"
                if self.store:
                    self.store.upsert_position_attribution(symbol, entry_side, sid)
                self.console.print(
                    f"  [green]🟢 LIVE ENTRY[/green] {symbol} {entry_side} @ {entry_price} "
                    f"(qty: {quantity:.4f})"
                )
                # Track position in live monitor
                if order_result.get("deferred_tp_sl"):
                    self.live_monitor.track(
                        symbol=symbol,
                        entry_order_id=order_result["entry_order"].order_id,
                        side=entry_side,
                        quantity=str(quantity),
                        deferred_tp_sl=order_result["deferred_tp_sl"],
                        tp_pct=decision.tp_pct,
                        sl_pct=decision.sl_pct,
                        strategy_id=sid,
                    )
                # Telegram notification
                if self.notifier:
                    await self.notifier.notify_entry_placed(
                        symbol=symbol, side=entry_side,
                        price=str(entry_price), qty=f"{quantity:.4f}",
                        margin=str(margin),
                        order_id=str(order_result["entry_order"].order_id),
                    )
            elif order_result.get("error"):
                self.console.print(
                    f"  [red]LIVE ORDER FAILED[/red] {symbol}: {order_result['error']}"
                )
        except Exception as e:
            logger.error("[%s] 实盘下单失败 %s: %s", sid, symbol, e, exc_info=True)
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

                # Count today's live trades — push date filter to SQL; parse UTC properly
                today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                today_live = self.store.get_live_trades(limit=9999, since_date=today_utc)
                today_trades = sum(
                    1 for t in today_live
                    if t.timestamp
                    and datetime.fromisoformat(
                        t.timestamp.replace("Z", "+00:00")
                    ).astimezone(timezone.utc).strftime("%Y-%m-%d") == today_utc
                )

                # Save today's balance snapshot & get yesterday's
                total_bal = bal['total_balance']
                self.store.save_balance_snapshot(
                    today_utc, total_bal, bal['unrealized_pnl'],
                )
                yesterday_bal = self.store.get_yesterday_balance(today_utc)

                await self.notifier.notify_daily_summary(
                    total_balance=f"{total_bal:,.2f}",
                    daily_pnl=f"{daily_pnl:+,.2f}",
                    unrealized_pnl=f"{bal['unrealized_pnl']:+,.2f}",
                    open_positions=open_count,
                    trades_today=today_trades,
                    yesterday_balance=f"{yesterday_bal:,.2f}" if yesterday_bal is not None else None,
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
        self.console.print("[bold cyan]🚀 Trader — Rolling R24 | 🔴 实盘模式 (LIVE)[/bold cyan]")
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
        rc = self.rolling_config
        if rc:
            self.console.print(f"  TP:           {rc.tp_initial * 100:.0f}%")
            self.console.print(f"  SL:           {rc.sl_threshold * 100:.0f}%")
            self.console.print(f"  Max hold:     {rc.max_hold_days * 24}h")
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
