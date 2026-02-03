"""Data models for positions and fills."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ChangeType(Enum):
    """Type of position change."""
    OPEN = "open"           # New position opened
    CLOSE = "close"         # Position fully closed
    INCREASE = "increase"   # Position size increased
    DECREASE = "decrease"   # Position size decreased
    FLIP = "flip"           # Position flipped (long to short or vice versa)


@dataclass
class Position:
    """Represents a trading position."""
    wallet_address: str
    coin: str
    size: float              # Positive for long, negative for short
    entry_price: float
    unrealized_pnl: float
    leverage: float
    liquidation_price: Optional[float] = None
    margin_used: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def side(self) -> str:
        """Return 'long' or 'short' based on position size."""
        return "long" if self.size > 0 else "short"

    @property
    def notional_value(self) -> float:
        """Return the notional value of the position."""
        return abs(self.size) * self.entry_price

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "wallet_address": self.wallet_address,
            "coin": self.coin,
            "size": self.size,
            "entry_price": self.entry_price,
            "unrealized_pnl": self.unrealized_pnl,
            "leverage": self.leverage,
            "liquidation_price": self.liquidation_price,
            "margin_used": self.margin_used,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_api(cls, wallet_address: str, data: dict) -> "Position":
        """Create Position from Hyperliquid API response."""
        position = data.get("position", {})
        return cls(
            wallet_address=wallet_address.lower(),
            coin=position.get("coin", ""),
            size=float(position.get("szi", 0)),
            entry_price=float(position.get("entryPx", 0)),
            unrealized_pnl=float(position.get("unrealizedPnl", 0)),
            leverage=float(position.get("leverage", {}).get("value", 1)),
            liquidation_price=float(liq) if (liq := position.get("liquidationPx")) else None,
            margin_used=float(position.get("marginUsed", 0)),
        )


@dataclass
class Fill:
    """Represents a trade fill."""
    wallet_address: str
    coin: str
    side: str                # "B" for buy, "A" for sell (ask)
    price: float
    size: float
    time: datetime
    fee: float
    fee_token: str
    oid: int                 # Order ID
    tid: int                 # Trade ID
    crossed: bool            # True if taker order
    hash: str                # Transaction hash
    start_position: float    # Position before fill
    closed_pnl: float        # Realized PnL from this fill

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "wallet_address": self.wallet_address,
            "coin": self.coin,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "time": self.time.isoformat(),
            "fee": self.fee,
            "fee_token": self.fee_token,
            "oid": self.oid,
            "tid": self.tid,
            "crossed": self.crossed,
            "hash": self.hash,
            "start_position": self.start_position,
            "closed_pnl": self.closed_pnl,
        }

    @classmethod
    def from_api(cls, wallet_address: str, data: dict) -> "Fill":
        """Create Fill from Hyperliquid API response."""
        return cls(
            wallet_address=wallet_address.lower(),
            coin=data.get("coin", ""),
            side=data.get("side", ""),
            price=float(data.get("px", 0)),
            size=float(data.get("sz", 0)),
            time=datetime.fromtimestamp(data.get("time", 0) / 1000),
            fee=float(data.get("fee", 0)),
            fee_token=data.get("feeToken", "USDC"),
            oid=int(data.get("oid", 0)),
            tid=int(data.get("tid", 0)),
            crossed=data.get("crossed", False),
            hash=data.get("hash", ""),
            start_position=float(data.get("startPosition", 0)),
            closed_pnl=float(data.get("closedPnl", 0)),
        )


@dataclass
class PositionChange:
    """Represents a change in position state."""
    wallet_address: str
    wallet_name: str
    coin: str
    change_type: ChangeType
    old_size: float
    new_size: float
    old_entry_price: float
    new_entry_price: float
    fill: Optional[Fill] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def size_change(self) -> float:
        """Return the change in position size."""
        return self.new_size - self.old_size

    @property
    def old_side(self) -> str:
        """Return old position side."""
        if self.old_size == 0:
            return "none"
        return "long" if self.old_size > 0 else "short"

    @property
    def new_side(self) -> str:
        """Return new position side."""
        if self.new_size == 0:
            return "none"
        return "long" if self.new_size > 0 else "short"

    def format_message(self) -> str:
        """Format human-readable message about the change."""
        emoji = {
            ChangeType.OPEN: "🟢",
            ChangeType.CLOSE: "🔴",
            ChangeType.INCREASE: "⬆️",
            ChangeType.DECREASE: "⬇️",
            ChangeType.FLIP: "🔄",
        }[self.change_type]

        side = self.new_side.upper() if self.new_size != 0 else self.old_side.upper()

        if self.change_type == ChangeType.OPEN:
            return (
                f"{emoji} [{self.wallet_name}] OPENED {side} {self.coin}\n"
                f"Size: {abs(self.new_size):.4f} @ ${self.new_entry_price:.2f}"
            )
        elif self.change_type == ChangeType.CLOSE:
            pnl = self.fill.closed_pnl if self.fill else 0
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            return (
                f"{emoji} [{self.wallet_name}] CLOSED {self.old_side.upper()} {self.coin}\n"
                f"Closed Size: {abs(self.old_size):.4f} | PnL: {pnl_str}"
            )
        elif self.change_type == ChangeType.INCREASE:
            return (
                f"{emoji} [{self.wallet_name}] INCREASED {side} {self.coin}\n"
                f"Size: {abs(self.old_size):.4f} → {abs(self.new_size):.4f} @ ${self.new_entry_price:.2f}"
            )
        elif self.change_type == ChangeType.DECREASE:
            pnl = self.fill.closed_pnl if self.fill else 0
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            return (
                f"{emoji} [{self.wallet_name}] DECREASED {side} {self.coin}\n"
                f"Size: {abs(self.old_size):.4f} → {abs(self.new_size):.4f} | PnL: {pnl_str}"
            )
        elif self.change_type == ChangeType.FLIP:
            return (
                f"{emoji} [{self.wallet_name}] FLIPPED {self.old_side.upper()} → {self.new_side.upper()} {self.coin}\n"
                f"Size: {abs(self.old_size):.4f} → {abs(self.new_size):.4f} @ ${self.new_entry_price:.2f}"
            )
        return f"Position change: {self.change_type.value}"
