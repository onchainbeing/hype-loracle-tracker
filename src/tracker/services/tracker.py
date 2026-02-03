"""Position tracking logic - detects changes and triggers notifications."""

import asyncio
import logging
from typing import Callable, Optional, Any

from tracker.core.config import Config, WalletConfig
from tracker.core.models import Position, Fill, PositionChange, ChangeType
from tracker.services.hyperliquid import HyperliquidClient

logger = logging.getLogger(__name__)


class PositionTracker:
    """Tracks positions for multiple wallets and detects changes."""

    def __init__(
        self,
        config: Config,
        client: HyperliquidClient,
        on_change: Optional[Callable[[PositionChange], Any]] = None,
    ):
        self.config = config
        self.client = client
        self.on_change = on_change

        # Current positions by wallet address, then by coin
        # {address: {coin: Position}}
        self._positions: dict[str, dict[str, Position]] = {}

        # Wallet config lookup by address
        self._wallet_map: dict[str, WalletConfig] = {
            w.address.lower(): w for w in config.get_enabled_wallets()
        }

    async def initialize(self) -> None:
        """Load initial positions for all tracked wallets."""
        logger.info("Initializing position tracker...")

        for wallet in self.config.get_enabled_wallets():
            try:
                positions = await self.client.get_positions(wallet.address)
                self._positions[wallet.address.lower()] = {
                    p.coin: p for p in positions
                }
                logger.info(
                    f"Loaded {len(positions)} positions for {wallet.name} "
                    f"({wallet.address[:10]}...)"
                )
            except Exception as e:
                logger.error(f"Failed to load positions for {wallet.name}: {e}")
                self._positions[wallet.address.lower()] = {}

    def get_positions(self, address: str) -> list[Position]:
        """Get current positions for an address."""
        return list(self._positions.get(address.lower(), {}).values())

    def get_all_positions(self) -> dict[str, list[Position]]:
        """Get all positions grouped by wallet address."""
        return {
            addr: list(positions.values())
            for addr, positions in self._positions.items()
        }

    async def handle_fill(self, address: str, fill: Fill) -> Optional[PositionChange]:
        """
        Process a new fill and detect position changes.

        Args:
            address: Wallet address
            fill: The new fill

        Returns:
            PositionChange if position changed, None otherwise
        """
        address = address.lower()
        wallet = self._wallet_map.get(address)

        if not wallet:
            logger.warning(f"Received fill for unknown wallet: {address}")
            return None

        logger.info(
            f"Processing fill for {wallet.name}: {fill.side} {fill.size} {fill.coin} @ ${fill.price}"
        )

        # Get current position state
        current = self._positions.get(address, {}).get(fill.coin)
        old_size = current.size if current else 0
        old_entry_price = current.entry_price if current else 0

        # Fetch updated position from API
        try:
            positions = await self.client.get_positions(address)
            position_map = {p.coin: p for p in positions}

            # Update stored positions
            self._positions[address] = position_map

            # Get new position state
            new_position = position_map.get(fill.coin)
            new_size = new_position.size if new_position else 0
            new_entry_price = new_position.entry_price if new_position else 0

        except Exception as e:
            logger.error(f"Failed to fetch updated positions: {e}")
            return None

        # Detect change type
        change_type = self._detect_change_type(old_size, new_size)

        if change_type is None:
            logger.debug(f"No significant position change detected for {fill.coin}")
            return None

        # Create position change record
        change = PositionChange(
            wallet_address=address,
            wallet_name=wallet.name,
            coin=fill.coin,
            change_type=change_type,
            old_size=old_size,
            new_size=new_size,
            old_entry_price=old_entry_price,
            new_entry_price=new_entry_price,
            fill=fill,
        )

        logger.info(f"Position change detected: {change.format_message()}")

        # Trigger callback
        if self.on_change:
            try:
                result = self.on_change(change)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Error in change callback: {e}")

        return change

    def _detect_change_type(
        self,
        old_size: float,
        new_size: float,
    ) -> Optional[ChangeType]:
        """Determine the type of position change."""
        # No change
        if old_size == new_size:
            return None

        # Position opened from zero
        if old_size == 0 and new_size != 0:
            return ChangeType.OPEN

        # Position closed to zero
        if old_size != 0 and new_size == 0:
            return ChangeType.CLOSE

        # Position flipped (long to short or vice versa)
        if (old_size > 0 and new_size < 0) or (old_size < 0 and new_size > 0):
            return ChangeType.FLIP

        # Position increased (same direction, larger size)
        if abs(new_size) > abs(old_size):
            return ChangeType.INCREASE

        # Position decreased (same direction, smaller size)
        if abs(new_size) < abs(old_size):
            return ChangeType.DECREASE

        return None

    async def sync_positions(self) -> list[PositionChange]:
        """
        Sync positions for all wallets and detect any changes.

        This is useful for detecting changes that might have been missed
        (e.g., during disconnection).

        Returns:
            List of detected position changes
        """
        changes = []

        for wallet in self.config.get_enabled_wallets():
            try:
                address = wallet.address.lower()
                old_positions = self._positions.get(address, {})

                # Fetch current positions
                new_positions_list = await self.client.get_positions(wallet.address)
                new_positions = {p.coin: p for p in new_positions_list}

                # Check for changes
                all_coins = set(old_positions.keys()) | set(new_positions.keys())

                for coin in all_coins:
                    old_pos = old_positions.get(coin)
                    new_pos = new_positions.get(coin)

                    old_size = old_pos.size if old_pos else 0
                    new_size = new_pos.size if new_pos else 0

                    change_type = self._detect_change_type(old_size, new_size)

                    if change_type:
                        change = PositionChange(
                            wallet_address=address,
                            wallet_name=wallet.name,
                            coin=coin,
                            change_type=change_type,
                            old_size=old_size,
                            new_size=new_size,
                            old_entry_price=old_pos.entry_price if old_pos else 0,
                            new_entry_price=new_pos.entry_price if new_pos else 0,
                        )
                        changes.append(change)

                        if self.on_change:
                            try:
                                result = self.on_change(change)
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception as e:
                                logger.error(f"Error in change callback: {e}")

                # Update stored positions
                self._positions[address] = new_positions

            except Exception as e:
                logger.error(f"Failed to sync positions for {wallet.name}: {e}")

        return changes
