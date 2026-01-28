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

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    company_url: str | None = None
    email_address: str | None = None
    profile_url: str | None = None
    position: str | None = None
    location: str | None = None


class HeyReachMessage(BaseModel):
    """A message in the conversation history."""

    model_config = ConfigDict(extra="ignore")

    creation_time: str
    message: str
    is_reply: bool | None = None
    message_type: str | None = None


class HeyReachSender(BaseModel):
    """Sender information (LinkedIn account)."""

    model_config = ConfigDict(extra="ignore")

    id: int | str  # Can be int or str
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email_address: str | None = None
    profile_url: str | None = None


class HeyReachCampaign(BaseModel):
    """Campaign information."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    name: str | None = None
    status: str | None = None


class HeyReachWebhookPayload(BaseModel):
    """Schema for incoming HeyReach webhook payload."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Core fields
    lead: HeyReachLead
    recent_messages: list[HeyReachMessage]
    conversation_id: str
    sender: HeyReachSender

    # Optional fields
    campaign: HeyReachCampaign | None = None
    is_inmail: bool | None = None
    timestamp: str | None = None
    event_type: str | None = None
    correlation_id: str | None = None

    @property
    def lead_name(self) -> str:
        """Get the lead's full name."""
        return self.lead.full_name

    @property
    def lead_company(self) -> str | None:
        """Get the lead's company name."""
        return self.lead.company_name

    @property
    def lead_title(self) -> str | None:
        """Get the lead's position/title."""
        return self.lead.position

    @property
    def linkedin_profile_url(self) -> str | None:
        """Get the lead's LinkedIn profile URL."""
        return self.lead.profile_url

    @property
    def linkedin_account_id(self) -> str:
        """Get the LinkedIn account ID for sending replies."""
        return str(self.sender.id)

    @property
    def latest_message(self) -> str:
        """Get the most recent message content."""
        if self.recent_messages:
            # Find the most recent non-empty message
            for msg in reversed(self.recent_messages):
                if msg.message:
                    return msg.message
        return ""

    @property
    def all_recent_messages(self) -> list[HeyReachMessage]:
        """Get all recent messages."""
        return self.recent_messages


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
