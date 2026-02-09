"""Tests for speed metrics calculation (Speed to Lead and Speed to Reply)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    Conversation,
    MessageDirection,
    MessageLog,
    Prospect,
    ProspectSource,
)
from app.services.reports import (
    calculate_speed_to_lead,
    calculate_speed_to_reply,
)


@pytest_asyncio.fixture
async def speed_metrics_db():
    """Create a test database for speed metrics tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


class TestSpeedToLead:
    """Tests for Speed to Lead metric calculation."""

    @pytest.mark.asyncio
    async def test_speed_to_lead_basic(self, speed_metrics_db):
        """Should calculate time from heyreach_uploaded_at to first inbound message."""
        session = speed_metrics_db

        # Create a prospect uploaded 2 hours ago
        uploaded_at = datetime.now(timezone.utc) - timedelta(hours=2)
        prospect = Prospect(
            linkedin_url="https://linkedin.com/in/johndoe",
            full_name="John Doe",
            source_type=ProspectSource.COLD_OUTREACH,
            heyreach_uploaded_at=uploaded_at,
        )
        session.add(prospect)

        # Create conversation for this prospect
        conversation = Conversation(
            heyreach_lead_id="lead_123",
            linkedin_profile_url="https://linkedin.com/in/johndoe",
            lead_name="John Doe",
        )
        session.add(conversation)
        await session.flush()

        # First inbound message 2 hours after outreach (now)
        first_reply_at = datetime.now(timezone.utc)
        message = MessageLog(
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND,
            content="Yes, I'm interested!",
            sent_at=first_reply_at,
        )
        session.add(message)
        await session.commit()

        # Calculate for today
        start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_lead(session, start_date, end_date)

        assert result is not None
        assert result["count"] == 1
        # Should be approximately 120 minutes (2 hours)
        assert 115 <= result["avg_minutes"] <= 125

    @pytest.mark.asyncio
    async def test_speed_to_lead_multiple_prospects(self, speed_metrics_db):
        """Should calculate average across multiple first replies."""
        session = speed_metrics_db
        now = datetime.now(timezone.utc)

        # Prospect 1: uploaded 1 hour ago, replied now (60 min)
        prospect1 = Prospect(
            linkedin_url="https://linkedin.com/in/alice",
            full_name="Alice",
            source_type=ProspectSource.COLD_OUTREACH,
            heyreach_uploaded_at=now - timedelta(hours=1),
        )
        session.add(prospect1)

        conv1 = Conversation(
            heyreach_lead_id="lead_1",
            linkedin_profile_url="https://linkedin.com/in/alice",
            lead_name="Alice",
        )
        session.add(conv1)
        await session.flush()

        msg1 = MessageLog(
            conversation_id=conv1.id,
            direction=MessageDirection.INBOUND,
            content="Interested!",
            sent_at=now,
        )
        session.add(msg1)

        # Prospect 2: uploaded 3 hours ago, replied now (180 min)
        prospect2 = Prospect(
            linkedin_url="https://linkedin.com/in/bob",
            full_name="Bob",
            source_type=ProspectSource.COLD_OUTREACH,
            heyreach_uploaded_at=now - timedelta(hours=3),
        )
        session.add(prospect2)

        conv2 = Conversation(
            heyreach_lead_id="lead_2",
            linkedin_profile_url="https://linkedin.com/in/bob",
            lead_name="Bob",
        )
        session.add(conv2)
        await session.flush()

        msg2 = MessageLog(
            conversation_id=conv2.id,
            direction=MessageDirection.INBOUND,
            content="Tell me more",
            sent_at=now,
        )
        session.add(msg2)
        await session.commit()

        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_lead(session, start_date, end_date)

        assert result is not None
        assert result["count"] == 2
        # Average should be (60 + 180) / 2 = 120 minutes
        assert 115 <= result["avg_minutes"] <= 125

    @pytest.mark.asyncio
    async def test_speed_to_lead_only_first_inbound(self, speed_metrics_db):
        """Should only count first inbound message per conversation, not subsequent ones."""
        session = speed_metrics_db
        now = datetime.now(timezone.utc)

        # Prospect uploaded 1 hour ago
        prospect = Prospect(
            linkedin_url="https://linkedin.com/in/charlie",
            full_name="Charlie",
            source_type=ProspectSource.COLD_OUTREACH,
            heyreach_uploaded_at=now - timedelta(hours=1),
        )
        session.add(prospect)

        conv = Conversation(
            heyreach_lead_id="lead_3",
            linkedin_profile_url="https://linkedin.com/in/charlie",
            lead_name="Charlie",
        )
        session.add(conv)
        await session.flush()

        # First inbound message (this is the speed to lead)
        msg1 = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.INBOUND,
            content="First reply",
            sent_at=now,
        )
        session.add(msg1)

        # Second inbound message (should not count as new speed to lead)
        msg2 = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.INBOUND,
            content="Second message",
            sent_at=now + timedelta(minutes=30),
        )
        session.add(msg2)
        await session.commit()

        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=2)

        result = await calculate_speed_to_lead(session, start_date, end_date)

        assert result is not None
        # Should only count 1 first reply, not 2
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_speed_to_lead_no_data(self, speed_metrics_db):
        """Should return None when no data in date range."""
        session = speed_metrics_db

        start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_lead(session, start_date, end_date)

        assert result is None

    @pytest.mark.asyncio
    async def test_speed_to_lead_no_prospect_match(self, speed_metrics_db):
        """Should handle conversations without matching prospect (no heyreach_uploaded_at)."""
        session = speed_metrics_db
        now = datetime.now(timezone.utc)

        # Conversation without a matching Prospect
        conv = Conversation(
            heyreach_lead_id="lead_orphan",
            linkedin_profile_url="https://linkedin.com/in/orphan",
            lead_name="Orphan Lead",
        )
        session.add(conv)
        await session.flush()

        msg = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.INBOUND,
            content="Hello",
            sent_at=now,
        )
        session.add(msg)
        await session.commit()

        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_lead(session, start_date, end_date)

        # Should return None since no prospect with heyreach_uploaded_at
        assert result is None


class TestSpeedToReply:
    """Tests for Speed to Reply metric calculation (our response time)."""

    @pytest.mark.asyncio
    async def test_speed_to_reply_basic(self, speed_metrics_db):
        """Should calculate time from inbound message to our response."""
        session = speed_metrics_db
        now = datetime.now(timezone.utc)

        conv = Conversation(
            heyreach_lead_id="lead_reply",
            linkedin_profile_url="https://linkedin.com/in/reply-test",
            lead_name="Reply Test",
        )
        session.add(conv)
        await session.flush()

        # Inbound message at now
        inbound = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.INBOUND,
            content="Question?",
            sent_at=now,
        )
        session.add(inbound)

        # Our response 30 minutes later
        outbound = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.OUTBOUND,
            content="Answer!",
            sent_at=now + timedelta(minutes=30),
        )
        session.add(outbound)
        await session.commit()

        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_reply(session, start_date, end_date)

        assert result is not None
        assert result["count"] == 1
        # Should be approximately 30 minutes
        assert 28 <= result["avg_minutes"] <= 32

    @pytest.mark.asyncio
    async def test_speed_to_reply_multiple(self, speed_metrics_db):
        """Should calculate average response time across multiple exchanges."""
        session = speed_metrics_db
        now = datetime.now(timezone.utc)

        conv = Conversation(
            heyreach_lead_id="lead_multi",
            linkedin_profile_url="https://linkedin.com/in/multi-reply",
            lead_name="Multi Reply",
        )
        session.add(conv)
        await session.flush()

        # Exchange 1: inbound -> 20 min -> outbound
        msg1_in = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.INBOUND,
            content="First question",
            sent_at=now,
        )
        session.add(msg1_in)

        msg1_out = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.OUTBOUND,
            content="First answer",
            sent_at=now + timedelta(minutes=20),
        )
        session.add(msg1_out)

        # Exchange 2: inbound -> 40 min -> outbound
        msg2_in = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.INBOUND,
            content="Second question",
            sent_at=now + timedelta(hours=1),
        )
        session.add(msg2_in)

        msg2_out = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.OUTBOUND,
            content="Second answer",
            sent_at=now + timedelta(hours=1, minutes=40),
        )
        session.add(msg2_out)
        await session.commit()

        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_reply(session, start_date, end_date)

        assert result is not None
        assert result["count"] == 2
        # Average should be (20 + 40) / 2 = 30 minutes
        assert 28 <= result["avg_minutes"] <= 32

    @pytest.mark.asyncio
    async def test_speed_to_reply_no_response_yet(self, speed_metrics_db):
        """Should not count inbound messages without a response."""
        session = speed_metrics_db
        now = datetime.now(timezone.utc)

        conv = Conversation(
            heyreach_lead_id="lead_pending",
            linkedin_profile_url="https://linkedin.com/in/pending",
            lead_name="Pending",
        )
        session.add(conv)
        await session.flush()

        # Inbound message with no response
        inbound = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.INBOUND,
            content="Waiting for reply...",
            sent_at=now,
        )
        session.add(inbound)
        await session.commit()

        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_reply(session, start_date, end_date)

        # No response sent, so nothing to count
        assert result is None

    @pytest.mark.asyncio
    async def test_speed_to_reply_across_conversations(self, speed_metrics_db):
        """Should calculate response time across different conversations."""
        session = speed_metrics_db
        now = datetime.now(timezone.utc)

        # Conversation 1: 15 min response
        conv1 = Conversation(
            heyreach_lead_id="lead_c1",
            linkedin_profile_url="https://linkedin.com/in/conv1",
            lead_name="Conv1",
        )
        session.add(conv1)
        await session.flush()

        msg1_in = MessageLog(
            conversation_id=conv1.id,
            direction=MessageDirection.INBOUND,
            content="Question 1",
            sent_at=now,
        )
        session.add(msg1_in)

        msg1_out = MessageLog(
            conversation_id=conv1.id,
            direction=MessageDirection.OUTBOUND,
            content="Answer 1",
            sent_at=now + timedelta(minutes=15),
        )
        session.add(msg1_out)

        # Conversation 2: 45 min response
        conv2 = Conversation(
            heyreach_lead_id="lead_c2",
            linkedin_profile_url="https://linkedin.com/in/conv2",
            lead_name="Conv2",
        )
        session.add(conv2)
        await session.flush()

        msg2_in = MessageLog(
            conversation_id=conv2.id,
            direction=MessageDirection.INBOUND,
            content="Question 2",
            sent_at=now,
        )
        session.add(msg2_in)

        msg2_out = MessageLog(
            conversation_id=conv2.id,
            direction=MessageDirection.OUTBOUND,
            content="Answer 2",
            sent_at=now + timedelta(minutes=45),
        )
        session.add(msg2_out)
        await session.commit()

        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_reply(session, start_date, end_date)

        assert result is not None
        assert result["count"] == 2
        # Average: (15 + 45) / 2 = 30 minutes
        assert 28 <= result["avg_minutes"] <= 32

    @pytest.mark.asyncio
    async def test_speed_to_reply_no_data(self, speed_metrics_db):
        """Should return None when no inbound messages in date range."""
        session = speed_metrics_db

        start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)

        result = await calculate_speed_to_reply(session, start_date, end_date)

        assert result is None


class TestFormatMinutes:
    """Tests for the format_minutes helper function."""

    def test_format_minutes_none(self):
        """Should return N/A for None."""
        from app.services.reports import format_minutes

        assert format_minutes(None) == "N/A"

    def test_format_minutes_under_hour(self):
        """Should format minutes only when under 1 hour."""
        from app.services.reports import format_minutes

        assert format_minutes(45) == "45m"
        assert format_minutes(1) == "1m"

    def test_format_minutes_exact_hour(self):
        """Should format exact hours without minutes."""
        from app.services.reports import format_minutes

        assert format_minutes(60) == "1h"
        assert format_minutes(120) == "2h"

    def test_format_minutes_hours_and_minutes(self):
        """Should format as Xh Ym when both apply."""
        from app.services.reports import format_minutes

        assert format_minutes(90) == "1h 30m"
        assert format_minutes(135) == "2h 15m"

    def test_format_minutes_zero(self):
        """Should handle zero minutes."""
        from app.services.reports import format_minutes

        assert format_minutes(0) == "0m"
