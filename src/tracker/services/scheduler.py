"""Scheduler for periodic tasks like daily summary."""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Callable, Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class Scheduler:
    """Async scheduler for periodic tasks."""

    def __init__(self, timezone: str = "Asia/Shanghai"):
        self.timezone = ZoneInfo(timezone)
        self._tasks: list[asyncio.Task] = []
        self._running = False

    def _get_next_run_time(self, target_time: time) -> datetime:
        """Calculate next run time for a daily task."""
        now = datetime.now(self.timezone)
        target = datetime.combine(now.date(), target_time, tzinfo=self.timezone)

        # If target time has passed today, schedule for tomorrow
        if target <= now:
            target += timedelta(days=1)

        return target

    async def schedule_daily(
        self,
        target_time: str,
        callback: Callable[[], Any],
        name: str = "daily_task",
    ) -> None:
        """
        Schedule a task to run daily at a specific time.

        Args:
            target_time: Time string in "HH:MM" format
            callback: Async function to call
            name: Task name for logging
        """
        # Parse target time
        hour, minute = map(int, target_time.split(":"))
        daily_time = time(hour, minute)

        self._running = True

        while self._running:
            next_run = self._get_next_run_time(daily_time)
            wait_seconds = (next_run - datetime.now(self.timezone)).total_seconds()

            logger.info(
                f"[{name}] Next run at {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                f"(in {wait_seconds / 3600:.1f} hours)"
            )

            try:
                await asyncio.sleep(wait_seconds)

                if not self._running:
                    break

                logger.info(f"[{name}] Running scheduled task...")

                try:
                    result = callback()
                    if asyncio.iscoroutine(result):
                        await result
                    logger.info(f"[{name}] Task completed successfully")
                except Exception as e:
                    logger.error(f"[{name}] Task failed: {e}")

            except asyncio.CancelledError:
                logger.info(f"[{name}] Task cancelled")
                break

    def start_daily_task(
        self,
        target_time: str,
        callback: Callable[[], Any],
        name: str = "daily_task",
    ) -> asyncio.Task:
        """
        Start a daily scheduled task in the background.

        Returns the asyncio Task for management.
        """
        task = asyncio.create_task(
            self.schedule_daily(target_time, callback, name)
        )
        self._tasks.append(task)
        return task

    async def stop(self) -> None:
        """Stop all scheduled tasks."""
        self._running = False

        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._tasks.clear()
        logger.info("Scheduler stopped")


class IntervalScheduler:
    """Scheduler for interval-based tasks (e.g., periodic sync)."""

    def __init__(self):
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def schedule_interval(
        self,
        interval_seconds: int,
        callback: Callable[[], Any],
        name: str = "interval_task",
        run_immediately: bool = False,
    ) -> None:
        """
        Schedule a task to run at regular intervals.

        Args:
            interval_seconds: Seconds between runs
            callback: Async function to call
            name: Task name for logging
            run_immediately: If True, run callback before first wait
        """
        self._running = True

        if run_immediately:
            try:
                result = callback()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"[{name}] Initial run failed: {e}")

        while self._running:
            try:
                await asyncio.sleep(interval_seconds)

                if not self._running:
                    break

                try:
                    result = callback()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"[{name}] Task failed: {e}")

            except asyncio.CancelledError:
                break

    def start_interval_task(
        self,
        interval_seconds: int,
        callback: Callable[[], Any],
        name: str = "interval_task",
        run_immediately: bool = False,
    ) -> asyncio.Task:
        """Start an interval task in the background."""
        task = asyncio.create_task(
            self.schedule_interval(interval_seconds, callback, name, run_immediately)
        )
        self._tasks.append(task)
        return task

    async def stop(self) -> None:
        """Stop all interval tasks."""
        self._running = False

        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._tasks.clear()
