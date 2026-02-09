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
        paper_trader=None,  # PaperTrader reference (set after init)
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.trader = paper_trader
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

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            while self._running:
                try:
                    resp = await client.get(
                        f"{self._base_url}/getUpdates",
                        params={
                            "offset": self._offset,
                            "timeout": 30,
                            "allowed_updates": '["message"]',
                        },
                    )
                    data = resp.json()
                    if not data.get("ok"):
                        logger.warning("Telegram getUpdates 失败: %s", data)
                        await asyncio.sleep(5)
                        continue

                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        await self._handle_update(update, client)

                except asyncio.CancelledError:
                    break
                except httpx.TimeoutException:
                    continue  # Normal for long-polling
                except Exception as e:
                    logger.warning("Telegram Bot 异常: %s", e)
                    await asyncio.sleep(5)

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

    @property
    def _is_live(self) -> bool:
        return bool(self.trader and self.trader.live_monitor)

    @property
    def _mode_label(self) -> str:
        return "🔴 实盘" if self._is_live else "📝 模拟盘"

    async def _cmd_help(self, args: list[str]) -> str:
        return (
            f"🤖 <b>可用命令</b>  ({self._mode_label})\n\n"
            "/status — 账户概览\n"
            "/positions — 持仓详情\n"
            "/trades — 最近交易\n"
            "/close &lt;SYMBOL&gt; — 强制平仓 (实盘)\n"
            "/help — 显示帮助"
        )

    async def _cmd_status(self, args: list[str]) -> str:
        """Account overview: balance, P&L, position count."""
        if not self.trader:
            return "⚠️ 交易系统未连接"

        try:
            lines = [f"📊 <b>账户状态</b>  ({self._mode_label})\n"]

            if self._is_live:
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
            else:
                # Paper mode — show paper stats
                store = self.trader.store
                positions = store.get_open_positions() if store else []
                trades = store.get_trades(limit=9999) if store else []

                from datetime import datetime, timezone
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                today_trades = [t for t in trades if t.exit_time and t.exit_time.startswith(today)]
                today_pnl = sum(float(t.pnl) for t in today_trades)

                pnl_emoji = "📈" if today_pnl >= 0 else "📉"

                lines.append(
                    f"📌 模拟持仓: {len(positions)}\n"
                    f"📊 总交易数: {len(trades)}\n"
                    f"{pnl_emoji} 今日盈亏: <code>{today_pnl:+,.2f}</code> USDT\n"
                    f"📜 今日成交: {len(today_trades)}"
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
            if self._is_live:
                all_pos = await self.trader.client.get_position_risk()
                open_pos = [p for p in all_pos if float(p.position_amt) != 0]

                if not open_pos:
                    return f"📭 当前无持仓  ({self._mode_label})"

                lines = [f"📋 <b>当前持仓</b>  ({self._mode_label})\n"]
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
            else:
                # Paper mode
                store = self.trader.store
                positions = store.get_open_positions() if store else []

                if not positions:
                    return f"📭 当前无模拟持仓  ({self._mode_label})"

                lines = [f"📋 <b>模拟持仓</b>  ({self._mode_label})\n"]
                for p in positions:
                    side_emoji = "📉" if p.side == "short" else "📈"
                    lines.append(
                        f"<b>{p.symbol}</b> {p.side.upper()} {side_emoji}\n"
                        f"  入场: <code>{p.entry_price}</code>\n"
                        f"  数量: <code>{p.size}</code>\n"
                        f"  TP: {p.tp_pct}% | 强弱: {p.strength}\n"
                    )

            return "\n".join(lines)
        except Exception as e:
            return f"❌ 获取持仓失败: {e}"

    async def _cmd_trades(self, args: list[str]) -> str:
        """Show recent trades."""
        if not self.trader or not self.trader.store:
            return "⚠️ 交易记录不可用"

        try:
            if self._is_live:
                trades = self.trader.store.get_live_trades(limit=10)
                label = "实盘交易"
            else:
                trades = self.trader.store.get_trades(limit=10)
                label = "模拟交易"

            if not trades:
                return f"📭 暂无{label}记录"

            lines = [f"📜 <b>最近{label}</b>  ({self._mode_label})\n"]

            if self._is_live:
                for t in trades:
                    event_emoji = {
                        "entry": "🔹", "tp": "🎯", "sl": "🛑",
                        "timeout": "⏰",
                    }.get(t.event, "•")
                    lines.append(
                        f"{event_emoji} {t.symbol} {t.side} — {t.event}\n"
                        f"  {t.timestamp or '?'}\n"
                    )
            else:
                for t in trades:
                    pnl = float(t.pnl)
                    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                    lines.append(
                        f"{pnl_emoji} {t.symbol} {t.side}\n"
                        f"  {t.exit_reason} | PnL: <code>{pnl:+,.2f}</code>\n"
                        f"  {t.exit_time or '?'}\n"
                    )

            return "\n".join(lines)
        except Exception as e:
            return f"❌ 获取交易记录失败: {e}"

    async def _cmd_close(self, args: list[str]) -> str:
        """Force close a position: /close BTCUSDT"""
        if not self._is_live:
            return "⚠️ 平仓仅限实盘模式\n模拟盘持仓会按策略自动退出"

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
                return f"✅ 已发送 {symbol} 市价平仓指令"
            except Exception as e:
                return f"❌ 平仓失败: {e}"

        # Direct market close if not in monitor
        try:
            all_pos = await self.trader.client.get_position_risk(symbol)
            for p in all_pos:
                amt = float(p.position_amt)
                if amt == 0:
                    continue
                close_side = "SELL" if amt > 0 else "BUY"
                is_hedge = await self.trader.client.get_position_mode()
                ps = ("LONG" if amt > 0 else "SHORT") if is_hedge else "BOTH"
                await self.trader.client.place_market_close(
                    symbol=symbol,
                    side=close_side,
                    quantity=str(abs(amt)),
                    position_side=ps,
                )
                return f"✅ 已发送 {symbol} 市价平仓指令 ({close_side} {abs(amt)})"
        except Exception as e:
            return f"❌ 平仓失败: {e}"

        return f"⚠️ 未找到 {symbol} 可平仓仓位"

