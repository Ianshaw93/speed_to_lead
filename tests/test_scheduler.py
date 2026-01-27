"""Tests for the scheduler service."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scheduler import (
    SchedulerService,
    calculate_snooze_time,
    schedule_snooze_reminder,
)


class TestCalculateSnoozeTime:
    """Tests for snooze time calculation."""

    def test_calculate_1_hour(self):
        """Should calculate time 1 hour from now."""
        before = datetime.now(timezone.utc)
        result = calculate_snooze_time("1h")
        after = datetime.now(timezone.utc)

        expected_min = before + timedelta(hours=1)
        expected_max = after + timedelta(hours=1)

        assert expected_min <= result <= expected_max

    def test_calculate_4_hours(self):
        """Should calculate time 4 hours from now."""
        before = datetime.now(timezone.utc)
        result = calculate_snooze_time("4h")
        after = datetime.now(timezone.utc)

        expected_min = before + timedelta(hours=4)
        expected_max = after + timedelta(hours=4)

        assert expected_min <= result <= expected_max

    def test_calculate_tomorrow_9am(self):
        """Should calculate tomorrow at 9am."""
        result = calculate_snooze_time("tomorrow")

        # Should be at 9:00
        assert result.hour == 9
        assert result.minute == 0

        # Should be tomorrow or later
        now = datetime.now(timezone.utc)
        assert result > now

    def test_invalid_duration(self):
        """Should raise error for invalid duration."""
        with pytest.raises(ValueError):
            calculate_snooze_time("invalid")


class TestSchedulerService:
    """Tests for the scheduler service."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler service for testing."""
        with patch("app.services.scheduler.AsyncIOScheduler") as MockScheduler:
            mock_scheduler = MagicMock()
            mock_scheduler.running = False
            MockScheduler.return_value = mock_scheduler
            service = SchedulerService()
            return service

    def test_scheduler_initialization(self, scheduler):
        """Should initialize the scheduler."""
        assert scheduler._scheduler is not None

    def test_add_snooze_job(self, scheduler):
        """Should add a job to the scheduler."""
        draft_id = uuid.uuid4()
        run_time = datetime.now(timezone.utc) + timedelta(hours=1)

        scheduler.add_snooze_reminder(draft_id, run_time)

        scheduler._scheduler.add_job.assert_called_once()
        call_args = scheduler._scheduler.add_job.call_args
        # Check that the trigger contains the run time
        trigger = call_args.kwargs.get("trigger")
        assert trigger is not None

    def test_cancel_snooze_job(self, scheduler):
        """Should cancel a scheduled job."""
        draft_id = uuid.uuid4()
        # First add a job so there's something to cancel
        run_time = datetime.now(timezone.utc) + timedelta(hours=1)
        scheduler.add_snooze_reminder(draft_id, run_time)

        scheduler.cancel_snooze_reminder(draft_id)

        scheduler._scheduler.remove_job.assert_called_once()

    def test_start_scheduler(self, scheduler):
        """Should start the scheduler."""
        scheduler._scheduler.running = False
        scheduler.start()
        scheduler._scheduler.start.assert_called_once()

    def test_shutdown_scheduler(self, scheduler):
        """Should shut down the scheduler."""
        scheduler._scheduler.running = True
        scheduler.shutdown()
        scheduler._scheduler.shutdown.assert_called_once()


class TestScheduleSnoozeReminder:
    """Tests for the schedule_snooze_reminder helper function."""

    @pytest.mark.asyncio
    async def test_schedule_snooze_calls_service(self):
        """Should use the scheduler service to add a reminder."""
        draft_id = uuid.uuid4()

        with patch("app.services.scheduler.get_scheduler_service") as mock_get:
            mock_scheduler = MagicMock()
            mock_get.return_value = mock_scheduler

            run_time = await schedule_snooze_reminder(draft_id, "1h")

            mock_scheduler.add_snooze_reminder.assert_called_once()
            assert run_time is not None
