"""Email notification service using Resend API or aiosmtplib."""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import aiohttp
import aiosmtplib

from tracker.core.config import Config
from tracker.core.models import Position, PositionChange

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
PUSHPLUS_API_URL = "http://www.pushplus.plus/send"


class EmailService:
    """Async email service for sending notifications."""

    def __init__(self, config: Config):
        self.config = config
        self.smtp_config = config.smtp
        self.resend_config = config.resend
        self.pushplus_config = config.pushplus
        self.recipients = config.notifications.recipients

        # Batching state
        self._batch_window = config.notifications.batch_window_seconds
        self._pending_changes: list[PositionChange] = []
        self._batch_task: Optional[asyncio.Task] = None
        self._batch_lock = asyncio.Lock()

    async def send_email(
        self,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
    ) -> bool:
        """
        Send an email to all configured recipients.
        Uses Resend API if configured, falls back to SMTP.

        Args:
            subject: Email subject
            html_body: HTML content
            text_body: Plain text content (optional)

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.smtp_config.enabled:
            logger.debug("Email notifications are disabled, skipping")
            return False

        if not self.recipients:
            logger.warning("No email recipients configured")
            return False

        # Prefer Resend API
        if self.resend_config.api_key:
            return await self._send_via_resend(subject, html_body, text_body)

        # Fall back to SMTP
        return await self._send_via_smtp(subject, html_body, text_body)

    async def _send_via_resend(
        self,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
    ) -> bool:
        """Send email via Resend API."""
        try:
            headers = {
                "Authorization": f"Bearer {self.resend_config.api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "from": self.resend_config.from_email,
                "to": self.recipients,
                "subject": subject,
                "html": html_body,
            }

            if text_body:
                payload["text"] = text_body

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    RESEND_API_URL,
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status == 200:
                        logger.info(f"Email sent via Resend: {subject}")
                        return True
                    else:
                        error = await response.text()
                        logger.error(f"Resend API error: {response.status} - {error}")
                        return False

        except Exception as e:
            logger.error(f"Failed to send email via Resend: {e}")
            return False

    async def _send_via_smtp(
        self,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
    ) -> bool:
        """Send email via SMTP."""
        if not self.smtp_config.username or not self.smtp_config.password:
            logger.error("SMTP credentials not configured")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.smtp_config.username
            msg["To"] = ", ".join(self.recipients)

            if text_body:
                msg.attach(MIMEText(text_body, "plain"))

            msg.attach(MIMEText(html_body, "html"))

            await aiosmtplib.send(
                msg,
                hostname=self.smtp_config.host,
                port=self.smtp_config.port,
                start_tls=self.smtp_config.use_tls,
                use_tls=self.smtp_config.use_ssl,
                username=self.smtp_config.username,
                password=self.smtp_config.password,
            )

            logger.info(f"Email sent via SMTP: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")
            return False

    async def _send_via_pushplus(self, title: str, content: str) -> bool:
        """Send notification via PushPlus WeChat push."""
        if not self.pushplus_config.token:
            logger.debug("PushPlus token not configured, skipping")
            return False

        if not self.pushplus_config.enabled:
            logger.debug("PushPlus is disabled, skipping")
            return False

        try:
            payload = {
                "token": self.pushplus_config.token,
                "title": title,
                "content": content,
                "template": "markdown",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(PUSHPLUS_API_URL, json=payload) as response:
                    result = await response.json()
                    if result.get("code") == 200:
                        logger.info(f"PushPlus notification sent: {title}")
                        return True
                    else:
                        logger.error(f"PushPlus API error: {result}")
                        return False

        except Exception as e:
            logger.error(f"Failed to send PushPlus notification: {e}")
            return False

    async def queue_position_change_alert(self, change: PositionChange) -> None:
        """Queue a position change for batched notification."""
        async with self._batch_lock:
            self._pending_changes.append(change)
            logger.debug(f"Queued position change: {change.coin} ({len(self._pending_changes)} pending)")

            # Start batch timer if not already running
            if self._batch_task is None or self._batch_task.done():
                self._batch_task = asyncio.create_task(self._batch_timer())

    async def _batch_timer(self) -> None:
        """Wait for batch window then send all pending notifications."""
        await asyncio.sleep(self._batch_window)
        await self.flush_batch()

    async def flush_batch(self) -> bool:
        """Send all pending notifications as a single batched message."""
        async with self._batch_lock:
            if not self._pending_changes:
                return True

            changes = self._pending_changes.copy()
            self._pending_changes.clear()

        logger.info(f"Sending batched notification with {len(changes)} position changes")

        # Group changes by wallet for cleaner formatting
        changes_by_wallet: dict[str, list[PositionChange]] = defaultdict(list)
        for change in changes:
            changes_by_wallet[change.wallet_name].append(change)

        # Create summary subject
        coins = list(set(c.coin for c in changes))
        coins_str = ", ".join(coins[:3]) + ("..." if len(coins) > 3 else "")
        subject = f"[HL Alert] {len(changes)} position changes: {coins_str}"

        html_body = self._format_batched_changes_html(changes_by_wallet)
        text_body = self._format_batched_changes_text(changes_by_wallet)

        # Send email
        email_sent = await self.send_email(subject, html_body, text_body)

        # Send PushPlus WeChat notification
        pushplus_title = f"{len(changes)} Position Changes"
        pushplus_content = self._format_batched_changes_markdown(changes_by_wallet)
        pushplus_sent = await self._send_via_pushplus(pushplus_title, pushplus_content)

        return email_sent or pushplus_sent

    async def stop(self) -> None:
        """Stop the batcher and flush any pending notifications."""
        if self._batch_task and not self._batch_task.done():
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
        await self.flush_batch()

    async def send_position_change_alert(self, change: PositionChange) -> bool:
        """Send alert for a position change immediately (bypasses batching)."""
        subject = f"[HL Alert] {change.wallet_name} - {change.change_type.value.upper()} {change.coin}"

        html_body = self._format_position_change_html(change)
        text_body = change.format_message()

        # Send email
        email_sent = await self.send_email(subject, html_body, text_body)

        # Send PushPlus WeChat notification
        pushplus_title = f"{change.change_type.value.upper()} {change.coin}"
        pushplus_content = self._format_position_change_markdown(change)
        pushplus_sent = await self._send_via_pushplus(pushplus_title, pushplus_content)

        # Return True if at least one notification was sent
        return email_sent or pushplus_sent

    async def send_daily_summary(
        self,
        positions_by_wallet: dict[str, list[Position]],
        wallet_names: dict[str, str],
    ) -> bool:
        """
        Send daily summary email with all current positions.

        Args:
            positions_by_wallet: Dict mapping address to list of positions
            wallet_names: Dict mapping address to wallet name
        """
        now = datetime.now()
        subject = f"[HL Summary] Daily Position Summary - {now.strftime('%Y-%m-%d')}"

        html_body = self._format_daily_summary_html(positions_by_wallet, wallet_names)
        text_body = self._format_daily_summary_text(positions_by_wallet, wallet_names)

        return await self.send_email(subject, html_body, text_body)

    def _format_position_details_markdown(self, pos: Position) -> str:
        """Format position details as Markdown list (PushPlus doesn't support tables)."""
        upnl = pos.unrealized_pnl
        upnl_str = f"+${upnl:.2f}" if upnl >= 0 else f"-${abs(upnl):.2f}"
        liq_str = f"${pos.liquidation_price:.2f}" if pos.liquidation_price else "N/A"

        return "\n".join([
            "",
            "📊 **当前仓位**",
            f"- Symbol: **{pos.coin}**",
            f"- uPnL: **{upnl_str}**",
            f"- Entry Price: **${pos.entry_price:.2f}**",
            f"- Liq. Price: **{liq_str}**",
            f"- Margin: **${pos.margin_used:.2f}**",
            "",
        ])

    def _format_position_change_markdown(self, change: PositionChange) -> str:
        """Format position change as Markdown for PushPlus WeChat notification."""
        emoji_map = {
            "open": "🟢",
            "close": "🔴",
            "increase": "🔵",
            "decrease": "🟡",
            "flip": "🟣",
        }
        emoji = emoji_map.get(change.change_type.value, "🔔")

        direction = change.new_side.upper() if change.new_size != 0 else change.old_side.upper()
        old_size = abs(change.old_size)
        new_size = abs(change.new_size)

        lines = [
            f"## {emoji} {change.change_type.value.upper()} {change.coin}",
            "",
            f"**钱包**: {change.wallet_name}",
            f"**方向**: {direction}",
            f"**仓位**: {old_size:.2f} → {new_size:.2f}",
            f"**入场价**: ${change.new_entry_price:.2f}",
        ]

        # Add PnL if available
        if change.fill and change.fill.closed_pnl != 0:
            pnl = change.fill.closed_pnl
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            lines.append(f"**已实现盈亏**: {pnl_str}")

        # Add current position details
        if change.current_position:
            lines.append(self._format_position_details_markdown(change.current_position))

        # Add trade details
        if change.fill:
            fill = change.fill
            side_text = "买入" if fill.side == "B" else "卖出"
            lines.extend([
                "",
                "---",
                "**交易详情**",
                f"{side_text} {fill.size:.4f} {fill.coin} @ ${fill.price:.4f}",
                f"时间: {fill.time.strftime('%H:%M:%S')}",
            ])

        return "\n".join(lines)

    def _format_position_details_html(self, pos: Position) -> str:
        """Format position details as HTML table."""
        upnl = pos.unrealized_pnl
        upnl_color = "#22c55e" if upnl >= 0 else "#ef4444"
        upnl_str = f"+${upnl:.2f}" if upnl >= 0 else f"-${abs(upnl):.2f}"
        liq_str = f"${pos.liquidation_price:.2f}" if pos.liquidation_price else "N/A"

        return f"""
                    <div style="background: #e5e7eb; padding: 15px; border-radius: 8px; margin-top: 15px;">
                        <h4 style="margin: 0 0 10px 0; color: #374151;">📊 Current Position</h4>
                        <div class="info-row">
                            <span class="label">Symbol</span>
                            <span class="value">{pos.coin}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">uPnL</span>
                            <span class="value" style="color: {upnl_color};">{upnl_str}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Entry Price</span>
                            <span class="value">${pos.entry_price:.2f}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Liq. Price</span>
                            <span class="value">{liq_str}</span>
                        </div>
                        <div class="info-row" style="border-bottom: none;">
                            <span class="label">Margin</span>
                            <span class="value">${pos.margin_used:.2f}</span>
                        </div>
                    </div>"""

    def _format_position_change_html(self, change: PositionChange) -> str:
        """Format position change as HTML email."""
        color_map = {
            "open": "#22c55e",      # green
            "close": "#ef4444",     # red
            "increase": "#3b82f6",  # blue
            "decrease": "#f59e0b",  # amber
            "flip": "#8b5cf6",      # purple
        }
        color = color_map.get(change.change_type.value, "#6b7280")

        pnl_html = ""
        if change.fill and change.fill.closed_pnl != 0:
            pnl = change.fill.closed_pnl
            pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            pnl_html = f"""
                    <div class="info-row">
                        <span class="label">Realized PnL</span>
                        <span class="value" style="color: {pnl_color};">{pnl_str}</span>
                    </div>"""

        # Current position details
        position_html = ""
        if change.current_position:
            position_html = self._format_position_details_html(change.current_position)

        # Fill/Trade details section
        fill_html = ""
        if change.fill:
            fill = change.fill
            side_text = "BUY" if fill.side == "B" else "SELL"
            side_color = "#22c55e" if fill.side == "B" else "#ef4444"
            notional = fill.price * fill.size
            fill_html = f"""
                    <div style="background: #e5e7eb; padding: 15px; border-radius: 8px; margin-top: 15px;">
                        <h4 style="margin: 0 0 10px 0; color: #374151;">Trade Details</h4>
                        <div class="info-row">
                            <span class="label">Action</span>
                            <span class="value" style="color: {side_color};">{side_text}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Trade Size</span>
                            <span class="value">{fill.size:.4f} {fill.coin}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Trade Price</span>
                            <span class="value">${fill.price:.4f}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Notional Value</span>
                            <span class="value">${notional:.2f}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Fee</span>
                            <span class="value">${fill.fee:.4f}</span>
                        </div>
                        <div class="info-row" style="border-bottom: none;">
                            <span class="label">Trade Time</span>
                            <span class="value">{fill.time.strftime('%Y-%m-%d %H:%M:%S')}</span>
                        </div>
                    </div>"""

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: {color}; color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
                .content {{ background: #f8f9fa; padding: 20px; border-radius: 0 0 8px 8px; }}
                .info-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #e5e7eb; }}
                .label {{ color: #6b7280; }}
                .value {{ font-weight: 600; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2 style="margin: 0;">{change.change_type.value.upper()} {change.coin}</h2>
                    <p style="margin: 5px 0 0 0; opacity: 0.9;">{change.wallet_name}</p>
                </div>
                <div class="content">
                    <h4 style="margin: 0 0 10px 0; color: #374151;">Position Update</h4>
                    <div class="info-row">
                        <span class="label">Direction</span>
                        <span class="value">{change.new_side.upper() if change.new_size != 0 else change.old_side.upper()}</span>
                    </div>
                    <div class="info-row">
                        <span class="label">Old Size</span>
                        <span class="value">{abs(change.old_size):.4f}</span>
                    </div>
                    <div class="info-row">
                        <span class="label">New Size</span>
                        <span class="value">{abs(change.new_size):.4f}</span>
                    </div>
                    <div class="info-row">
                        <span class="label">Entry Price</span>
                        <span class="value">${change.new_entry_price:.2f}</span>
                    </div>
                    {pnl_html}
                    {position_html}
                    {fill_html}
                    <p style="color: #9ca3af; font-size: 12px; margin-top: 20px;">
                        {change.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

    def _format_daily_summary_html(
        self,
        positions_by_wallet: dict[str, list[Position]],
        wallet_names: dict[str, str],
    ) -> str:
        """Format daily summary as HTML email."""
        now = datetime.now()

        wallets_html = ""
        for address, positions in positions_by_wallet.items():
            wallet_name = wallet_names.get(address, address[:10] + "...")

            if not positions:
                positions_html = '<p style="color: #9ca3af;">No open positions</p>'
            else:
                positions_html = ""
                for pos in positions:
                    side_color = "#22c55e" if pos.size > 0 else "#ef4444"
                    pnl_color = "#22c55e" if pos.unrealized_pnl >= 0 else "#ef4444"
                    pnl_str = f"+${pos.unrealized_pnl:.2f}" if pos.unrealized_pnl >= 0 else f"-${abs(pos.unrealized_pnl):.2f}"
                    liq_str = f"${pos.liquidation_price:.2f}" if pos.liquidation_price else "N/A"

                    positions_html += f"""
                    <div style="background: white; padding: 15px; border-radius: 8px; margin-bottom: 10px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <span style="font-weight: 600; font-size: 16px;">{pos.coin}</span>
                                <span style="color: {side_color}; margin-left: 8px;">{pos.side.upper()}</span>
                            </div>
                            <span style="color: {pnl_color}; font-weight: 600;">{pnl_str}</span>
                        </div>
                        <div style="margin-top: 10px; color: #6b7280; font-size: 14px;">
                            Size: {abs(pos.size):.4f} | Entry: ${pos.entry_price:.2f} | Leverage: {pos.leverage}x
                        </div>
                        <div style="margin-top: 6px; color: #6b7280; font-size: 14px;">
                            Liq. Price: {liq_str} | Margin: ${pos.margin_used:.2f}
                        </div>
                    </div>
                    """

            wallets_html += f"""
            <div style="margin-bottom: 30px;">
                <h3 style="color: #374151; border-bottom: 2px solid #e5e7eb; padding-bottom: 10px;">
                    {wallet_name}
                </h3>
                {positions_html}
            </div>
            """

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f3f4f6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2 style="color: #111827;">Daily Position Summary</h2>
                <p style="color: #6b7280;">{now.strftime('%Y-%m-%d %H:%M:%S')}</p>
                {wallets_html}
                <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 30px;">
                    Hyperliquid Smart Money Tracker
                </p>
            </div>
        </body>
        </html>
        """

    def _format_daily_summary_text(
        self,
        positions_by_wallet: dict[str, list[Position]],
        wallet_names: dict[str, str],
    ) -> str:
        """Format daily summary as plain text."""
        now = datetime.now()
        lines = [
            f"Daily Position Summary - {now.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 50,
            "",
        ]

        for address, positions in positions_by_wallet.items():
            wallet_name = wallet_names.get(address, address[:10] + "...")
            lines.append(f"[{wallet_name}]")

            if not positions:
                lines.append("  No open positions")
            else:
                for pos in positions:
                    pnl_str = f"+${pos.unrealized_pnl:.2f}" if pos.unrealized_pnl >= 0 else f"-${abs(pos.unrealized_pnl):.2f}"
                    liq_str = f"${pos.liquidation_price:.2f}" if pos.liquidation_price else "N/A"
                    lines.append(
                        f"  {pos.coin} {pos.side.upper()} | "
                        f"Size: {abs(pos.size):.4f} | "
                        f"Entry: ${pos.entry_price:.2f} | "
                        f"uPnL: {pnl_str}"
                    )
                    lines.append(
                        f"    Liq. Price: {liq_str} | "
                        f"Margin: ${pos.margin_used:.2f}"
                    )

            lines.append("")

        return "\n".join(lines)

    def _format_batched_changes_markdown(
        self,
        changes_by_wallet: dict[str, list[PositionChange]],
    ) -> str:
        """Format batched position changes as Markdown for PushPlus."""
        emoji_map = {
            "open": "🟢",
            "close": "🔴",
            "increase": "🔵",
            "decrease": "🟡",
            "flip": "🟣",
        }

        lines = [f"## 📊 批量仓位变动通知", ""]

        for wallet_name, changes in changes_by_wallet.items():
            lines.append(f"### {wallet_name}")
            lines.append("")

            for change in changes:
                emoji = emoji_map.get(change.change_type.value, "🔔")
                direction = change.new_side.upper() if change.new_size != 0 else change.old_side.upper()
                old_size = abs(change.old_size)
                new_size = abs(change.new_size)

                lines.append(
                    f"- {emoji} **{change.change_type.value.upper()}** {change.coin} "
                    f"({direction}) {old_size:.2f} → {new_size:.2f}"
                )

                # Add current position details
                if change.current_position:
                    lines.append(self._format_position_details_markdown(change.current_position))

            lines.append("")

        lines.append(f"---")
        lines.append(f"*{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

        return "\n".join(lines)

    def _format_batched_changes_text(
        self,
        changes_by_wallet: dict[str, list[PositionChange]],
    ) -> str:
        """Format batched position changes as plain text."""
        now = datetime.now()
        total = sum(len(changes) for changes in changes_by_wallet.values())
        lines = [
            f"Batched Position Changes ({total} changes)",
            f"{now.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 50,
            "",
        ]

        for wallet_name, changes in changes_by_wallet.items():
            lines.append(f"[{wallet_name}]")

            for change in changes:
                direction = change.new_side.upper() if change.new_size != 0 else change.old_side.upper()
                lines.append(
                    f"  {change.change_type.value.upper()} {change.coin} ({direction}): "
                    f"{abs(change.old_size):.4f} → {abs(change.new_size):.4f}"
                )

            lines.append("")

        return "\n".join(lines)

    def _format_batched_changes_html(
        self,
        changes_by_wallet: dict[str, list[PositionChange]],
    ) -> str:
        """Format batched position changes as HTML email."""
        now = datetime.now()
        total = sum(len(changes) for changes in changes_by_wallet.values())

        color_map = {
            "open": "#22c55e",
            "close": "#ef4444",
            "increase": "#3b82f6",
            "decrease": "#f59e0b",
            "flip": "#8b5cf6",
        }

        wallets_html = ""
        for wallet_name, changes in changes_by_wallet.items():
            changes_html = ""
            for change in changes:
                color = color_map.get(change.change_type.value, "#6b7280")
                direction = change.new_side.upper() if change.new_size != 0 else change.old_side.upper()
                old_size = abs(change.old_size)
                new_size = abs(change.new_size)

                pnl_html = ""
                if change.fill and change.fill.closed_pnl != 0:
                    pnl = change.fill.closed_pnl
                    pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"
                    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                    pnl_html = f' <span style="color: {pnl_color};">({pnl_str})</span>'

                # Current position details
                position_html = ""
                if change.current_position:
                    pos = change.current_position
                    upnl = pos.unrealized_pnl
                    upnl_color = "#22c55e" if upnl >= 0 else "#ef4444"
                    upnl_str = f"+${upnl:.2f}" if upnl >= 0 else f"-${abs(upnl):.2f}"
                    liq_str = f"${pos.liquidation_price:.2f}" if pos.liquidation_price else "N/A"
                    position_html = f"""
                    <div style="margin-top: 8px; padding: 10px; background: #f3f4f6; border-radius: 4px; font-size: 12px;">
                        <div style="font-weight: 600; margin-bottom: 6px; color: #374151;">📊 Current Position</div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px; color: #6b7280;">
                            <span>uPnL: <span style="color: {upnl_color};">{upnl_str}</span></span>
                            <span>Entry: ${pos.entry_price:.2f}</span>
                            <span>Liq: {liq_str}</span>
                            <span>Margin: ${pos.margin_used:.2f}</span>
                        </div>
                    </div>
                    """

                changes_html += f"""
                <div style="background: white; padding: 12px; border-radius: 6px; margin-bottom: 8px; border-left: 4px solid {color};">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="font-weight: 600;">{change.coin}</span>
                            <span style="color: {color}; margin-left: 8px; font-size: 12px; text-transform: uppercase;">{change.change_type.value}</span>
                        </div>
                        <span style="color: #6b7280; font-size: 12px;">{direction}</span>
                    </div>
                    <div style="margin-top: 6px; color: #6b7280; font-size: 13px;">
                        Size: {old_size:.4f} → {new_size:.4f}{pnl_html}
                    </div>
                    {position_html}
                </div>
                """

            wallets_html += f"""
            <div style="margin-bottom: 24px;">
                <h3 style="color: #374151; margin: 0 0 12px 0; font-size: 16px;">{wallet_name}</h3>
                {changes_html}
            </div>
            """

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f3f4f6; margin: 0; padding: 0; }}
            </style>
        </head>
        <body>
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: #3b82f6; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                    <h2 style="margin: 0;">Position Changes Summary</h2>
                    <p style="margin: 5px 0 0 0; opacity: 0.9;">{total} changes detected</p>
                </div>
                <div style="background: #f8f9fa; padding: 20px; border-radius: 0 0 8px 8px;">
                    {wallets_html}
                    <p style="color: #9ca3af; font-size: 12px; margin-top: 20px; text-align: center;">
                        {now.strftime('%Y-%m-%d %H:%M:%S')} • Hyperliquid Smart Money Tracker
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
