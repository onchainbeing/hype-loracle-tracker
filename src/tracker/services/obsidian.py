"""Obsidian vault writer for trading analysis notes."""

import logging
from datetime import date
from pathlib import Path

from tracker.core.config import ObsidianConfig

logger = logging.getLogger(__name__)

# Chinese weekday names
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class ObsidianWriter:
    """Writes trading analysis notes to Obsidian vault."""

    def __init__(self, config: ObsidianConfig):
        self.config = config
        self._notes_dir: Path = None

    def initialize(self) -> None:
        """Ensure vault and notes directory exist."""
        if not self.config.enabled:
            logger.info("Obsidian writer disabled")
            return

        if not self.config.vault_path:
            logger.warning("Obsidian vault_path not set, writer disabled")
            self.config.enabled = False
            return

        self._notes_dir = Path(self.config.vault_path) / self.config.notes_folder
        self._notes_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Obsidian writer initialized: {self._notes_dir}")

    def append_analysis(self, wallet_name: str, today: date, analysis: str) -> None:
        """Append a daily analysis section to a wallet's note file.

        Creates the note file with header if it doesn't exist.
        """
        if not self.config.enabled or not self._notes_dir:
            return

        note_path = self._notes_dir / f"{wallet_name}.md"

        # Create file with header if it doesn't exist
        if not note_path.exists():
            header = (
                f"# {wallet_name} - 交易分析日志\n"
                f"\n"
                f"> 由 Hyperliquid Smart Money Tracker 自动生成\n"
                f"\n"
                f"---\n"
            )
            note_path.write_text(header, encoding="utf-8")
            logger.info(f"Created new note: {note_path}")

        # Format date heading with Chinese weekday
        weekday = WEEKDAYS[today.weekday()]
        date_str = today.strftime("%Y-%m-%d")

        section = f"\n## {date_str} {weekday}\n\n{analysis}\n\n---\n"

        with open(note_path, "a", encoding="utf-8") as f:
            f.write(section)

        logger.info(f"Appended analysis to {note_path.name} for {date_str}")
