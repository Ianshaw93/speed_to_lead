"""Tests for Pydantic schemas."""

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import DraftStatus, MessageDirection
from app.schemas import (
    ConversationCreate,
    ConversationResponse,
    DraftCreate,
    DraftResponse,
    DraftUpdate,
    HeyReachWebhookPayload,
    HealthResponse,
    MessageLogCreate,
    TelegramCallbackData,
)


class TestConversationSchemas:
    """Tests for conversation schemas."""

    def test_conversation_create_valid(self):
        """Should create a valid conversation schema."""
        data = ConversationCreate(
            heyreach_lead_id="lead_123",
            linkedin_profile_url="https://linkedin.com/in/johndoe",
            lead_name="John Doe",
        )
        assert data.heyreach_lead_id == "lead_123"
        assert data.lead_name == "John Doe"
        assert data.conversation_history == []

    def test_conversation_create_with_history(self):
        """Should accept conversation history."""
        history = [{"role": "lead", "content": "Hello!"}]
        data = ConversationCreate(
            heyreach_lead_id="lead_123",
            linkedin_profile_url="https://linkedin.com/in/johndoe",
            lead_name="John Doe",
            conversation_history=history,
        )
        assert data.conversation_history == history

    def test_conversation_response_from_attributes(self):
        """Should work with from_attributes mode."""

        class MockConversation:
            id = uuid.uuid4()
            heyreach_lead_id = "lead_123"
            linkedin_profile_url = "https://linkedin.com/in/test"
            lead_name = "Test User"
            conversation_history = []
            created_at = datetime.now(timezone.utc)
            updated_at = datetime.now(timezone.utc)

        response = ConversationResponse.model_validate(MockConversation())
        assert response.heyreach_lead_id == "lead_123"


class TestDraftSchemas:
    """Tests for draft schemas."""

    def test_draft_create_valid(self):
        """Should create a valid draft schema."""
        conv_id = uuid.uuid4()
        data = DraftCreate(
            conversation_id=conv_id,
            ai_draft="Hello! Thanks for reaching out...",
        )
        assert data.conversation_id == conv_id
        assert data.status == DraftStatus.PENDING
        assert data.telegram_message_id is None

    def test_draft_update_partial(self):
        """Should allow partial updates."""
        data = DraftUpdate(status=DraftStatus.APPROVED)
        assert data.status == DraftStatus.APPROVED
        assert data.ai_draft is None
        assert data.snooze_until is None

    def test_draft_update_snooze(self):
        """Should allow setting snooze time."""
        snooze_time = datetime.now(timezone.utc)
        data = DraftUpdate(
            status=DraftStatus.SNOOZED,
            snooze_until=snooze_time,
        )
        assert data.status == DraftStatus.SNOOZED
        assert data.snooze_until is not None


class TestMessageLogSchemas:
    """Tests for message log schemas."""

    def test_message_log_create_inbound(self):
        """Should create an inbound message log."""
        conv_id = uuid.uuid4()
        data = MessageLogCreate(
            conversation_id=conv_id,
            direction=MessageDirection.INBOUND,
            content="Hello from lead!",
        )
        assert data.direction == MessageDirection.INBOUND
        assert data.content == "Hello from lead!"

    def test_message_log_create_outbound(self):
        """Should create an outbound message log."""
        conv_id = uuid.uuid4()
        data = MessageLogCreate(
            conversation_id=conv_id,
            direction=MessageDirection.OUTBOUND,
            content="Hello from us!",
        )
        assert data.direction == MessageDirection.OUTBOUND


class TestHeyReachWebhookPayload:
    """Tests for HeyReach webhook payload schema."""

    def test_webhook_payload_valid(self):
        """Should parse valid webhook payload."""
        data = HeyReachWebhookPayload(
            leadId="lead_123",
            linkedinUrl="https://linkedin.com/in/johndoe",
            leadName="John Doe",
            messageContent="I'm interested in learning more!",
        )
        assert data.lead_id == "lead_123"
        assert data.linkedin_url == "https://linkedin.com/in/johndoe"
        assert data.message_content == "I'm interested in learning more!"

    def test_webhook_payload_with_optional_fields(self):
        """Should parse webhook with optional company/title."""
        data = HeyReachWebhookPayload(
            leadId="lead_123",
            linkedinUrl="https://linkedin.com/in/johndoe",
            leadName="John Doe",
            messageContent="Hello!",
            leadTitle="VP of Engineering",
            leadCompany="Acme Corp",
        )
        assert data.lead_title == "VP of Engineering"
        assert data.lead_company == "Acme Corp"

    def test_webhook_payload_missing_required(self):
        """Should reject payload missing required fields."""
        with pytest.raises(ValidationError):
            HeyReachWebhookPayload(
                leadId="lead_123",
                # Missing required fields
            )


class TestTelegramCallbackData:
    """Tests for Telegram callback data schema."""

    def test_callback_data_approve(self):
        """Should parse approve callback data."""
        draft_id = uuid.uuid4()
        data = TelegramCallbackData(
            action="approve",
            draft_id=draft_id,
        )
        assert data.action == "approve"
        assert data.draft_id == draft_id
        assert data.extra is None

    def test_callback_data_with_extra(self):
        """Should parse callback with extra data."""
        draft_id = uuid.uuid4()
        data = TelegramCallbackData(
            action="snooze",
            draft_id=draft_id,
            extra="1h",
        )
        assert data.extra == "1h"


class TestHealthResponse:
    """Tests for health check response schema."""

    def test_health_response(self):
        """Should create valid health response."""
        data = HealthResponse(
            status="healthy",
            environment="production",
        )
        assert data.status == "healthy"
        assert data.environment == "production"
