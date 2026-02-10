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
        """Insert a daily analysis section after the header of a wallet's note file.

        New entries are placed right after the header so the latest date appears first.
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

        # Insert new section after the header separator so newest entries come first
        content = note_path.read_text(encoding="utf-8")
        separator = "---\n"
        idx = content.find(separator)
        if idx != -1:
            insert_pos = idx + len(separator)
            new_content = content[:insert_pos] + section + content[insert_pos:]
        else:
            new_content = content + section
        note_path.write_text(new_content, encoding="utf-8")

        logger.info(f"Inserted analysis into {note_path.name} for {date_str}")
