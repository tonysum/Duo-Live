"""Telegram notification service for live trading events.

Configure via environment variables:
    TELEGRAM_BOT_TOKEN  — Bot token from @BotFather
    TELEGRAM_CHAT_ID    — Your Telegram user/group chat ID
    SMTP_EMAIL          — Email address for sending alerts (optional)
    SMTP_PASSWORD       — Email password/authorization code (optional)
    ALERT_EMAIL         — Email address to receive alerts (optional)

If not configured, notifications are silently skipped (no error).
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Telegram API base
_TG_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Send trading notifications via Telegram bot and email.

    Uses a shared persistent httpx.AsyncClient to avoid per-message
    TLS handshake overhead (A).
    
    🔧 新增（从 AE Server 移植）：邮件报警功能
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        smtp_email: Optional[str] = None,
        smtp_password: Optional[str] = None,
        alert_email: Optional[str] = None,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)

        # 🔧 邮件配置（从 AE Server 移植）
        self.smtp_email = smtp_email or os.getenv("SMTP_EMAIL", "")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD", "")
        self.alert_email = alert_email or os.getenv("ALERT_EMAIL", "")
        self.email_enabled = bool(self.smtp_email and self.smtp_password and self.alert_email)

        # Shared client — created lazily, reused across all send() calls
        # This avoids a TLS handshake on every notification.
        self._client: Optional[httpx.AsyncClient] = None

        if not self.enabled:
            logger.info("📵 Telegram 通知未配置 (跳过推送)")
        else:
            logger.info("📱 Telegram 通知已启用")
        
        if not self.email_enabled:
            logger.info("📧 邮件报警未配置 (跳过邮件)")
        else:
            logger.info("📧 邮件报警已启用 (发送至: %s)", self.alert_email)

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared client, creating it lazily."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client. Call on shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

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
            client = self._get_client()
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            else:
                logger.warning("Telegram 发送失败: %s %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.warning("Telegram 发送异常: %s", e)
            # Invalidate client so next call gets a fresh one
            self._client = None
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

    # ── 邮件报警功能（从 AE Server 移植）────────────────────────────

    async def send_email_alert(self, subject: str, message: str) -> bool:
        """发送邮件报警（从 AE Server 移植）
        
        Args:
            subject: 邮件主题
            message: 邮件正文
            
        Returns:
            bool: 是否发送成功
        """
        if not self.email_enabled:
            logger.debug("邮件报警未配置，跳过发送")
            return False

        try:
            # 创建邮件
            msg = MIMEMultipart()
            msg['From'] = self.smtp_email
            msg['To'] = self.alert_email
            msg['Subject'] = f"[duo-live 交易系统] {subject}"
            
            # 邮件正文
            import socket
            hostname = socket.gethostname()
            body = f"""
duo-live 自动交易系统报警

时间: {asyncio.get_event_loop().time()}

{message}

---
此邮件由 duo-live 交易系统自动发送
服务器: {hostname}
"""
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            # 发送邮件（使用163邮箱SMTP服务）
            with smtplib.SMTP_SSL('smtp.163.com', 465, timeout=10) as server:
                server.login(self.smtp_email, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"✅ 邮件报警已发送: {subject}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 发送邮件报警失败: {e}")
            return False

    def send_email_alert_sync(self, subject: str, message: str) -> bool:
        """同步版本的邮件报警"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.send_email_alert(subject, message))
                return True
            else:
                return loop.run_until_complete(self.send_email_alert(subject, message))
        except RuntimeError:
            return asyncio.run(self.send_email_alert(subject, message))

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

    async def notify_tp_triggered(
        self,
        symbol: str,
        side: str,
        price: str = "",
        pnl_usdt: str = "",
    ):
        """Take-profit triggered. (D: includes price and PnL)"""
        lines = [f"🎯 <b>止盈触发</b> 💰\n  {symbol} {side}"]
        if price:
            lines.append(f"  平仓价: {price}")
        if pnl_usdt:
            val = float(pnl_usdt)
            sign = "+" if val >= 0 else ""
            lines.append(f"  盈亏: {sign}{pnl_usdt} USDT")
        await self.send("\n".join(lines))

    async def notify_sl_triggered(
        self,
        symbol: str,
        side: str,
        price: str = "",
        pnl_usdt: str = "",
    ):
        """Stop-loss triggered. (D: includes price and PnL)"""
        lines = [f"🛑 <b>止损触发</b>\n  {symbol} {side}"]
        if price:
            lines.append(f"  平仓价: {price}")
        if pnl_usdt:
            val = float(pnl_usdt)
            sign = "+" if val >= 0 else ""
            lines.append(f"  盈亏: {sign}{pnl_usdt} USDT")
        await self.send("\n".join(lines))

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

    async def notify_signal(
        self, symbol: str, surge_ratio: str, price: str,
        accepted: bool, reason: str = "",
    ):
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
        yesterday_balance: str | None = None,
    ):
        """Send daily P&L summary report."""
        pnl_val = float(daily_pnl) if daily_pnl else 0
        pnl_emoji = "📈" if pnl_val >= 0 else "📉"
        unreal_val = float(unrealized_pnl) if unrealized_pnl else 0
        unreal_emoji = "🟢" if unreal_val >= 0 else "🔴"

        lines = [
            f"{pnl_emoji} <b>每日盈亏报告</b>",
            "━━━━━━━━━━━━━━",
            f"  余额:     {total_balance} USDT",
        ]
        if yesterday_balance:
            lines.append(f"  昨日余额: {yesterday_balance} USDT")
            try:
                today_v = float(total_balance.replace(",", ""))
                yest_v = float(yesterday_balance.replace(",", ""))
                diff = today_v - yest_v
                sign = "+" if diff >= 0 else ""
                lines.append(f"  日变化:   {sign}{diff:,.2f} USDT")
            except ValueError:
                pass
        lines.extend([
            f"  今日盈亏: {daily_pnl} USDT",
            f"  {unreal_emoji} 浮动盈亏: {unrealized_pnl} USDT",
            f"  持仓数:   {open_positions}",
            f"  今日交易: {trades_today} 笔",
        ])
        await self.send("\n".join(lines))

    # ── 紧急报警（同时发送 Telegram 和邮件）────────────────────────

    async def send_critical_alert(self, subject: str, message: str):
        """发送紧急报警（同时通过 Telegram 和邮件）
        
        用于需要立即人工干预的严重问题，如：
        - 平仓失败
        - 保证金不足
        - 系统异常
        
        Args:
            subject: 报警主题
            message: 报警详情
        """
        # 发送 Telegram 通知
        telegram_msg = f"🚨 <b>{subject}</b>\n\n{message}"
        await self.send(telegram_msg)
        
        # 同时发送邮件报警
        await self.send_email_alert(subject, message)
