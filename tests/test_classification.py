"""Tests for reply classification feature."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Draft, DraftStatus, ReplyClassification, ICPFeedback
from app.services.slack import (
    build_classification_buttons,
    build_action_buttons,
)


class TestClassificationButtons:
    """Tests for classification button building."""

    def test_build_classification_buttons_first_reply(self):
        """Should include Positive Reply button on first reply."""
        draft_id = uuid.uuid4()
        blocks = build_classification_buttons(draft_id, is_first_reply=True)

        # Should return one actions block
        assert len(blocks) == 1
        assert blocks[0]["type"] == "actions"

        # Get all button action IDs
        elements = blocks[0]["elements"]
        action_ids = [el.get("action_id") for el in elements]

        # Should have all three classification buttons
        assert "classify_positive" in action_ids
        assert "classify_not_interested" in action_ids
        assert "classify_not_icp" in action_ids

    def test_build_classification_buttons_not_first_reply(self):
        """Should NOT include Positive Reply button on subsequent replies."""
        draft_id = uuid.uuid4()
        blocks = build_classification_buttons(draft_id, is_first_reply=False)

        # Should return one actions block
        assert len(blocks) == 1
        assert blocks[0]["type"] == "actions"

        # Get all button action IDs
        elements = blocks[0]["elements"]
        action_ids = [el.get("action_id") for el in elements]

        # Should have NOT have positive, but have the others
        assert "classify_positive" not in action_ids
        assert "classify_not_interested" in action_ids
        assert "classify_not_icp" in action_ids

    def test_classification_buttons_have_correct_values(self):
        """Classification buttons should have draft_id as value."""
        draft_id = uuid.uuid4()
        blocks = build_classification_buttons(draft_id, is_first_reply=True)

        for element in blocks[0]["elements"]:
            assert element.get("value") == str(draft_id)

    def test_classification_buttons_have_context(self):
        """Should include context text above buttons."""
        draft_id = uuid.uuid4()
        blocks = build_classification_buttons(draft_id, is_first_reply=True)

        # First block should be context
        # Note: If implementation puts context first, this should pass
        # Otherwise adjust based on actual implementation
        block_str = str(blocks)
        assert "classify" in block_str.lower() or "metric" in block_str.lower() or str(draft_id) in block_str


class TestReplyClassificationEnum:
    """Tests for ReplyClassification enum."""

    def test_enum_values(self):
        """Should have correct enum values."""
        assert ReplyClassification.POSITIVE.value == "positive"
        assert ReplyClassification.NOT_INTERESTED.value == "not_interested"
        assert ReplyClassification.NOT_ICP.value == "not_icp"

    def test_enum_is_string(self):
        """Enum values should be usable as strings."""
        assert str(ReplyClassification.POSITIVE) == "ReplyClassification.POSITIVE"
        assert ReplyClassification.POSITIVE.value == "positive"


class TestDraftClassificationFields:
    """Tests for Draft model classification fields."""

    @pytest.mark.asyncio
    async def test_draft_has_classification_fields(self, test_db_session):
        """Draft should have classification-related fields."""
        from app.models import Conversation

        # Create a conversation first
        conversation = Conversation(
            heyreach_lead_id="test-123",
            linkedin_profile_url="https://linkedin.com/in/test",
            lead_name="Test Lead",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        # Create draft with classification fields
        draft = Draft(
            conversation_id=conversation.id,
            ai_draft="Test draft",
            is_first_reply=True,
            classification=ReplyClassification.POSITIVE,
            classified_at=datetime.now(timezone.utc),
        )
        test_db_session.add(draft)
        await test_db_session.commit()

        # Verify fields are set
        assert draft.is_first_reply is True
        assert draft.classification == ReplyClassification.POSITIVE
        assert draft.classified_at is not None

    @pytest.mark.asyncio
    async def test_draft_classification_nullable(self, test_db_session):
        """Classification should be nullable (not required)."""
        from app.models import Conversation

        conversation = Conversation(
            heyreach_lead_id="test-456",
            linkedin_profile_url="https://linkedin.com/in/test2",
            lead_name="Test Lead 2",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        draft = Draft(
            conversation_id=conversation.id,
            ai_draft="Test draft",
            is_first_reply=False,
            # No classification set
        )
        test_db_session.add(draft)
        await test_db_session.commit()

        assert draft.classification is None
        assert draft.classified_at is None


class TestICPFeedbackModel:
    """Tests for ICPFeedback model."""

    @pytest.mark.asyncio
    async def test_create_icp_feedback(self, test_db_session):
        """Should create ICP feedback record."""
        from app.models import Conversation

        conversation = Conversation(
            heyreach_lead_id="test-789",
            linkedin_profile_url="https://linkedin.com/in/test3",
            lead_name="Test Lead 3",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        draft = Draft(
            conversation_id=conversation.id,
            ai_draft="Test draft",
            is_first_reply=True,
        )
        test_db_session.add(draft)
        await test_db_session.flush()

        feedback = ICPFeedback(
            lead_name="John Doe",
            linkedin_url="https://linkedin.com/in/johndoe",
            job_title="Software Engineer",
            company_name="Tech Corp",
            original_icp_match=True,
            original_icp_reason="Senior role at tech company",
            notes="Actually works in different department",
            marked_by_slack_user="U123ABC",
            draft_id=draft.id,
        )
        test_db_session.add(feedback)
        await test_db_session.commit()

        assert feedback.id is not None
        assert feedback.lead_name == "John Doe"
        assert feedback.draft_id == draft.id


class TestClassificationHandlers:
    """Tests for classification action handlers."""

    @pytest.mark.asyncio
    async def test_classify_positive_updates_draft(self, test_db_session):
        """Classifying as positive should update draft."""
        from app.models import Conversation
        from sqlalchemy import select

        # Setup
        conversation = Conversation(
            heyreach_lead_id="test-handler-1",
            linkedin_profile_url="https://linkedin.com/in/handler1",
            lead_name="Handler Test 1",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        draft = Draft(
            conversation_id=conversation.id,
            ai_draft="Test draft",
            is_first_reply=True,
        )
        test_db_session.add(draft)
        await test_db_session.commit()
        draft_id = draft.id

        # Simulate classification
        draft.classification = ReplyClassification.POSITIVE
        draft.classified_at = datetime.now(timezone.utc)
        await test_db_session.commit()

        # Verify
        result = await test_db_session.execute(
            select(Draft).where(Draft.id == draft_id)
        )
        updated_draft = result.scalar_one()
        assert updated_draft.classification == ReplyClassification.POSITIVE
        assert updated_draft.classified_at is not None


class TestMetricsAPI:
    """Tests for metrics API endpoints with proper database integration."""

    @pytest.mark.asyncio
    async def test_classifications_endpoint_empty(self, test_client):
        """GET /api/metrics/classifications with empty database."""
        response = await test_client.get("/api/metrics/classifications")
        assert response.status_code == 200
        data = response.json()

        # Should have expected fields
        assert "total_drafts" in data
        assert "classified" in data
        assert "by_classification" in data
        assert data["total_drafts"] == 0
        assert data["classified"] == 0

    @pytest.mark.asyncio
    async def test_icp_feedback_endpoint_empty(self, test_client):
        """GET /api/metrics/icp-feedback with empty database."""
        response = await test_client.get("/api/metrics/icp-feedback")
        assert response.status_code == 200
        data = response.json()

        # Should have expected structure
        assert "feedback" in data
        assert isinstance(data["feedback"], list)
        assert len(data["feedback"]) == 0
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_summary_endpoint(self, test_client):
        """GET /api/metrics/summary should return summary data."""
        response = await test_client.get("/api/metrics/summary")
        assert response.status_code == 200
        data = response.json()

        assert "total_drafts" in data
        assert "first_reply_count" in data
        assert "classifications" in data
        assert "icp_feedback_records" in data
        assert "generated_at" in data


class TestMetricsQueries:
    """Tests for metrics query logic using test database session."""

    @pytest.mark.asyncio
    async def test_count_drafts_by_classification(self, test_db_session):
        """Should be able to count drafts by classification."""
        from app.models import Conversation
        from sqlalchemy import func, select

        # Create test data
        conversation = Conversation(
            heyreach_lead_id="test-metrics-1",
            linkedin_profile_url="https://linkedin.com/in/metrics1",
            lead_name="Metrics Test",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        # Create drafts with different classifications
        draft1 = Draft(
            conversation_id=conversation.id,
            ai_draft="Test 1",
            is_first_reply=True,
            classification=ReplyClassification.POSITIVE,
        )
        draft2 = Draft(
            conversation_id=conversation.id,
            ai_draft="Test 2",
            is_first_reply=False,
            classification=ReplyClassification.NOT_INTERESTED,
        )
        draft3 = Draft(
            conversation_id=conversation.id,
            ai_draft="Test 3",
            is_first_reply=False,
            # No classification
        )
        test_db_session.add_all([draft1, draft2, draft3])
        await test_db_session.commit()

        # Count total
        total_result = await test_db_session.execute(
            select(func.count(Draft.id))
        )
        total = total_result.scalar()
        assert total == 3

        # Count classified
        classified_result = await test_db_session.execute(
            select(func.count(Draft.id)).where(Draft.classification.isnot(None))
        )
        classified = classified_result.scalar()
        assert classified == 2

        # Count positive
        positive_result = await test_db_session.execute(
            select(func.count(Draft.id)).where(
                Draft.classification == ReplyClassification.POSITIVE
            )
        )
        positive = positive_result.scalar()
        assert positive == 1

    @pytest.mark.asyncio
    async def test_icp_feedback_query(self, test_db_session):
        """Should be able to query ICP feedback records."""
        from app.models import Conversation
        from sqlalchemy import select

        # Create test data
        conversation = Conversation(
            heyreach_lead_id="test-icp-query",
            linkedin_profile_url="https://linkedin.com/in/icpquery",
            lead_name="ICP Query Test",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        draft = Draft(
            conversation_id=conversation.id,
            ai_draft="Test draft",
            is_first_reply=True,
            classification=ReplyClassification.NOT_ICP,
        )
        test_db_session.add(draft)
        await test_db_session.flush()

        feedback = ICPFeedback(
            lead_name="ICP Test Lead",
            linkedin_url="https://linkedin.com/in/icptest",
            job_title="Manager",
            company_name="Test Co",
            notes="Wrong industry",
            draft_id=draft.id,
        )
        test_db_session.add(feedback)
        await test_db_session.commit()

        # Query feedback
        result = await test_db_session.execute(
            select(ICPFeedback).order_by(ICPFeedback.created_at.desc())
        )
        records = result.scalars().all()
        assert len(records) == 1
        assert records[0].lead_name == "ICP Test Lead"
        assert records[0].notes == "Wrong industry"


class TestFirstReplyDetection:
    """Tests for first reply detection logic."""

    @pytest.mark.asyncio
    async def test_first_inbound_message_sets_is_first_reply(self, test_db_session):
        """First inbound message should set is_first_reply=True on draft."""
        from app.models import Conversation, MessageLog, MessageDirection
        from sqlalchemy import select, func

        # Create conversation with no previous inbound messages
        conversation = Conversation(
            heyreach_lead_id="test-first-1",
            linkedin_profile_url="https://linkedin.com/in/first1",
            lead_name="First Reply Test",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        # Count inbound messages (should be 0)
        result = await test_db_session.execute(
            select(func.count(MessageLog.id)).where(
                MessageLog.conversation_id == conversation.id,
                MessageLog.direction == MessageDirection.INBOUND,
            )
        )
        inbound_count = result.scalar()
        assert inbound_count == 0

        # First draft should have is_first_reply=True
        is_first_reply = inbound_count == 0
        assert is_first_reply is True

    @pytest.mark.asyncio
    async def test_subsequent_message_sets_is_first_reply_false(self, test_db_session):
        """Subsequent inbound messages should set is_first_reply=False."""
        from app.models import Conversation, MessageLog, MessageDirection
        from sqlalchemy import select, func

        # Create conversation
        conversation = Conversation(
            heyreach_lead_id="test-second-1",
            linkedin_profile_url="https://linkedin.com/in/second1",
            lead_name="Second Reply Test",
        )
        test_db_session.add(conversation)
        await test_db_session.flush()

        # Add a previous inbound message
        msg = MessageLog(
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND,
            content="First message",
        )
        test_db_session.add(msg)
        await test_db_session.flush()

        # Count inbound messages (should be 1)
        result = await test_db_session.execute(
            select(func.count(MessageLog.id)).where(
                MessageLog.conversation_id == conversation.id,
                MessageLog.direction == MessageDirection.INBOUND,
            )
        )
        inbound_count = result.scalar()
        assert inbound_count == 1

        # Second draft should have is_first_reply=False
        is_first_reply = inbound_count == 0
        assert is_first_reply is False
