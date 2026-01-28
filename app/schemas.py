"""Pydantic schemas for request/response validation."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import DraftStatus, MessageDirection


# Base schemas
class ConversationBase(BaseModel):
    """Base schema for conversation data."""

    heyreach_lead_id: str
    linkedin_profile_url: str
    lead_name: str
    conversation_history: list[dict[str, Any]] | None = Field(default_factory=list)


class ConversationCreate(ConversationBase):
    """Schema for creating a conversation."""

    pass


class ConversationResponse(ConversationBase):
    """Schema for conversation responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DraftBase(BaseModel):
    """Base schema for draft data."""

    ai_draft: str
    status: DraftStatus = DraftStatus.PENDING


class DraftCreate(DraftBase):
    """Schema for creating a draft."""

    conversation_id: uuid.UUID
    slack_message_ts: str | None = None


class DraftUpdate(BaseModel):
    """Schema for updating a draft."""

    status: DraftStatus | None = None
    ai_draft: str | None = None
    snooze_until: datetime | None = None


class DraftResponse(DraftBase):
    """Schema for draft responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    slack_message_ts: str | None
    snooze_until: datetime | None
    created_at: datetime
    updated_at: datetime


class MessageLogBase(BaseModel):
    """Base schema for message log data."""

    direction: MessageDirection
    content: str


class MessageLogCreate(MessageLogBase):
    """Schema for creating a message log entry."""

    conversation_id: uuid.UUID


class MessageLogResponse(MessageLogBase):
    """Schema for message log responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    sent_at: datetime


# HeyReach webhook schemas
class HeyReachLead(BaseModel):
    """Lead information from HeyReach webhook."""

    full_name: str
    company_name: str | None = None
    company_url: str | None = None
    email_address: str | None = None


class HeyReachMessage(BaseModel):
    """A message in the conversation history."""

    creation_time: str
    message: str


class HeyReachSender(BaseModel):
    """Sender information (LinkedIn account)."""

    id: str


class HeyReachWebhookBody(BaseModel):
    """Inner body of HeyReach webhook payload."""

    lead: HeyReachLead
    recent_messages: list[HeyReachMessage]
    conversation_id: str
    sender: HeyReachSender


class HeyReachWebhookPayload(BaseModel):
    """Schema for incoming HeyReach webhook payload.

    HeyReach wraps the data in a 'body' field.
    """

    body: HeyReachWebhookBody

    @property
    def lead_name(self) -> str:
        """Get the lead's full name."""
        return self.body.lead.full_name

    @property
    def lead_company(self) -> str | None:
        """Get the lead's company name."""
        return self.body.lead.company_name

    @property
    def linkedin_account_id(self) -> str:
        """Get the LinkedIn account ID for sending replies."""
        return self.body.sender.id

    @property
    def conversation_id(self) -> str:
        """Get the conversation ID."""
        return self.body.conversation_id

    @property
    def latest_message(self) -> str:
        """Get the most recent message content."""
        if self.body.recent_messages:
            return self.body.recent_messages[-1].message
        return ""

    @property
    def recent_messages(self) -> list[HeyReachMessage]:
        """Get all recent messages."""
        return self.body.recent_messages


class HeyReachSendMessageRequest(BaseModel):
    """Schema for sending a message via HeyReach API."""

    message: str
    conversation_id: str
    linkedin_account_id: str
    subject: str | None = None  # Optional, often same as message


class HeyReachSendMessageResponse(BaseModel):
    """Schema for HeyReach send message API response."""

    success: bool
    message_id: str | None = None
    error: str | None = None


# Slack action schemas
class SlackActionPayload(BaseModel):
    """Schema for Slack action payload."""

    action_id: str
    draft_id: uuid.UUID


# Health check schema
class HealthResponse(BaseModel):
    """Schema for health check response."""

    status: str
    environment: str
