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

    def test_webhook_payload_valid(self):
        """Should parse valid webhook payload."""
        data = HeyReachWebhookPayload(
            lead={"full_name": "John Doe"},
            recent_messages=[
                {"creation_time": "2024-01-27T10:00:00Z", "message": "I'm interested!"}
            ],
            conversation_id="conv_123",
            sender={"id": 456},
        )
        assert data.lead_name == "John Doe"
        assert data.conversation_id == "conv_123"
        assert data.linkedin_account_id == "456"
        assert data.latest_message == "I'm interested!"

    def test_webhook_payload_with_string_sender_id(self):
        """Should accept sender.id as string."""
        data = HeyReachWebhookPayload(
            lead={"full_name": "Jane Doe"},
            recent_messages=[
                {"creation_time": "2024-01-27T10:00:00Z", "message": "Hello!"}
            ],
            conversation_id="conv_direct",
            sender={"id": "li_direct_456"},
        )
        assert data.linkedin_account_id == "li_direct_456"

    def test_webhook_payload_with_optional_fields(self):
        """Should parse webhook with optional company/email."""
        data = HeyReachWebhookPayload(
            lead={
                "full_name": "John Doe",
                "company_name": "Acme Corp",
                "company_url": "https://acme.com",
                "email_address": "john@acme.com",
                "position": "CEO",
                "profile_url": "https://linkedin.com/in/johndoe",
            },
            recent_messages=[
                {"creation_time": "2024-01-27T10:00:00Z", "message": "Hello!"}
            ],
            conversation_id="conv_123",
            sender={"id": 456, "full_name": "Sender Name"},
            campaign={"id": 123, "name": "Test Campaign"},
            event_type="every_message_reply_received",
        )
        assert data.lead_company == "Acme Corp"
        assert data.lead_title == "CEO"
        assert data.linkedin_profile_url == "https://linkedin.com/in/johndoe"
        assert data.lead.email_address == "john@acme.com"

    def test_webhook_payload_missing_required(self):
        """Should reject payload missing required fields."""
        with pytest.raises(ValidationError):
            HeyReachWebhookPayload(
                lead={"full_name": "John"},
                # Missing: recent_messages, conversation_id, sender
            )

    def test_webhook_payload_multiple_messages(self):
        """Should return the latest non-empty message from conversation history."""
        data = HeyReachWebhookPayload(
            lead={"full_name": "John Doe"},
            recent_messages=[
                {"creation_time": "2024-01-27T09:00:00Z", "message": "First message"},
                {"creation_time": "2024-01-27T10:00:00Z", "message": "Latest reply"},
                {"creation_time": "2024-01-27T10:01:00Z", "message": "", "message_type": "Attachment"},
            ],
            conversation_id="conv_123",
            sender={"id": 456},
        )
        # Should skip the empty attachment message
        assert data.latest_message == "Latest reply"

    def test_webhook_payload_ignores_extra_fields(self):
        """Should ignore extra fields not in schema."""
        data = HeyReachWebhookPayload(
            lead={"full_name": "John Doe", "unknown_field": "value"},
            recent_messages=[
                {"creation_time": "2024-01-27T10:00:00Z", "message": "Hi"}
            ],
            conversation_id="conv_123",
            sender={"id": 456},
            unknown_root_field="should be ignored",
        )
        assert data.lead_name == "John Doe"


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


class TestHeyReachLeadAllFields:
    """Tests to ensure HeyReachLead schema includes all fields from HeyReach webhooks."""

    def test_lead_extended_fields(self):
        """Should parse all lead fields including summary, about, tags, and lists."""
        payload = {
            "recent_messages": [
                {"creation_time": "2026-01-31T16:22:24Z", "message": "Hi"}
            ],
            "conversation_id": "conv_123",
            "sender": {"id": 123},
            "lead": {
                "id": "TestId",
                "full_name": "John Doe",
                "first_name": "John",
                "last_name": "Doe",
                "profile_url": "https://www.linkedin.com/in/johndoe",
                "location": "Miami, Florida, US",
                "summary": "Test Summary",
                "company_url": "https://example.com/",
                "company_name": "Test Company Name",
                "position": "CEO at Test",
                "about": "Test About",
                "email_address": "johndoe@test.com",
                "enriched_email": None,
                "custom_email": None,
                "tags": ["TagTest"],
                "lists": [
                    {
                        "name": "My List 1",
                        "id": 123,
                        "custom_fields": {
                            "Favorite_cookie": "Chocolate chip",
                            "Gender": "Male"
                        }
                    }
                ]
            }
        }

        data = HeyReachWebhookPayload(**payload)

        # Verify extended lead fields are parsed
        assert data.lead.summary == "Test Summary"
        assert data.lead.about == "Test About"
        assert data.lead.enriched_email is None
        assert data.lead.custom_email is None
        assert data.lead.tags == ["TagTest"]
        assert len(data.lead.lists) == 1
        assert data.lead.lists[0].name == "My List 1"
        assert data.lead.lists[0].id == 123
        assert data.lead.lists[0].custom_fields["Favorite_cookie"] == "Chocolate chip"

    def test_lead_personalized_message_helper_property(self):
        """Should access personalized_message via helper property."""
        payload = {
            "recent_messages": [
                {"creation_time": "2026-01-31T16:22:24Z", "message": "Hi"}
            ],
            "conversation_id": "conv_123",
            "sender": {"id": 123},
            "lead": {
                "full_name": "Jane Smith",
                "lists": [
                    {
                        "name": "Outreach List",
                        "id": 456,
                        "custom_fields": {
                            "personalized_message": "Hey Jane, saw your work at Acme - impressive stuff!"
                        }
                    }
                ]
            }
        }

        data = HeyReachWebhookPayload(**payload)

        # Access via helper property
        assert data.lead.personalized_message == "Hey Jane, saw your work at Acme - impressive stuff!"

    def test_lead_personalized_message_returns_none_when_missing(self):
        """Should return None when personalized_message is not in custom_fields."""
        payload = {
            "recent_messages": [
                {"creation_time": "2026-01-31T16:22:24Z", "message": "Hi"}
            ],
            "conversation_id": "conv_123",
            "sender": {"id": 123},
            "lead": {
                "full_name": "No Message Lead",
                "lists": [
                    {
                        "name": "Some List",
                        "id": 789,
                        "custom_fields": {"other_field": "value"}
                    }
                ]
            }
        }

        data = HeyReachWebhookPayload(**payload)
        assert data.lead.personalized_message is None

    def test_lead_personalized_message_no_lists(self):
        """Should return None when lead has no lists."""
        payload = {
            "recent_messages": [
                {"creation_time": "2026-01-31T16:22:24Z", "message": "Hi"}
            ],
            "conversation_id": "conv_123",
            "sender": {"id": 123},
            "lead": {"full_name": "No Lists Lead"}
        }

        data = HeyReachWebhookPayload(**payload)
        assert data.lead.personalized_message is None
