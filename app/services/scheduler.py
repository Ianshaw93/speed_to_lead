"""Scheduler service for snooze reminders and scheduled reports using APScheduler."""

import uuid
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger


def calculate_snooze_time(duration: str) -> datetime:
    """Calculate the snooze end time based on duration string.

    Args:
        duration: Duration string ('1h', '4h', 'tomorrow').

    Returns:
        Datetime when the snooze ends.

    Raises:
        ValueError: If duration is not recognized.
    """
    now = datetime.now(timezone.utc)

    if duration == "1h":
        return now + timedelta(hours=1)
    elif duration == "4h":
        return now + timedelta(hours=4)
    elif duration == "tomorrow":
        # Tomorrow at 9am UTC
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Unknown snooze duration: {duration}")


async def send_snooze_reminder(draft_id: uuid.UUID) -> None:
    """Send a reminder for a snoozed draft.

    This function is called by the scheduler when a snooze period ends.

    Args:
        draft_id: The ID of the draft to remind about.
    """
    # Import here to avoid circular imports
    from app.services.slack import get_slack_bot

    bot = get_slack_bot()
    await bot.send_confirmation(
        f"⏰ Reminder: You have a snoozed draft waiting for your attention!"
    )
    # TODO: Re-send the draft notification with updated buttons


async def send_daily_report_task() -> None:
    """Send daily metrics report. Called by scheduler at 9am UK time."""
    import logging
    from app.database import async_session_factory
    from app.services.reports import get_daily_dashboard_metrics
    from app.services.slack import get_slack_bot

    logger = logging.getLogger(__name__)

    try:
        # Get yesterday's metrics (report covers previous day)
        yesterday = date.today() - timedelta(days=1)

        async with async_session_factory() as session:
            metrics = await get_daily_dashboard_metrics(session, yesterday)

        bot = get_slack_bot()
        await bot.send_daily_report(yesterday, metrics)
        logger.info(f"Daily report sent for {yesterday}")

    except Exception as e:
        logger.error(f"Failed to send daily report: {e}", exc_info=True)


async def check_engagement_task() -> None:
    """Check for new LinkedIn posts from watched profiles. Called by scheduler."""
    import logging
    from app.services.engagement import check_engagement_posts

    logger = logging.getLogger(__name__)

    try:
        result = await check_engagement_posts()
        logger.info(f"Engagement check completed: {result}")
    except Exception as e:
        logger.error(f"Failed to run engagement check: {e}", exc_info=True)


async def send_weekly_report_task() -> None:
    """Send weekly metrics report. Called by scheduler on Monday 9am UK time."""
    import logging
    from app.database import async_session_factory
    from app.services.reports import get_weekly_dashboard_metrics
    from app.services.slack import get_slack_bot

    logger = logging.getLogger(__name__)

    try:
        # Get previous week's metrics (Monday to Sunday)
        today = date.today()
        # Today is Monday, so previous week is 7-13 days ago
        end_of_week = today - timedelta(days=1)  # Sunday
        start_of_week = end_of_week - timedelta(days=6)  # Monday

        async with async_session_factory() as session:
            metrics = await get_weekly_dashboard_metrics(session, start_of_week, end_of_week)

        bot = get_slack_bot()
        await bot.send_weekly_report(start_of_week, end_of_week, metrics)
        logger.info(f"Weekly report sent for {start_of_week} to {end_of_week}")

    except Exception as e:
        logger.error(f"Failed to send weekly report: {e}", exc_info=True)


class SchedulerService:
    """Service for managing scheduled tasks."""

    def __init__(self):
        """Initialize the scheduler service."""
        self._scheduler = AsyncIOScheduler()
        self._jobs: dict[uuid.UUID, str] = {}  # draft_id -> job_id mapping

    def start(self) -> None:
        """Start the scheduler and register recurring jobs."""
        if not self._scheduler.running:
            self._register_report_jobs()
            self._scheduler.start()

    def _register_report_jobs(self) -> None:
        """Register daily and weekly report jobs."""
        # Daily report at 9am UK time (Europe/London handles DST)
        self._scheduler.add_job(
            send_daily_report_task,
            trigger=CronTrigger(
                hour=9,
                minute=0,
                timezone='Europe/London',
            ),
            id='daily_report',
            name='Daily metrics report',
            replace_existing=True,
            misfire_grace_time=3600,  # 1 hour grace
        )

        # Engagement check at 8am, 12pm, 4pm, 8pm UK time
        self._scheduler.add_job(
            check_engagement_task,
            trigger=CronTrigger(
                hour='8,12,16,20',
                minute=0,
                timezone='Europe/London',
            ),
            id='engagement_check',
            name='LinkedIn engagement check',
            replace_existing=True,
            misfire_grace_time=3600,  # 1 hour grace
        )

        # Weekly report on Monday 9am UK time
        self._scheduler.add_job(
            send_weekly_report_task,
            trigger=CronTrigger(
                day_of_week='mon',
                hour=9,
                minute=0,
                timezone='Europe/London',
            ),
            id='weekly_report',
            name='Weekly metrics report',
            replace_existing=True,
            misfire_grace_time=3600,  # 1 hour grace
        )

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler.

        Args:
            wait: Whether to wait for running jobs to complete.
        """
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    def add_snooze_reminder(
        self,
        draft_id: uuid.UUID,
        run_time: datetime,
    ) -> str:
        """Schedule a snooze reminder.

        Args:
            draft_id: The draft ID to remind about.
            run_time: When to send the reminder.

        Returns:
            The job ID.
        """
        job_id = f"snooze_{draft_id}"

        # Remove existing job if any
        self.cancel_snooze_reminder(draft_id)

        # Add new job
        self._scheduler.add_job(
            send_snooze_reminder,
            trigger=DateTrigger(run_date=run_time),
            args=[draft_id],
            id=job_id,
            name=f"Snooze reminder for draft {draft_id}",
            misfire_grace_time=300,  # 5 minutes grace time
        )

        self._jobs[draft_id] = job_id
        return job_id

    def cancel_snooze_reminder(self, draft_id: uuid.UUID) -> bool:
        """Cancel a scheduled snooze reminder.

        Args:
            draft_id: The draft ID to cancel the reminder for.

        Returns:
            True if a job was cancelled, False if no job existed.
        """
        job_id = self._jobs.pop(draft_id, None)
        if job_id:
            try:
                self._scheduler.remove_job(job_id)
                return True
            except Exception:
                # Job may not exist
                pass
        return False

    def get_job_info(self, draft_id: uuid.UUID) -> dict | None:
        """Get information about a scheduled job.

        Args:
            draft_id: The draft ID to get job info for.

        Returns:
            Job information dict or None if no job exists.
        """
        job_id = self._jobs.get(draft_id)
        if job_id:
            job = self._scheduler.get_job(job_id)
            if job:
                return {
                    "id": job.id,
                    "name": job.name,
                    "next_run_time": job.next_run_time,
                }
        return None


# Global scheduler instance
_scheduler: SchedulerService | None = None


def get_scheduler_service() -> SchedulerService:
    """Get or create the scheduler service singleton."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerService()
    return _scheduler


async def schedule_snooze_reminder(
    draft_id: uuid.UUID,
    duration: str,
) -> datetime:
    """Convenience function to schedule a snooze reminder.

    Args:
        draft_id: The draft ID to remind about.
        duration: Duration string ('1h', '4h', 'tomorrow').

    Returns:
        The scheduled reminder time.
    """
    run_time = calculate_snooze_time(duration)
    scheduler = get_scheduler_service()
    scheduler.add_snooze_reminder(draft_id, run_time)
    return run_time
