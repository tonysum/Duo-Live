"""Telegram notification service for live trading events.

Configure via environment variables:
    TELEGRAM_BOT_TOKEN  — Bot token from @BotFather
    TELEGRAM_CHAT_ID    — Your Telegram user/group chat ID

If not configured, notifications are silently skipped (no error).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Telegram API base
_TG_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Send trading notifications via Telegram bot."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)

        if not self.enabled:
            logger.info("📵 Telegram 通知未配置 (跳过推送)")
        else:
            logger.info("📱 Telegram 通知已启用")

    async def send(self, message: str) -> bool:
        """Send a message. Returns True if successful."""
        if not self.enabled:
            return False

        url = _TG_API.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
                else:
                    logger.warning("Telegram 发送失败: %s %s", resp.status_code, resp.text)
                    return False
        except Exception as e:
            logger.warning("Telegram 发送异常: %s", e)
            return False

    def send_sync(self, message: str) -> bool:
        """Synchronous wrapper for send()."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.send(message))
                return True
            else:
                return loop.run_until_complete(self.send(message))
        except RuntimeError:
            return asyncio.run(self.send(message))

    # ── Convenience methods for trading events ────────────────────────

    async def notify_entry_placed(
        self, symbol: str, side: str, price: str, qty: str,
        margin: str = "", order_id: str = "",
    ):
        """Entry order submitted."""
        await self.send(
            f"📋 <b>入场单已提交</b>\n"
            f"  {symbol} {side}\n"
            f"  价格: {price}\n"
            f"  数量: {qty}\n"
            f"  保证金: {margin} USDT\n"
            f"  orderId: {order_id}"
        )

    async def notify_entry_filled(
        self, symbol: str, side: str, price: str,
    ):
        """Entry order filled."""
        await self.send(
            f"✅ <b>入场成交</b>\n"
            f"  {symbol} {side} @ {price}\n"
            f"  TP/SL 自动挂出中..."
        )

    async def notify_tp_sl_placed(
        self, symbol: str, tp_price: str, sl_price: str,
    ):
        """TP/SL orders placed after entry fill."""
        await self.send(
            f"🎯 <b>TP/SL 已挂出</b>\n"
            f"  {symbol}\n"
            f"  止盈: {tp_price}\n"
            f"  止损: {sl_price}"
        )

    async def notify_tp_triggered(self, symbol: str, side: str):
        """Take-profit triggered."""
        await self.send(
            f"🎯 <b>止盈触发</b> 💰\n"
            f"  {symbol} {side}"
        )

    async def notify_sl_triggered(self, symbol: str, side: str):
        """Stop-loss triggered."""
        await self.send(
            f"🛑 <b>止损触发</b>\n"
            f"  {symbol} {side}"
        )

    async def notify_timeout_close(self, symbol: str, hours: int):
        """Max hold time exceeded, market close."""
        await self.send(
            f"⏰ <b>超时平仓</b>\n"
            f"  {symbol} 持仓 {hours}h 已市价平仓"
        )

    async def notify_daily_loss_limit(self, daily_pnl: str, limit: str):
        """Daily loss limit reached."""
        await self.send(
            f"🚨 <b>每日亏损限额触发</b>\n"
            f"  今日盈亏: {daily_pnl} USDT\n"
            f"  限额: -{limit} USDT\n"
            f"  已停止开新仓"
        )

    async def notify_signal(self, symbol: str, surge_ratio: str, price: str, accepted: bool, reason: str = ""):
        """Signal detected (accepted or filtered)."""
        if accepted:
            await self.send(
                f"📡 <b>信号触发</b>\n"
                f"  {symbol} 暴涨比 {surge_ratio}\n"
                f"  价格: {price}"
            )
        else:
            await self.send(
                f"📡 <b>信号过滤</b>\n"
                f"  {symbol} 暴涨比 {surge_ratio}\n"
                f"  原因: {reason}"
            )

    async def notify_daily_summary(
        self,
        total_balance: str,
        daily_pnl: str,
        unrealized_pnl: str,
        open_positions: int,
        trades_today: int,
    ):
        """Send daily P&L summary report."""
        pnl_val = float(daily_pnl) if daily_pnl else 0
        pnl_emoji = "📈" if pnl_val >= 0 else "📉"
        unreal_val = float(unrealized_pnl) if unrealized_pnl else 0
        unreal_emoji = "🟢" if unreal_val >= 0 else "🔴"

        await self.send(
            f"{pnl_emoji} <b>每日盈亏报告</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"  余额:     {total_balance} USDT\n"
            f"  今日盈亏: {daily_pnl} USDT\n"
            f"  {unreal_emoji} 浮动盈亏: {unrealized_pnl} USDT\n"
            f"  持仓数:   {open_positions}\n"
            f"  今日交易: {trades_today} 笔"
        )
