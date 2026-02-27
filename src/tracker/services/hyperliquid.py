"""Hyperliquid REST API client."""

import logging
from typing import Optional

import aiohttp

from tracker.core.models import Position, Fill

logger = logging.getLogger(__name__)


class HyperliquidClient:
    """Async client for Hyperliquid REST API."""

    def __init__(self, base_url: str = "https://api.hyperliquid.xyz"):
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post(self, endpoint: str, data: dict) -> dict:
        """Make POST request to API."""
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        async with session.post(url, json=data) as response:
            response.raise_for_status()
            return await response.json()

    async def get_clearinghouse_state(
        self, address: str, dex: str = ""
    ) -> dict:
        """
        Get current positions and account state for an address.

        Args:
            address: Wallet address
            dex: Perp dex name for HIP-3 builder-deployed markets.
                 Empty string for the default perp dex.

        Returns:
            dict with 'assetPositions' and 'marginSummary'
        """
        data = {
            "type": "clearinghouseState",
            "user": address.lower(),
        }
        if dex:
            data["dex"] = dex
        result = await self._post("/info", data)
        logger.debug(
            f"Clearinghouse state for {address[:10]}... "
            f"(dex={dex!r}): "
            f"{len(result.get('assetPositions', []))} positions"
        )
        return result

    async def get_positions(
        self, address: str, dex: str = ""
    ) -> list[Position]:
        """
        Get current open positions for an address.

        Args:
            address: Wallet address
            dex: Perp dex name for HIP-3 builder-deployed markets.

        Returns:
            List of Position objects
        """
        state = await self.get_clearinghouse_state(address, dex=dex)
        positions = []

        for asset_pos in state.get("assetPositions", []):
            position = Position.from_api(address, asset_pos)
            if position.size != 0:  # Only include non-zero positions
                positions.append(position)

        return positions

    async def get_user_fills(
        self,
        address: str,
        start_time: Optional[int] = None,
        limit: int = 100,
    ) -> list[Fill]:
        """
        Get trade history for an address.

        Args:
            address: Wallet address
            start_time: Optional start timestamp in ms
            limit: Max number of fills to return

        Returns:
            List of Fill objects
        """
        data = {
            "type": "userFills",
            "user": address.lower(),
        }

        if start_time:
            data["startTime"] = start_time

        result = await self._post("/info", data)

        fills = []
        for fill_data in result[:limit]:
            fill = Fill.from_api(address, fill_data)
            fills.append(fill)

        logger.debug(f"Got {len(fills)} fills for {address[:10]}...")
        return fills

    async def get_meta(self) -> dict:
        """Get exchange metadata (coins, decimals, etc)."""
        data = {"type": "meta"}
        return await self._post("/info", data)

    async def get_all_mids(self) -> dict[str, float]:
        """
        Get current mid prices for all coins.

        Returns:
            Dict mapping coin name to mid price
        """
        data = {"type": "allMids"}
        result = await self._post("/info", data)
        return {k: float(v) for k, v in result.items()}

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
