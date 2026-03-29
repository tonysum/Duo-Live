"""Binance Futures WebSocket user data stream.

Listens for real-time account events:
  - ORDER_TRADE_UPDATE  (order fills, TP/SL triggers)
  - ACCOUNT_UPDATE      (position changes)

Binance docs:
  - listenKey valid 60min, keepalive via PUT every 30min
  - WS connection max 24h, auto-reconnect required
  - Use `E` field for event ordering
  - `x` = execution type (NEW/TRADE/CANCELED/EXPIRED)
  - `X` = order status  (NEW/PARTIALLY_FILLED/FILLED/CANCELED)

Runs alongside the REST polling loop as a fast-path notification layer.
REST polling remains as fallback for reliability.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Coroutine, Optional

import websockets
import websockets.exceptions

from .binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)

WS_BASE = "wss://fstream.binance.com/ws/"

# Max WS connection duration per Binance spec
MAX_CONNECTION_HOURS = 23  # reconnect before 24h limit

# After this many consecutive reconnects without a clean session, alert via Telegram
_DISCONNECT_ALERT_THRESHOLD = 3

# Callback type: async def handler(event: dict) -> None
EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class _ListenKeyExpired(Exception):
    """Sentinel raised when Binance signals listenKey expiry.

    Caught specifically in the inner message loop so we can break out and
    reconnect immediately — without triggering the 5s/10s sleep in the
    outer exception handlers.
    """


class BinanceUserStream:
    """WebSocket listener for Binance Futures user data stream.

    Usage:
        stream = BinanceUserStream(client)
        stream.on_order_update = my_handler
        await stream.run_forever()
    """

    def __init__(
        self,
        client: BinanceFuturesClient,
        keepalive_interval: int = 30 * 60,  # 30 minutes (listenKey valid 60min)
        notifier=None,  # TelegramNotifier (optional) for disconnect alerts
    ):
        self.client = client
        self.keepalive_interval = keepalive_interval
        self.notifier = notifier
        self._listen_key: Optional[str] = None
        self._running = False
        self._consecutive_disconnects: int = 0

        # Event callbacks (async)
        self.on_order_update: Optional[EventCallback] = None
        self.on_account_update: Optional[EventCallback] = None

    async def run_forever(self):
        """Connect and listen. Auto-reconnect on disconnect or 24h limit."""
        self._running = True
        while self._running:
            try:
                # Get fresh listenKey
                self._listen_key = await self.client.create_listen_key()
                ws_url = f"{WS_BASE}{self._listen_key}"
                logger.info("🔌 WebSocket 连接中: %s...", ws_url[:60])

                connect_time = time.monotonic()
                max_duration = MAX_CONNECTION_HOURS * 3600

                async with websockets.connect(
                    ws_url,
                    ping_interval=30,   # send ping every 30s
                    ping_timeout=40,    # wait for pong (10s 在弱网下易触发 keepalive timeout)
                    close_timeout=5,
                ) as ws:
                    logger.info("🔌 WebSocket 已连接")
                    self._consecutive_disconnects = 0  # reset on successful connect

                    # Start keepalive task
                    keepalive_task = asyncio.create_task(
                        self._keepalive_loop()
                    )

                    try:
                        async for raw_msg in ws:
                            if not self._running:
                                break

                            # Check 24h connection limit
                            elapsed = time.monotonic() - connect_time
                            if elapsed > max_duration:
                                logger.info(
                                    "🔌 已连接 %dh, 达到24h限制前主动重连",
                                    int(elapsed / 3600),
                                )
                                break

                            try:
                                data = json.loads(raw_msg)
                                await self._dispatch(data)
                            except _ListenKeyExpired:
                                # Immediate reconnect — don't fall into outer sleeps
                                break
                            except json.JSONDecodeError:
                                logger.warning("WS: 无法解析消息: %s", raw_msg[:200])
                    finally:
                        keepalive_task.cancel()
                        try:
                            await keepalive_task
                        except asyncio.CancelledError:
                            pass

            except (
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK,
                ConnectionError,
                OSError,
            ) as e:
                if not self._running:
                    break
                self._consecutive_disconnects += 1
                logger.warning(
                    "🔌 WebSocket 断开 (第%d次): %s, 5s 后重连...",
                    self._consecutive_disconnects, e,
                )
                # F: alert via Telegram after repeated disconnects
                if (
                    self._consecutive_disconnects >= _DISCONNECT_ALERT_THRESHOLD
                    and self.notifier
                ):
                    try:
                        await self.notifier.send(
                            f"🔌 <b>WebSocket 持续断线</b>\n"
                            f"  已连续断开 {self._consecutive_disconnects} 次\n"
                            f"  REST 轮询延迟可达 120s，请检查网络"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break

            except Exception as e:
                if not self._running:
                    break
                self._consecutive_disconnects += 1
                logger.error("🔌 WebSocket 异常: %s, 10s 后重连...", e)
                await asyncio.sleep(10)

        # Cleanup
        if self._listen_key:
            try:
                await self.client.close_listen_key()
            except Exception:
                pass
        logger.info("🔌 WebSocket 已关闭")

    def stop(self):
        self._running = False

    async def _keepalive_loop(self):
        """Send keepalive every 30 minutes to prevent listenKey expiry (valid 60min)."""
        while self._running:
            await asyncio.sleep(self.keepalive_interval)
            try:
                await self.client.keepalive_listen_key()
                logger.debug("🔌 listenKey keepalive sent")
            except Exception as e:
                logger.warning("🔌 listenKey keepalive 失败: %s", e)

    async def _dispatch(self, data: dict[str, Any]):
        """Dispatch event to appropriate callback."""
        event_type = data.get("e")

        if event_type == "ORDER_TRADE_UPDATE":
            order_data = data.get("o", {})
            logger.debug(
                "📨 WS ORDER: %s %s %s x=%s X=%s rp=%s",
                order_data.get("s"),           # symbol
                order_data.get("ps"),           # position side
                order_data.get("ot"),           # original order type
                order_data.get("x"),            # execution type
                order_data.get("X"),            # order status
                order_data.get("rp"),           # realized PnL
            )
            if self.on_order_update:
                await self.on_order_update(data)

        elif event_type == "ACCOUNT_UPDATE":
            logger.debug("📨 WS ACCOUNT_UPDATE")
            if self.on_account_update:
                await self.on_account_update(data)

        elif event_type == "listenKeyExpired":
            logger.warning("🔌 listenKey 已过期, 立即重连")
            # Raise sentinel — caught in the inner for-loop to break immediately
            # without triggering the outer 5s/10s sleep exception handlers
            raise _ListenKeyExpired()
