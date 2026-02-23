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


async def process_buying_signals_task() -> None:
    """Process unprocessed buying signal prospects. Runs daily at 7am EST."""
    import logging
    from app.services.buying_signal_outreach import process_buying_signal_batch
    from app.services.slack import get_slack_bot

    logger = logging.getLogger(__name__)

    try:
        result = await process_buying_signal_batch()

        # Send Slack summary
        bot = get_slack_bot()
        summary = (
            f"*Buying Signal Outreach Batch Complete*\n"
            f"- Prospects processed: {result['processed']}\n"
            f"- Messages generated: {result['messages_generated']}\n"
            f"- Uploaded to HeyReach: {result['uploaded']}\n"
            f"- Errors: {result['errors']}"
        )
        await bot.send_confirmation(summary)
        logger.info(f"Buying signal batch completed: {result}")

    except Exception as e:
        logger.error(f"Failed to process buying signals: {e}", exc_info=True)
        try:
            bot = get_slack_bot()
            await bot.send_confirmation(f"*Buying Signal Outreach FAILED*\nError: {e}")
        except Exception:
            pass


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


async def run_daily_learning_task() -> None:
    """Analyze human edits from the last 24h. Called daily at 2am UK time."""
    import logging
    from app.services.learning_agent import run_daily_learning

    logger = logging.getLogger(__name__)

    try:
        result = await run_daily_learning()
        logger.info(f"Daily learning completed: {result}")
    except Exception as e:
        logger.error(f"Failed to run daily learning: {e}", exc_info=True)
        try:
            from app.services.slack import get_slack_bot
            bot = get_slack_bot()
            await bot.send_confirmation(f"*Daily Learning FAILED*\nError: {e}")
        except Exception:
            pass


async def run_weekly_consolidation_task() -> None:
    """Consolidate learnings into QA guidelines. Called Saturday 3am UK time."""
    import logging
    from app.services.learning_agent import run_weekly_consolidation

    logger = logging.getLogger(__name__)

    try:
        result = await run_weekly_consolidation()
        logger.info(f"Weekly consolidation completed: {result}")
    except Exception as e:
        logger.error(f"Failed to run weekly consolidation: {e}", exc_info=True)
        try:
            from app.services.slack import get_slack_bot
            bot = get_slack_bot()
            await bot.send_confirmation(f"*Weekly Consolidation FAILED*\nError: {e}")
        except Exception:
            pass


async def run_trend_scout_scheduled_task() -> None:
    """Run trend scout discovery. Called by scheduler on Saturday 7am UK time."""
    import logging
    from app.services.trend_scout import run_trend_scout_task

    logger = logging.getLogger(__name__)

    try:
        result = await run_trend_scout_task()
        logger.info(f"Trend scout completed: {result['topics_saved']} topics saved (batch={result['batch_id']})")
    except Exception as e:
        logger.error(f"Failed to run trend scout: {e}", exc_info=True)
        try:
            from app.services.slack import get_slack_bot
            bot = get_slack_bot()
            await bot.send_confirmation(f"*Trend Scout FAILED*\nError: {e}")
        except Exception:
            pass


async def run_health_check_task() -> None:
    """Run production health checks. Called by scheduler at 10am and 3pm UK time."""
    import logging
    from app.database import async_session_factory
    from app.services.health_check import CheckStatus, run_health_check

    logger = logging.getLogger(__name__)

    try:
        async with async_session_factory() as session:
            report = await run_health_check(session)

        logger.info(
            f"Health check completed: {report.status.value} "
            f"({report.passing}/{len(report.checks)} passing)"
        )

        if report.status != CheckStatus.OK:
            from app.services.slack import get_slack_bot
            bot = get_slack_bot()
            await bot.send_health_check_alert(report)
            logger.info("Health check alert sent to Slack")

    except Exception as e:
        logger.error(f"Failed to run health check: {e}", exc_info=True)


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


async def send_scheduled_pitched_message(
    prospect_id: uuid.UUID,
    message_text: str,
) -> None:
    """Send a scheduled message from the pitched channel.

    Called by the scheduler at the specified time.

    Args:
        prospect_id: The prospect to send the message to.
        message_text: The message content.
    """
    import logging
    from app.database import async_session_factory
    from app.models import Conversation, MessageDirection, MessageLog, Prospect
    from app.services.heyreach import get_heyreach_client, HeyReachError
    from app.services.slack import get_slack_bot
    from sqlalchemy import func, select

    logger = logging.getLogger(__name__)

    try:
        async with async_session_factory() as session:
            # Load prospect and linked conversation
            result = await session.execute(
                select(Prospect).where(Prospect.id == prospect_id)
            )
            prospect = result.scalar_one_or_none()

            if not prospect:
                logger.error(f"Prospect {prospect_id} not found")
                bot = get_slack_bot()
                await bot.send_confirmation(
                    f"Scheduled message failed: prospect not found."
                )
                return

            # Auto-link conversation if missing
            if not prospect.conversation_id and prospect.linkedin_url:
                conv_search = await session.execute(
                    select(Conversation).where(
                        func.lower(Conversation.linkedin_profile_url)
                        == prospect.linkedin_url.lower().strip().rstrip("/")
                    )
                )
                found_conv = conv_search.scalar_one_or_none()
                if found_conv:
                    prospect.conversation_id = found_conv.id
                    await session.commit()
                    logger.info(
                        f"Auto-linked conversation {found_conv.id} to prospect {prospect_id}"
                    )

            if not prospect.conversation_id:
                logger.error(
                    f"Prospect {prospect.full_name} ({prospect_id}) has no conversation"
                )
                bot = get_slack_bot()
                await bot.send_confirmation(
                    f"Scheduled message failed for {prospect.full_name or 'prospect'}: "
                    "no HeyReach conversation found. They may not have replied yet."
                )
                return

            conv_result = await session.execute(
                select(Conversation).where(Conversation.id == prospect.conversation_id)
            )
            conversation = conv_result.scalar_one_or_none()

            if not conversation or not conversation.linkedin_account_id:
                logger.error(f"Conversation missing or no linkedin_account_id for prospect {prospect_id}")
                bot = get_slack_bot()
                await bot.send_confirmation(
                    f"Scheduled message failed: missing LinkedIn account ID."
                )
                return

            # Send via HeyReach
            heyreach = get_heyreach_client()
            await heyreach.send_message(
                conversation_id=conversation.heyreach_lead_id,
                linkedin_account_id=conversation.linkedin_account_id,
                message=message_text,
            )

            # Log outbound message
            message_log = MessageLog(
                conversation_id=conversation.id,
                direction=MessageDirection.OUTBOUND,
                content=message_text,
            )
            session.add(message_log)
            await session.commit()

            bot = get_slack_bot()
            await bot.send_confirmation(
                f"Scheduled message sent to {prospect.full_name or 'prospect'}."
            )

            logger.info(f"Sent scheduled message to prospect {prospect_id}")

    except HeyReachError as e:
        logger.error(f"HeyReach error sending scheduled message: {e}")
        bot = get_slack_bot()
        await bot.send_confirmation(f"Scheduled message failed: {e}")
    except Exception as e:
        logger.error(f"Error sending scheduled message: {e}", exc_info=True)


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

        # Buying signal outreach at 7am EST daily
        self._scheduler.add_job(
            process_buying_signals_task,
            trigger=CronTrigger(
                hour=7,
                minute=0,
                timezone='US/Eastern',
            ),
            id='buying_signal_outreach',
            name='Buying signal outreach batch',
            replace_existing=True,
            misfire_grace_time=3600,
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

        # Health check at 10am and 3pm UK time
        self._scheduler.add_job(
            run_health_check_task,
            trigger=CronTrigger(
                hour='10,15',
                minute=0,
                timezone='Europe/London',
            ),
            id='health_check',
            name='Production health check',
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Trend scout on Saturday 7am UK time
        self._scheduler.add_job(
            run_trend_scout_scheduled_task,
            trigger=CronTrigger(
                day_of_week='sat',
                hour=7,
                minute=0,
                timezone='Europe/London',
            ),
            id='trend_scout_weekly',
            name='Weekly trend scout scan',
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Daily learning analysis at 2am UK time
        self._scheduler.add_job(
            run_daily_learning_task,
            trigger=CronTrigger(
                hour=2,
                minute=0,
                timezone='Europe/London',
            ),
            id='daily_learning',
            name='Daily QA learning analysis',
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Weekly consolidation on Saturday 3am UK time
        self._scheduler.add_job(
            run_weekly_consolidation_task,
            trigger=CronTrigger(
                day_of_week='sat',
                hour=3,
                minute=0,
                timezone='Europe/London',
            ),
            id='weekly_consolidation',
            name='Weekly QA guideline consolidation',
            replace_existing=True,
            misfire_grace_time=3600,
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

    def add_scheduled_message(
        self,
        prospect_id: uuid.UUID,
        message_text: str,
        run_time: datetime,
    ) -> str:
        """Schedule a message to be sent to a prospect at a future time.

        Args:
            prospect_id: The prospect ID to send to.
            message_text: The message content.
            run_time: When to send the message.

        Returns:
            The job ID.
        """
        job_id = f"pitched_msg_{prospect_id}_{int(run_time.timestamp())}"

        self._scheduler.add_job(
            send_scheduled_pitched_message,
            trigger=DateTrigger(run_date=run_time),
            args=[prospect_id, message_text],
            id=job_id,
            name=f"Scheduled message for prospect {prospect_id}",
            misfire_grace_time=300,
        )

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
