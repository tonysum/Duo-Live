"""Telegram bot command handler for remote monitoring and control.

Polls for incoming messages via getUpdates long-polling and dispatches
commands. Runs as an asyncio task alongside the trading bot.

Supported commands:
    /status     — Account balance, open positions, today's P&L
    /positions  — Detailed open positions list
    /trades     — Recent trade history
    /close <SYM>— Force close a position
    /help       — Show available commands

Security: Only responds to messages from the configured TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_TG_API = "https://api.telegram.org/bot{token}"


class TelegramBot:
    """Telegram command handler with long-polling."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        trader=None,  # LiveTrader reference
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.trader = trader
        self._base_url = _TG_API.format(token=bot_token)
        self._offset = 0  # getUpdates offset
        self._running = False
        self.enabled = bool(bot_token and chat_id)

    async def run_forever(self):
        """Long-poll for Telegram updates and dispatch commands."""
        if not self.enabled:
            logger.info("📵 Telegram Bot 未配置, 跳过命令监听")
            return

        self._running = True
        logger.info("🤖 Telegram Bot 命令监听已启动")
        backoff = 5  # seconds, grows on consecutive errors
        min_poll_interval = 1.0  # Minimum 1 second between polls to avoid rate limit

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            while self._running:
                poll_start = asyncio.get_event_loop().time()
                try:
                    resp = await client.get(
                        f"{self._base_url}/getUpdates",
                        params={
                            "offset": self._offset,
                            "timeout": 25,  # Reduced from 30 to 25
                            "allowed_updates": '["message"]',
                        },
                    )
                    data = resp.json()
                    if not data.get("ok"):
                        # Respect retry_after from 429 responses
                        retry_after = (
                            data.get("parameters", {}).get("retry_after")
                            or backoff
                        )
                        logger.warning(
                            "Telegram getUpdates 失败: %s (等待 %ss)",
                            data, retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        backoff = min(backoff * 2, 60)  # exponential backoff
                        continue

                    # Success — reset backoff
                    backoff = 5
                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        await self._handle_update(update, client)

                except asyncio.CancelledError:
                    break
                except httpx.TimeoutException:
                    # Normal for long-polling, but add small delay to avoid hammering
                    await asyncio.sleep(0.5)
                    continue
                except Exception as e:
                    logger.warning("Telegram Bot 异常: %s", e)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                
                # Ensure minimum interval between polls to avoid rate limit
                poll_duration = asyncio.get_event_loop().time() - poll_start
                if poll_duration < min_poll_interval:
                    await asyncio.sleep(min_poll_interval - poll_duration)

    def stop(self):
        self._running = False

    async def _handle_update(self, update: dict, client: httpx.AsyncClient):
        """Process a single Telegram update."""
        msg = update.get("message", {})
        chat = msg.get("chat", {})
        text = msg.get("text", "").strip()

        # Security: only respond to configured chat
        if str(chat.get("id")) != str(self.chat_id):
            return

        if not text.startswith("/"):
            return

        parts = text.split()
        cmd = parts[0].lower().split("@")[0]  # handle /cmd@botname
        args = parts[1:]

        handlers = {
            "/status": self._cmd_status,
            "/positions": self._cmd_positions,
            "/pos": self._cmd_positions,
            "/trades": self._cmd_trades,
            "/close": self._cmd_close,
            "/cancel": self._cmd_cancel,
            "/help": self._cmd_help,
            "/start": self._cmd_help,
        }

        handler = handlers.get(cmd)
        if handler:
            try:
                response = await handler(args)
            except Exception as e:
                response = f"❌ 命令执行失败: {e}"
                logger.error("Telegram 命令 %s 失败: %s", cmd, e, exc_info=True)
        else:
            response = f"❓ 未知命令: {cmd}\n输入 /help 查看可用命令"

        await self._reply(client, response)

    async def _reply(self, client: httpx.AsyncClient, text: str):
        """Send a reply message."""
        try:
            await client.post(
                f"{self._base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
        except Exception as e:
            logger.warning("Telegram 回复失败: %s", e)

    # ------------------------------------------------------------------
    # Command Implementations
    # ------------------------------------------------------------------

    async def _cmd_help(self, args: list[str]) -> str:
        return (
            "🤖 <b>可用命令</b>  (🔴 实盘)\n\n"
            "/status — 账户概览\n"
            "/positions — 持仓详情\n"
            "/trades — 最近交易\n"
            "/close &lt;SYMBOL&gt; — 强制平仓\n"
            "/cancel &lt;SYMBOL&gt; — 撤销该币所有挂单 (不平仓)\n"
            "/help — 显示帮助"
        )

    async def _cmd_status(self, args: list[str]) -> str:
        """Account overview: balance, P&L, position count."""
        if not self.trader:
            return "⚠️ 交易系统未连接"

        try:
            lines = ["📊 <b>账户状态</b>  (🔴 实盘)\n"]

            client = self.trader.client
            bal = await client.get_account_balance()
            daily_pnl = await client.get_daily_realized_pnl()
            all_pos = await client.get_position_risk()
            open_count = sum(1 for p in all_pos if float(p.position_amt) != 0)

            total = bal["total_balance"]
            avail = bal["available_balance"]
            unreal = bal["unrealized_pnl"]

            pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
            unreal_emoji = "🟢" if unreal >= 0 else "🔴"

            lines.append(
                f"💰 总余额: <code>{total:,.2f}</code> USDT\n"
                f"💵 可用余额: <code>{avail:,.2f}</code> USDT\n"
                f"{pnl_emoji} 今日盈亏: <code>{daily_pnl:+,.2f}</code> USDT\n"
                f"{unreal_emoji} 未实现盈亏: <code>{unreal:+,.2f}</code> USDT\n"
                f"📌 持仓数: {open_count}"
            )

            lines.append(f"\n⏱️ {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ 获取状态失败: {e}"

    async def _cmd_positions(self, args: list[str]) -> str:
        """List open positions with details."""
        if not self.trader:
            return "⚠️ 交易系统未连接"

        try:
            all_pos = await self.trader.client.get_position_risk()
            open_pos = [p for p in all_pos if float(p.position_amt) != 0]

            if not open_pos:
                return "📭 当前无持仓  (🔴 实盘)"

            lines = ["📋 <b>当前持仓</b>  (🔴 实盘)\n"]
            for p in open_pos:
                amt = float(p.position_amt)
                side = "LONG 📈" if amt > 0 else "SHORT 📉"
                entry = float(p.entry_price)
                unreal = float(p.unrealized_profit)
                pnl_emoji = "🟢" if unreal >= 0 else "🔴"

                lines.append(
                    f"<b>{p.symbol}</b> {side}\n"
                    f"  入场: <code>{entry:,.4f}</code>\n"
                    f"  数量: <code>{abs(amt)}</code>\n"
                    f"  {pnl_emoji} 盈亏: <code>{unreal:+,.2f}</code> USDT\n"
                )

            return "\n".join(lines)
        except Exception as e:
            return f"❌ 获取持仓失败: {e}"

    async def _cmd_trades(self, args: list[str]) -> str:
        """Show recent trades with PnL and prices. (K)"""
        if not self.trader or not self.trader.store:
            return "⚠️ 交易记录不可用"

        try:
            trades = self.trader.store.get_live_trades(limit=10)

            if not trades:
                return "📭 暂无实盘交易记录"

            lines = ["📜 <b>最近实盘交易</b>  (🔴 实盘)\n"]

            for t in trades:
                event_emoji = {
                    "entry": "🔹", "tp": "🎯", "sl": "🛑",
                    "timeout": "⏰", "force": "⚡",
                }.get(t.event, "•")

                # PnL line (K: show realized profit/loss)
                pnl_str = ""
                if t.pnl_usdt is not None and t.event != "entry":
                    sign = "+" if t.pnl_usdt >= 0 else ""
                    pnl_emoji = "💰" if t.pnl_usdt >= 0 else "📉"
                    pnl_str = f"  {pnl_emoji} PnL: <code>{sign}{t.pnl_usdt:.2f}</code> USDT\n"

                # Price line
                price_str = ""
                if t.entry_price and t.exit_price:
                    price_str = f"  价格: <code>{t.entry_price}</code> → <code>{t.exit_price}</code>\n"
                elif t.entry_price:
                    price_str = f"  入场: <code>{t.entry_price}</code>\n"

                # Hold duration
                hold_str = ""
                if t.entry_time and t.exit_time:
                    try:
                        dt_in = datetime.fromisoformat(t.entry_time.replace("Z", "+00:00"))
                        dt_out = datetime.fromisoformat(t.exit_time.replace("Z", "+00:00"))
                        h = (dt_out - dt_in).total_seconds() / 3600
                        hold_str = f"  持仓: {h:.1f}h\n"
                    except Exception:
                        pass

                ts_str = (t.timestamp or "")[:16].replace("T", " ")
                lines.append(
                    f"{event_emoji} <b>{t.symbol}</b> {t.side} — {t.event} ({ts_str})\n"
                    f"{price_str}{pnl_str}{hold_str}"
                )

            return "\n".join(lines)
        except Exception as e:
            return f"❌ 获取交易记录失败: {e}"

    async def _cmd_close(self, args: list[str]) -> str:
        """Force close a position: /close BTCUSDT"""

        if not args:
            return "⚠️ 用法: /close BTCUSDT"

        symbol = args[0].upper()

        # Check if position exists in live monitor
        pos = self.trader.live_monitor._positions.get(symbol)
        if not pos:
            # Also check exchange positions
            try:
                all_pos = await self.trader.client.get_position_risk(symbol)
                open_pos = [p for p in all_pos if float(p.position_amt) != 0]
                if not open_pos:
                    return f"⚠️ 未找到 {symbol} 持仓"
            except Exception:
                return f"⚠️ 未找到 {symbol} 持仓"

        # Force close via live monitor
        if pos:
            try:
                await self.trader.live_monitor._force_close(pos)
                return f"✅ 已发送 {symbol} 限价平仓指令"
            except Exception as e:
                return f"❌ 平仓失败: {e}"

        # Direct limit close if not in monitor
        try:
            all_pos = await self.trader.client.get_position_risk(symbol)
            for p in all_pos:
                amt = float(p.position_amt)
                if amt == 0:
                    continue
                close_side = "SELL" if amt > 0 else "BUY"
                is_hedge = await self.trader.client.get_position_mode()
                ps = ("LONG" if amt > 0 else "SHORT") if is_hedge else "BOTH"
                await self.trader.client.place_limit_close(
                    symbol=symbol,
                    side=close_side,
                    quantity=str(abs(amt)),
                    position_side=ps,
                )
                return f"✅ 已发送 {symbol} 限价平仓指令 ({close_side} {abs(amt)})"
        except Exception as e:
            return f"❌ 平仓失败: {e}"

        return f"⚠️ 未找到 {symbol} 可平仓仓位"

    async def _cmd_cancel(self, args: list[str]) -> str:
        """Cancel all algo orders (TP/SL) for a symbol without closing the position. (L)"""
        if not args:
            return "⚠️ 用法: /cancel BTCUSDT"

        symbol = args[0].upper()
        try:
            algo_orders = await self.trader.client.get_open_algo_orders()
            target = [o for o in algo_orders if o.symbol == symbol]
            if not target:
                return f"📭 {symbol} 无挂单可撤销"

            cancelled = 0
            failed = 0
            for o in target:
                try:
                    await self.trader.client.cancel_algo_order(symbol, algo_id=o.algo_id)
                    cancelled += 1
                except Exception as e:
                    logger.warning("撤销挂单失败 %s algoId=%s: %s", symbol, o.algo_id, e)
                    failed += 1

            parts = [f"✅ {symbol} 已撤销 {cancelled} 张挂单"]
            if failed:
                parts.append(f"❌ {failed} 张失败")
            return "\n".join(parts)
        except Exception as e:
            return f"❌ 撤销失败: {e}"
