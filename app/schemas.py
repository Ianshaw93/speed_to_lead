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
    telegram_message_id: int | None = None


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
    telegram_message_id: int | None
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
class HeyReachWebhookPayload(BaseModel):
    """Schema for incoming HeyReach webhook payload."""

    lead_id: str = Field(..., alias="leadId")
    linkedin_url: str = Field(..., alias="linkedinUrl")
    lead_name: str = Field(..., alias="leadName")
    message_content: str = Field(..., alias="messageContent")
    lead_title: str | None = Field(None, alias="leadTitle")
    lead_company: str | None = Field(None, alias="leadCompany")

    model_config = ConfigDict(populate_by_name=True)


class HeyReachSendMessageRequest(BaseModel):
    """Schema for sending a message via HeyReach API."""

    lead_id: str
    message: str


class HeyReachSendMessageResponse(BaseModel):
    """Schema for HeyReach send message API response."""

    success: bool
    message_id: str | None = None
    error: str | None = None


# Telegram callback schemas
class TelegramCallbackData(BaseModel):
    """Schema for Telegram callback button data."""

    action: str
    draft_id: uuid.UUID
    extra: str | None = None


# Health check schema
class HealthResponse(BaseModel):
    """Schema for health check response."""

    status: str
    environment: str
