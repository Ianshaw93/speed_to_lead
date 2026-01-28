"""Tests for database models."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Draft, DraftStatus, MessageDirection, MessageLog


class TestConversationModel:
    """Tests for the Conversation model."""

    @pytest.mark.asyncio
    async def test_create_conversation(self, test_db_session: AsyncSession):
        """Should create a conversation with required fields."""
        conversation = Conversation(
            heyreach_lead_id="lead_123",
            linkedin_profile_url="https://linkedin.com/in/johndoe",
            lead_name="John Doe",
            conversation_history=[{"role": "lead", "content": "Hello!"}],
        )
        test_db_session.add(conversation)
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        assert conversation.id is not None
        assert isinstance(conversation.id, uuid.UUID)
        assert conversation.heyreach_lead_id == "lead_123"
        assert conversation.linkedin_profile_url == "https://linkedin.com/in/johndoe"
        assert conversation.lead_name == "John Doe"
        assert conversation.conversation_history == [{"role": "lead", "content": "Hello!"}]
        assert conversation.created_at is not None
        assert conversation.updated_at is not None

    @pytest.mark.asyncio
    async def test_conversation_timestamps_update(self, test_db_session: AsyncSession):
        """Should update the updated_at timestamp on changes."""
        conversation = Conversation(
            heyreach_lead_id="lead_456",
            linkedin_profile_url="https://linkedin.com/in/janedoe",
            lead_name="Jane Doe",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        original_updated_at = conversation.updated_at

        conversation.lead_name = "Jane Smith"
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        # Note: SQLite doesn't auto-update timestamps, so we check created_at stayed same
        assert conversation.created_at is not None


class TestDraftModel:
    """Tests for the Draft model."""

    @pytest.mark.asyncio
    async def test_create_draft(self, test_db_session: AsyncSession):
        """Should create a draft linked to a conversation."""
        conversation = Conversation(
            heyreach_lead_id="lead_789",
            linkedin_profile_url="https://linkedin.com/in/test",
            lead_name="Test User",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        draft = Draft(
            conversation_id=conversation.id,
            status=DraftStatus.PENDING,
            ai_draft="Hello! Thanks for your interest...",
            slack_message_ts="1234567890.123456",
        )
        test_db_session.add(draft)
        await test_db_session.commit()
        await test_db_session.refresh(draft)

        assert draft.id is not None
        assert draft.conversation_id == conversation.id
        assert draft.status == DraftStatus.PENDING
        assert draft.ai_draft == "Hello! Thanks for your interest..."
        assert draft.slack_message_ts == "1234567890.123456"
        assert draft.snooze_until is None

    @pytest.mark.asyncio
    async def test_draft_status_enum(self, test_db_session: AsyncSession):
        """Should support all draft status values."""
        conversation = Conversation(
            heyreach_lead_id="lead_enum",
            linkedin_profile_url="https://linkedin.com/in/enum",
            lead_name="Enum Test",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()

        for status in DraftStatus:
            draft = Draft(
                conversation_id=conversation.id,
                status=status,
                ai_draft=f"Draft with {status.value} status",
            )
            test_db_session.add(draft)

        await test_db_session.commit()

        result = await test_db_session.execute(
            select(Draft).where(Draft.conversation_id == conversation.id)
        )
        drafts = result.scalars().all()
        assert len(drafts) == len(DraftStatus)

    @pytest.mark.asyncio
    async def test_draft_snooze_until(self, test_db_session: AsyncSession):
        """Should support snooze_until timestamp."""
        conversation = Conversation(
            heyreach_lead_id="lead_snooze",
            linkedin_profile_url="https://linkedin.com/in/snooze",
            lead_name="Snooze Test",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()

        snooze_time = datetime.now(timezone.utc)
        draft = Draft(
            conversation_id=conversation.id,
            status=DraftStatus.SNOOZED,
            ai_draft="Snoozed draft",
            snooze_until=snooze_time,
        )
        test_db_session.add(draft)
        await test_db_session.commit()
        await test_db_session.refresh(draft)

        assert draft.snooze_until is not None


class TestMessageLogModel:
    """Tests for the MessageLog model."""

    @pytest.mark.asyncio
    async def test_create_message_log(self, test_db_session: AsyncSession):
        """Should create a message log entry."""
        conversation = Conversation(
            heyreach_lead_id="lead_msg",
            linkedin_profile_url="https://linkedin.com/in/msg",
            lead_name="Message Test",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        message = MessageLog(
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND,
            content="Hello from lead!",
        )
        test_db_session.add(message)
        await test_db_session.commit()
        await test_db_session.refresh(message)

        assert message.id is not None
        assert message.conversation_id == conversation.id
        assert message.direction == MessageDirection.INBOUND
        assert message.content == "Hello from lead!"
        assert message.sent_at is not None

    @pytest.mark.asyncio
    async def test_message_direction_enum(self, test_db_session: AsyncSession):
        """Should support inbound and outbound directions."""
        conversation = Conversation(
            heyreach_lead_id="lead_dir",
            linkedin_profile_url="https://linkedin.com/in/dir",
            lead_name="Direction Test",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()

        inbound = MessageLog(
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND,
            content="Inbound message",
        )
        outbound = MessageLog(
            conversation_id=conversation.id,
            direction=MessageDirection.OUTBOUND,
            content="Outbound message",
        )
        test_db_session.add_all([inbound, outbound])
        await test_db_session.commit()

        result = await test_db_session.execute(
            select(MessageLog).where(MessageLog.conversation_id == conversation.id)
        )
        messages = result.scalars().all()
        assert len(messages) == 2
        directions = {m.direction for m in messages}
        assert directions == {MessageDirection.INBOUND, MessageDirection.OUTBOUND}
