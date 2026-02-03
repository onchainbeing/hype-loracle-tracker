"""Email notification service using Resend API or aiosmtplib."""

import logging
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


class EmailService:
    """Async email service for sending notifications."""

    def __init__(self, config: Config):
        self.config = config
        self.smtp_config = config.smtp
        self.resend_config = config.resend
        self.recipients = config.notifications.recipients

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

    async def send_position_change_alert(self, change: PositionChange) -> bool:
        """Send alert email for a position change."""
        subject = f"[HL Alert] {change.wallet_name} - {change.change_type.value.upper()} {change.coin}"

        html_body = self._format_position_change_html(change)
        text_body = change.format_message()

        return await self.send_email(subject, html_body, text_body)

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
                    lines.append(
                        f"  {pos.coin} {pos.side.upper()} | "
                        f"Size: {abs(pos.size):.4f} | "
                        f"Entry: ${pos.entry_price:.2f} | "
                        f"PnL: {pnl_str}"
                    )

            lines.append("")

        return "\n".join(lines)
