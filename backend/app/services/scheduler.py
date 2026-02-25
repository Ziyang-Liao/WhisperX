"""Transcription Scheduler service using APScheduler for cron-based batch triggering."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.batch_processor import BatchProcessor

logger = logging.getLogger(__name__)

SCHEDULER_JOB_ID = "batch_transcription"


class TranscriptionScheduler:
    """Cron-based scheduler that triggers batch transcription tasks."""

    def __init__(self, cron_expression: str, batch_processor: BatchProcessor):
        self.batch_processor = batch_processor
        self.cron_expression = cron_expression
        self._scheduler = BackgroundScheduler()
        self._add_job(cron_expression)

    def _add_job(self, cron_expression: str) -> None:
        """Add or replace the cron job."""
        trigger = CronTrigger.from_crontab(cron_expression)
        self._scheduler.add_job(
            self._run_batch,
            trigger=trigger,
            id=SCHEDULER_JOB_ID,
            replace_existing=True,
        )

    def _run_batch(self) -> None:
        """Callback invoked by the scheduler. Runs batch transcription."""
        try:
            logger.info("Scheduled batch transcription triggered")
            self.batch_processor.run_batch(file_ids=None, trigger_type="scheduled")
        except PermissionError:
            logger.warning("Skipping scheduled run: a batch task is already running")
        except Exception:
            logger.exception("Scheduled batch transcription failed")

    def start(self) -> None:
        """Start the scheduler."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Transcription scheduler started (cron: %s)", self.cron_expression)

    def stop(self) -> None:
        """Stop the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Transcription scheduler stopped")

    def update_schedule(self, cron_expression: str) -> None:
        """Update the cron expression for the scheduled job."""
        self.cron_expression = cron_expression
        self._scheduler.reschedule_job(
            SCHEDULER_JOB_ID,
            trigger=CronTrigger.from_crontab(cron_expression),
        )
        logger.info("Scheduler updated to cron: %s", cron_expression)
