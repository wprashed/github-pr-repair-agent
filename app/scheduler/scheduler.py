"""APScheduler background job runner."""

import asyncio
import logging
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config.settings import settings
from app.monitoring.scanner import PRRepairScanner

logger = logging.getLogger(__name__)


class RepairScheduler:
    """Manages scheduled background PR scans using APScheduler."""

    def __init__(self, scanner: Optional[PRRepairScanner] = None):
        self.scanner = scanner or PRRepairScanner()
        self.scheduler = AsyncIOScheduler()
        self._is_running = False

    def start(self, interval_hours: Optional[float] = None) -> None:
        """Start the background scheduler."""
        hours = interval_hours or settings.SCHEDULER_INTERVAL_HOURS
        seconds = max(60, int(hours * 3600))

        self.scheduler.add_job(
            self.run_scheduled_scan,
            trigger=IntervalTrigger(seconds=seconds),
            id="pr_repair_scan",
            name="Periodic PR Scan and Repair",
            replace_existing=True,
        )
        self.scheduler.start()
        self._is_running = True
        logger.info("Scheduler started with interval: %.2f hour(s) (%d seconds)", hours, seconds)

    def stop(self) -> None:
        """Stop the background scheduler."""
        if self._is_running:
            self.scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("Scheduler stopped.")

    async def run_scheduled_scan(self) -> None:
        """Scheduled task handler."""
        logger.info("Triggering scheduled scan across all repositories.")
        try:
            await self.scanner.scan_all_repositories()
        except Exception as exc:
            logger.exception("Error during scheduled scan: %s", exc)

    async def trigger_manual_scan(self) -> None:
        """Trigger an immediate scan asynchronously."""
        logger.info("Triggering manual scan now.")
        asyncio.create_task(self.scanner.scan_all_repositories())


scheduler_service = RepairScheduler()
