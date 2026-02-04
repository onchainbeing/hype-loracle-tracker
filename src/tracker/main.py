"""Main application - orchestrates all components."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from tracker.core.config import Config, load_config
from tracker.core.models import Fill, PositionChange
from tracker.services.hyperliquid import HyperliquidClient
from tracker.services.websocket import WebSocketManager
from tracker.services.tracker import PositionTracker
from tracker.services.email import EmailService
from tracker.services.scheduler import Scheduler
from tracker.persistence.database import Database

logger = logging.getLogger(__name__)


class Application:
    """Main application class."""

    def __init__(self, config: Config):
        self.config = config

        # Initialize components
        self.client = HyperliquidClient(config.hyperliquid.rest_url)
        self.ws_manager = WebSocketManager(
            ws_url=config.hyperliquid.ws_url,
            reconnect_delay=config.hyperliquid.reconnect_delay,
            max_reconnect_delay=config.hyperliquid.max_reconnect_delay,
        )
        self.database = Database(config.get_db_path())
        self.email_service = EmailService(config)
        self.scheduler = Scheduler(config.notifications.timezone)

        # Tracker will be initialized after database
        self.tracker: PositionTracker = None

        self._shutdown_event: asyncio.Event = None

    async def start(self) -> None:
        """Start the application."""
        self._shutdown_event = asyncio.Event()
        logger.info("Starting Hyperliquid Smart Money Tracker...")

        # Connect to database
        await self.database.connect()

        # Initialize position tracker
        self.tracker = PositionTracker(
            config=self.config,
            client=self.client,
            on_change=self._on_position_change,
        )
        await self.tracker.initialize()

        # Subscribe to WebSocket fills for all wallets
        for wallet in self.config.get_enabled_wallets():
            await self.ws_manager.subscribe_fills(
                wallet.address,
                self._on_fill,
            )
            logger.info(f"Subscribed to fills for {wallet.name}")

        # Start daily summary scheduler
        self.scheduler.start_daily_task(
            target_time=self.config.notifications.daily_summary_time,
            callback=self._send_daily_summary,
            name="daily_summary",
        )

        # Start WebSocket connection
        ws_task = asyncio.create_task(self.ws_manager.run())

        logger.info("Application started successfully")

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cleanup
        await self._cleanup()

    async def _on_fill(self, address: str, fill: Fill) -> None:
        """Handle incoming fill from WebSocket."""
        # Check for duplicate
        if await self.database.fill_exists(fill.tid):
            logger.debug(f"Duplicate fill ignored: tid={fill.tid}")
            return

        # Save fill to database
        await self.database.save_fill(fill)

        # Process through tracker
        await self.tracker.handle_fill(address, fill)

    async def _on_position_change(self, change: PositionChange) -> None:
        """Handle detected position change."""
        # Save to database
        await self.database.save_position_change(change)

        # Queue notification for batched sending
        try:
            await self.email_service.queue_position_change_alert(change)
        except Exception as e:
            logger.error(f"Failed to queue position change alert: {e}")

    async def _send_daily_summary(self) -> None:
        """Send daily summary email."""
        positions = self.tracker.get_all_positions()
        wallet_names = {
            w.address.lower(): w.name
            for w in self.config.get_enabled_wallets()
        }

        try:
            await self.email_service.send_daily_summary(positions, wallet_names)
        except Exception as e:
            logger.error(f"Failed to send daily summary: {e}")

    async def _cleanup(self) -> None:
        """Cleanup resources."""
        logger.info("Shutting down...")

        await self.scheduler.stop()
        await self.ws_manager.stop()
        await self.email_service.stop()  # Flush pending notifications
        await self.client.close()
        await self.database.close()

        logger.info("Shutdown complete")

    def shutdown(self) -> None:
        """Signal the application to shutdown."""
        self._shutdown_event.set()


def setup_logging(config: Config) -> None:
    """Configure logging."""
    log_path = config.get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # File handler
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.logging.level.upper()))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def run() -> None:
    """Entry point for the application."""
    # Load configuration
    config = load_config()

    # Setup logging
    setup_logging(config)

    # Create application
    app = Application(config)

    # Setup signal handlers
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler():
        logger.info("Received shutdown signal")
        app.shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Run application
    try:
        loop.run_until_complete(app.start())
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)
    finally:
        loop.close()


if __name__ == "__main__":
    run()
