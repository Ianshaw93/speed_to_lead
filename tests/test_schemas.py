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
    DraftUpdate,
    HeyReachWebhookPayload,
    HealthResponse,
    MessageLogCreate,
    SlackActionPayload,
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

    def test_webhook_payload_with_body_wrapper(self):
        """Should parse valid webhook payload with body wrapper."""
        data = HeyReachWebhookPayload(
            body={
                "lead": {"full_name": "John Doe"},
                "recent_messages": [
                    {"creation_time": "2024-01-27T10:00:00Z", "message": "I'm interested!"}
                ],
                "conversation_id": "conv_123",
                "sender": {"id": "li_account_456"},
            }
        )
        assert data.lead_name == "John Doe"
        assert data.conversation_id == "conv_123"
        assert data.linkedin_account_id == "li_account_456"
        assert data.latest_message == "I'm interested!"

    def test_webhook_payload_without_body_wrapper(self):
        """Should parse valid webhook payload without body wrapper."""
        data = HeyReachWebhookPayload(
            lead={"full_name": "Jane Doe"},
            recent_messages=[
                {"creation_time": "2024-01-27T10:00:00Z", "message": "Hello direct!"}
            ],
            conversation_id="conv_direct",
            sender={"id": "li_direct_456"},
        )
        assert data.lead_name == "Jane Doe"
        assert data.conversation_id == "conv_direct"
        assert data.linkedin_account_id == "li_direct_456"
        assert data.latest_message == "Hello direct!"

    def test_webhook_payload_with_optional_fields(self):
        """Should parse webhook with optional company/email."""
        data = HeyReachWebhookPayload(
            body={
                "lead": {
                    "full_name": "John Doe",
                    "company_name": "Acme Corp",
                    "company_url": "https://acme.com",
                    "email_address": "john@acme.com",
                },
                "recent_messages": [
                    {"creation_time": "2024-01-27T10:00:00Z", "message": "Hello!"}
                ],
                "conversation_id": "conv_123",
                "sender": {"id": "li_account_456"},
            }
        )
        assert data.lead_company == "Acme Corp"
        assert data.body.lead.email_address == "john@acme.com"

    def test_webhook_payload_missing_required(self):
        """Should reject payload missing both body and direct fields."""
        with pytest.raises(ValidationError):
            HeyReachWebhookPayload()  # No body or direct fields

    def test_webhook_payload_multiple_messages(self):
        """Should return the latest message from conversation history."""
        data = HeyReachWebhookPayload(
            body={
                "lead": {"full_name": "John Doe"},
                "recent_messages": [
                    {"creation_time": "2024-01-27T09:00:00Z", "message": "First message"},
                    {"creation_time": "2024-01-27T10:00:00Z", "message": "Latest reply"},
                ],
                "conversation_id": "conv_123",
                "sender": {"id": "li_account_456"},
            }
        )
        assert data.latest_message == "Latest reply"


class TestSlackActionPayload:
    """Tests for Slack action payload schema."""

    def test_action_payload_approve(self):
        """Should parse approve action payload."""
        draft_id = uuid.uuid4()
        data = SlackActionPayload(
            action_id="approve",
            draft_id=draft_id,
        )
        assert data.action_id == "approve"
        assert data.draft_id == draft_id

    def test_action_payload_snooze(self):
        """Should parse snooze action payload."""
        draft_id = uuid.uuid4()
        data = SlackActionPayload(
            action_id="snooze_1h",
            draft_id=draft_id,
        )
        assert data.action_id == "snooze_1h"


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
