"""SQLite database for storing positions and fills."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite

from tracker.core.models import Position, Fill, PositionChange

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Connect to database and create tables."""
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        await self._create_tables()
        logger.info(f"Database connected: {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        await self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                coin TEXT NOT NULL,
                size REAL NOT NULL,
                entry_price REAL NOT NULL,
                unrealized_pnl REAL,
                leverage REAL,
                liquidation_price REAL,
                margin_used REAL,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_positions_wallet
                ON positions(wallet_address);
            CREATE INDEX IF NOT EXISTS idx_positions_coin
                ON positions(wallet_address, coin);

            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                coin TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                time TEXT NOT NULL,
                fee REAL,
                fee_token TEXT,
                oid INTEGER,
                tid INTEGER UNIQUE,
                crossed INTEGER,
                hash TEXT,
                start_position REAL,
                closed_pnl REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_fills_wallet
                ON fills(wallet_address);
            CREATE INDEX IF NOT EXISTS idx_fills_time
                ON fills(wallet_address, time);

            CREATE TABLE IF NOT EXISTS position_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                wallet_name TEXT NOT NULL,
                coin TEXT NOT NULL,
                change_type TEXT NOT NULL,
                old_size REAL NOT NULL,
                new_size REAL NOT NULL,
                old_entry_price REAL,
                new_entry_price REAL,
                fill_tid INTEGER,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_changes_wallet
                ON position_changes(wallet_address);
            CREATE INDEX IF NOT EXISTS idx_changes_time
                ON position_changes(timestamp);
        """)
        await self._connection.commit()

    async def save_position(self, position: Position) -> int:
        """Save a position snapshot to database."""
        cursor = await self._connection.execute(
            """
            INSERT INTO positions (
                wallet_address, coin, size, entry_price, unrealized_pnl,
                leverage, liquidation_price, margin_used, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.wallet_address,
                position.coin,
                position.size,
                position.entry_price,
                position.unrealized_pnl,
                position.leverage,
                position.liquidation_price,
                position.margin_used,
                position.timestamp.isoformat(),
            ),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def save_fill(self, fill: Fill) -> Optional[int]:
        """
        Save a fill to database.

        Returns None if fill already exists (duplicate tid).
        """
        try:
            cursor = await self._connection.execute(
                """
                INSERT INTO fills (
                    wallet_address, coin, side, price, size, time,
                    fee, fee_token, oid, tid, crossed, hash,
                    start_position, closed_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.wallet_address,
                    fill.coin,
                    fill.side,
                    fill.price,
                    fill.size,
                    fill.time.isoformat(),
                    fill.fee,
                    fill.fee_token,
                    fill.oid,
                    fill.tid,
                    1 if fill.crossed else 0,
                    fill.hash,
                    fill.start_position,
                    fill.closed_pnl,
                ),
            )
            await self._connection.commit()
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            # Duplicate tid
            return None

    async def save_position_change(self, change: PositionChange) -> int:
        """Save a position change record."""
        cursor = await self._connection.execute(
            """
            INSERT INTO position_changes (
                wallet_address, wallet_name, coin, change_type,
                old_size, new_size, old_entry_price, new_entry_price,
                fill_tid, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change.wallet_address,
                change.wallet_name,
                change.coin,
                change.change_type.value,
                change.old_size,
                change.new_size,
                change.old_entry_price,
                change.new_entry_price,
                change.fill.tid if change.fill else None,
                change.timestamp.isoformat(),
            ),
        )
        await self._connection.commit()
        return cursor.lastrowid

    async def get_latest_fill_time(self, wallet_address: str) -> Optional[datetime]:
        """Get the timestamp of the most recent fill for a wallet."""
        cursor = await self._connection.execute(
            """
            SELECT time FROM fills
            WHERE wallet_address = ?
            ORDER BY time DESC
            LIMIT 1
            """,
            (wallet_address.lower(),),
        )
        row = await cursor.fetchone()

        if row:
            return datetime.fromisoformat(row["time"])
        return None

    async def fill_exists(self, tid: int) -> bool:
        """Check if a fill with the given tid already exists."""
        cursor = await self._connection.execute(
            "SELECT 1 FROM fills WHERE tid = ?",
            (tid,),
        )
        return await cursor.fetchone() is not None

    async def get_position_changes(
        self,
        wallet_address: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get position change history."""
        query = "SELECT * FROM position_changes WHERE 1=1"
        params = []

        if wallet_address:
            query += " AND wallet_address = ?"
            params.append(wallet_address.lower())

        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def get_fills(
        self,
        wallet_address: Optional[str] = None,
        coin: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get fill history."""
        query = "SELECT * FROM fills WHERE 1=1"
        params = []

        if wallet_address:
            query += " AND wallet_address = ?"
            params.append(wallet_address.lower())

        if coin:
            query += " AND coin = ?"
            params.append(coin)

        if since:
            query += " AND time >= ?"
            params.append(since.isoformat())

        query += " ORDER BY time DESC LIMIT ?"
        params.append(limit)

        cursor = await self._connection.execute(query, params)
        rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
