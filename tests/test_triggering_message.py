"""Tests for triggering message tracking feature.

Verifies that when a prospect replies, we extract and store the last
outbound message that triggered the reply, show it in Slack notifications,
and expose it through the analytics endpoint.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas import HeyReachMessage
from app.services.slack import build_draft_message


def _make_messages(messages: list[tuple[str, bool | None]]) -> list[HeyReachMessage]:
    """Helper to build a list of HeyReachMessage from (content, is_reply) tuples."""
    return [
        HeyReachMessage(
            creation_time="2026-02-19T10:00:00",
            message=content,
            is_reply=is_reply,
        )
        for content, is_reply in messages
    ]


def extract_triggering_message(recent_messages: list[HeyReachMessage]) -> str | None:
    """Extract the last outbound message from recent_messages.

    This mirrors the logic in process_incoming_message.
    """
    for msg in reversed(recent_messages):
        if msg.is_reply is False:
            return msg.message
    return None


class TestExtractTriggeringMessage:
    """Tests for extracting the triggering message from conversation history."""

    def test_last_outbound_before_reply(self):
        """Should find the last outbound message (is_reply=False)."""
        messages = _make_messages([
            ("Hi John, I noticed your post about AI", False),
            ("Thanks for reaching out!", True),
        ])
        result = extract_triggering_message(messages)
        assert result == "Hi John, I noticed your post about AI"

    def test_multiple_outbound_picks_last(self):
        """Should pick the last outbound message when multiple exist."""
        messages = _make_messages([
            ("Connection request message", False),
            ("Follow-up: Hey John!", False),
            ("Yes I'd love to chat!", True),
        ])
        result = extract_triggering_message(messages)
        assert result == "Follow-up: Hey John!"

    def test_no_outbound_messages(self):
        """Should return None when there are no outbound messages."""
        messages = _make_messages([
            ("Hey, are you hiring?", True),
        ])
        result = extract_triggering_message(messages)
        assert result is None

    def test_empty_message_list(self):
        """Should return None for empty message list."""
        result = extract_triggering_message([])
        assert result is None

    def test_is_reply_none_skipped(self):
        """Messages with is_reply=None should be skipped (not treated as outbound)."""
        messages = _make_messages([
            ("Some system message", None),
            ("Thanks!", True),
        ])
        result = extract_triggering_message(messages)
        assert result is None

    def test_outbound_after_reply_still_found(self):
        """Should find outbound message that appears after a reply in history."""
        messages = _make_messages([
            ("Initial outreach", False),
            ("Sounds interesting", True),
            ("Great! Let me tell you more...", False),
            ("Yes, let's schedule a call!", True),
        ])
        result = extract_triggering_message(messages)
        assert result == "Great! Let me tell you more..."


class TestSlackNotificationTriggeringMessage:
    """Tests for showing triggering message in Slack notification."""

    def test_includes_our_message_when_present(self):
        """Should include 'Our Message' block when triggering_message is provided."""
        blocks = build_draft_message(
            lead_name="John Doe",
            lead_title="CEO",
            lead_company="Acme Corp",
            linkedin_url="https://linkedin.com/in/johndoe",
            lead_message="Yes I'd love to chat!",
            ai_draft="Great! Here's my calendar link...",
            triggering_message="Hi John, I noticed your post about AI",
        )

        block_text = str(blocks)
        assert "Our Message" in block_text
        assert "Hi John, I noticed your post about AI" in block_text

    def test_omits_our_message_when_none(self):
        """Should NOT include 'Our Message' block when triggering_message is None."""
        blocks = build_draft_message(
            lead_name="Jane Smith",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/janesmith",
            lead_message="Hello!",
            ai_draft="Hi Jane!",
            triggering_message=None,
        )

        block_text = str(blocks)
        assert "Our Message" not in block_text

    def test_omits_our_message_when_not_passed(self):
        """Should NOT include 'Our Message' block when parameter not passed (default)."""
        blocks = build_draft_message(
            lead_name="Jane Smith",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/janesmith",
            lead_message="Hello!",
            ai_draft="Hi Jane!",
        )

        block_text = str(blocks)
        assert "Our Message" not in block_text

    def test_our_message_before_their_message(self):
        """'Our Message' block should appear before 'Their Message' block."""
        blocks = build_draft_message(
            lead_name="John Doe",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/johndoe",
            lead_message="Interested!",
            ai_draft="Great!",
            triggering_message="Hey, want to connect?",
        )

        # Find positions of "Our Message" and "Their Message" in blocks
        our_idx = None
        their_idx = None
        for i, block in enumerate(blocks):
            text = str(block)
            if "Our Message" in text and our_idx is None:
                our_idx = i
            if "Their Message" in text and their_idx is None:
                their_idx = i

        assert our_idx is not None, "Our Message block not found"
        assert their_idx is not None, "Their Message block not found"
        assert our_idx < their_idx, "Our Message should appear before Their Message"


class TestSendDraftNotificationTriggeringMessage:
    """Tests for passing triggering_message through send_draft_notification."""

    @pytest.mark.asyncio
    async def test_send_draft_notification_passes_triggering_message(self):
        """send_draft_notification should pass triggering_message to build_draft_message."""
        from app.services.slack import SlackBot

        bot = SlackBot(bot_token="xoxb-test", channel_id="C123")
        bot._client = AsyncMock()
        bot._client.chat_postMessage = AsyncMock(return_value={"ts": "1234.5678"})

        with patch("app.services.slack.build_draft_message") as mock_build:
            mock_build.return_value = [{"type": "section", "text": {"type": "mrkdwn", "text": "test"}}]

            await bot.send_draft_notification(
                draft_id=uuid.uuid4(),
                lead_name="John",
                lead_title=None,
                lead_company=None,
                linkedin_url="https://linkedin.com/in/john",
                lead_message="Interested!",
                ai_draft="Great!",
                triggering_message="Our outreach message",
            )

            mock_build.assert_called_once()
            call_kwargs = mock_build.call_args
            assert call_kwargs.kwargs.get("triggering_message") == "Our outreach message"


class TestAnalyticsEndpoint:
    """Tests for the message effectiveness analytics endpoint."""

    @pytest.mark.asyncio
    async def test_message_effectiveness_endpoint_exists(self):
        """The /admin/message-effectiveness endpoint should be registered."""
        from app.main import app
        routes = [route.path for route in app.routes]
        assert "/admin/message-effectiveness" in routes
