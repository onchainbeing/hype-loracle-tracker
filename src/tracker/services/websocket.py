"""WebSocket manager for real-time updates from Hyperliquid."""

import asyncio
import json
import logging
import time
from typing import Callable, Optional, Any

import websockets
from websockets.client import WebSocketClientProtocol

from tracker.core.models import Fill

logger = logging.getLogger(__name__)

# Connection health constants
PING_INTERVAL = 10  # Send ping every 10 seconds
PING_TIMEOUT = 5    # Wait 5 seconds for pong
HEALTH_CHECK_INTERVAL = 30  # Application-level health check every 30 seconds
MAX_SILENCE_SECONDS = 60  # Force reconnect if no message received for 60 seconds


class WebSocketManager:
    """Manages WebSocket connection to Hyperliquid for real-time fills."""

    def __init__(
        self,
        ws_url: str = "wss://api.hyperliquid.xyz/ws",
        reconnect_delay: int = 5,
        max_reconnect_delay: int = 300,
    ):
        self.ws_url = ws_url
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay

        self._ws: Optional[WebSocketClientProtocol] = None
        self._subscriptions: dict[str, set[str]] = {}  # channel -> set of addresses
        self._callbacks: dict[str, Callable] = {}  # channel -> callback
        self._running = False
        self._current_delay = reconnect_delay
        self._last_message_time: float = 0  # Track last received message time
        self._health_check_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                ping_interval=PING_INTERVAL,
                ping_timeout=PING_TIMEOUT,
                close_timeout=5,
            )
            self._current_delay = self.reconnect_delay
            self._last_message_time = time.monotonic()
            logger.info(f"WebSocket connected to {self.ws_url}")

            # Resubscribe after reconnection
            await self._resubscribe()

        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            raise

    async def _resubscribe(self) -> None:
        """Resubscribe to all channels after reconnection."""
        for channel, addresses in self._subscriptions.items():
            for address in addresses:
                await self._send_subscription(channel, address)

    async def _send_subscription(self, channel: str, address: str) -> None:
        """Send subscription message to WebSocket."""
        if not self._ws:
            return

        msg = {
            "method": "subscribe",
            "subscription": {
                "type": channel,
                "user": address.lower(),
            },
        }

        await self._ws.send(json.dumps(msg))
        logger.debug(f"Subscribed to {channel} for {address[:10]}...")

    async def subscribe_fills(
        self,
        address: str,
        callback: Callable[[str, Fill], Any],
    ) -> None:
        """
        Subscribe to real-time fills for an address.

        Args:
            address: Wallet address to monitor
            callback: Async function called with (address, Fill) on new fill
        """
        channel = "userFills"

        if channel not in self._subscriptions:
            self._subscriptions[channel] = set()

        self._subscriptions[channel].add(address.lower())
        self._callbacks[channel] = callback

        if self._ws:
            await self._send_subscription(channel, address)

    async def unsubscribe_fills(self, address: str) -> None:
        """Unsubscribe from fills for an address."""
        channel = "userFills"

        if channel in self._subscriptions:
            self._subscriptions[channel].discard(address.lower())

        if self._ws:
            msg = {
                "method": "unsubscribe",
                "subscription": {
                    "type": channel,
                    "user": address.lower(),
                },
            }
            await self._ws.send(json.dumps(msg))

    async def _handle_message(self, message: str) -> None:
        """Process incoming WebSocket message."""
        self._last_message_time = time.monotonic()

        try:
            data = json.loads(message)

            # Handle subscription confirmation
            if data.get("channel") == "subscriptionResponse":
                logger.debug(f"Subscription confirmed: {data}")
                return

            # Handle fills
            if data.get("channel") == "userFills":
                await self._handle_fills(data)
                return

            # Handle pong
            if data.get("channel") == "pong":
                return

            logger.debug(f"Unhandled message type: {data.get('channel')}")

        except json.JSONDecodeError:
            logger.error(f"Failed to parse WebSocket message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")

    async def _handle_fills(self, data: dict) -> None:
        """Process fill notification."""
        callback = self._callbacks.get("userFills")
        if not callback:
            return

        fills_data = data.get("data", {})
        user = fills_data.get("user", "").lower()

        for fill_data in fills_data.get("fills", []):
            try:
                fill = Fill.from_api(user, fill_data)
                await callback(user, fill)
            except Exception as e:
                logger.error(f"Error processing fill: {e}")

    async def _health_check_loop(self) -> None:
        """Monitor connection health and force reconnect if stale."""
        while self._running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            if not self._running or self._is_ws_closed():
                continue

            silence_duration = time.monotonic() - self._last_message_time
            if silence_duration > MAX_SILENCE_SECONDS:
                logger.warning(
                    f"No messages received for {silence_duration:.0f}s, forcing reconnect"
                )
                # Force close the connection to trigger reconnect
                if self._ws and not self._is_ws_closed():
                    await self._ws.close()

    def _is_ws_closed(self) -> bool:
        """Check if WebSocket connection is closed."""
        if not self._ws:
            return True
        try:
            return self._ws.closed
        except AttributeError:
            # Newer websockets versions use different API
            try:
                return self._ws.state.name != "OPEN"
            except Exception:
                return True

    async def run(self) -> None:
        """Main loop - maintain connection and process messages."""
        self._running = True

        # Start health check task
        self._health_check_task = asyncio.create_task(self._health_check_loop())

        while self._running:
            try:
                if self._is_ws_closed():
                    await self.connect()

                async for message in self._ws:
                    if not self._running:
                        break
                    await self._handle_message(message)

            except websockets.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                await self._reconnect()

            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await self._reconnect()

    async def _reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        if not self._running:
            return

        logger.info(f"Reconnecting in {self._current_delay}s...")
        await asyncio.sleep(self._current_delay)

        # Exponential backoff
        self._current_delay = min(
            self._current_delay * 2,
            self.max_reconnect_delay,
        )

    async def stop(self) -> None:
        """Stop the WebSocket manager."""
        self._running = False

        # Cancel health check task
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        if self._ws and not self._is_ws_closed():
            await self._ws.close()

        logger.info("WebSocket manager stopped")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
