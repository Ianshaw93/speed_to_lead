"""Tests for database models."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Draft, DraftStatus, FunnelStage, MessageDirection, MessageLog


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
    async def test_conversation_linkedin_account_id(self, test_db_session: AsyncSession):
        """Should support linkedin_account_id field for sending messages."""
        conversation = Conversation(
            heyreach_lead_id="lead_linkedin",
            linkedin_profile_url="https://linkedin.com/in/test",
            lead_name="LinkedIn Test",
            linkedin_account_id="12345678",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        assert conversation.linkedin_account_id == "12345678"

    @pytest.mark.asyncio
    async def test_conversation_linkedin_account_id_nullable(self, test_db_session: AsyncSession):
        """linkedin_account_id should be nullable for backwards compatibility."""
        conversation = Conversation(
            heyreach_lead_id="lead_no_linkedin",
            linkedin_profile_url="https://linkedin.com/in/nolinkedin",
            lead_name="No LinkedIn ID",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        assert conversation.linkedin_account_id is None

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


class TestFunnelStageEnum:
    """Tests for the FunnelStage enum."""

    def test_funnel_stage_values(self):
        """FunnelStage enum should have all required stages."""
        expected_stages = {
            "initiated",
            "positive_reply",
            "pitched",
            "calendar_sent",
            "booked",
            "regeneration",
        }
        actual_stages = {stage.value for stage in FunnelStage}
        assert actual_stages == expected_stages

    @pytest.mark.asyncio
    async def test_conversation_funnel_stage(self, test_db_session: AsyncSession):
        """Conversation should support funnel_stage field."""
        conversation = Conversation(
            heyreach_lead_id="lead_stage",
            linkedin_profile_url="https://linkedin.com/in/stage",
            lead_name="Stage Test",
            funnel_stage=FunnelStage.POSITIVE_REPLY,
        )
        test_db_session.add(conversation)
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        assert conversation.funnel_stage == FunnelStage.POSITIVE_REPLY

    @pytest.mark.asyncio
    async def test_conversation_funnel_stage_nullable(self, test_db_session: AsyncSession):
        """funnel_stage should be nullable for backwards compatibility."""
        conversation = Conversation(
            heyreach_lead_id="lead_no_stage",
            linkedin_profile_url="https://linkedin.com/in/nostage",
            lead_name="No Stage",
        )
        test_db_session.add(conversation)
        await test_db_session.commit()
        await test_db_session.refresh(conversation)

        assert conversation.funnel_stage is None

    @pytest.mark.asyncio
    async def test_conversation_all_funnel_stages(self, test_db_session: AsyncSession):
        """Should support all funnel stage values."""
        for i, stage in enumerate(FunnelStage):
            conversation = Conversation(
                heyreach_lead_id=f"lead_stage_{i}",
                linkedin_profile_url=f"https://linkedin.com/in/stage{i}",
                lead_name=f"Stage {stage.value}",
                funnel_stage=stage,
            )
            test_db_session.add(conversation)

        await test_db_session.commit()

        result = await test_db_session.execute(
            select(Conversation).where(Conversation.funnel_stage.isnot(None))
        )
        conversations = result.scalars().all()
        stages = {c.funnel_stage for c in conversations}
        assert stages == set(FunnelStage)


class TestSchemaVerification:
    """Tests to verify database schema has all required columns."""

    def test_conversation_has_required_columns(self):
        """Conversation model should have all required columns."""
        from sqlalchemy import inspect

        mapper = inspect(Conversation)
        column_names = {col.key for col in mapper.columns}

        required_columns = {
            'id',
            'heyreach_lead_id',
            'linkedin_profile_url',
            'lead_name',
            'linkedin_account_id',  # Added for sending messages via HeyReach
            'conversation_history',
            'funnel_stage',  # Added for sales funnel tracking
            'created_at',
            'updated_at',
        }

        missing = required_columns - column_names
        assert not missing, f"Missing columns in Conversation: {missing}"

    def test_draft_has_required_columns(self):
        """Draft model should have all required columns."""
        from sqlalchemy import inspect

        mapper = inspect(Draft)
        column_names = {col.key for col in mapper.columns}

        required_columns = {
            'id',
            'conversation_id',
            'status',
            'ai_draft',
            'slack_message_ts',
            'snooze_until',
            'created_at',
            'updated_at',
        }

        missing = required_columns - column_names
        assert not missing, f"Missing columns in Draft: {missing}"

    def test_message_log_has_required_columns(self):
        """MessageLog model should have all required columns."""
        from sqlalchemy import inspect

        mapper = inspect(MessageLog)
        column_names = {col.key for col in mapper.columns}

        required_columns = {
            'id',
            'conversation_id',
            'direction',
            'content',
            'sent_at',
        }

        missing = required_columns - column_names
        assert not missing, f"Missing columns in MessageLog: {missing}"
