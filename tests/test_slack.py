"""Tests for Slack service."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.slack import (
    SlackBot,
    SlackError,
    build_action_buttons,
    build_draft_message,
    parse_action_payload,
)


class TestMessageFormatting:
    """Tests for Slack message formatting."""

    def test_build_draft_message_basic(self):
        """Should build a properly formatted draft message."""
        blocks = build_draft_message(
            lead_name="John Doe",
            lead_title="VP Engineering",
            lead_company="Acme Corp",
            linkedin_url="https://linkedin.com/in/johndoe",
            lead_message="I'm interested in learning more!",
            ai_draft="Hi John! Thanks for your interest...",
        )

        assert len(blocks) > 0
        # Check header
        assert blocks[0]["type"] == "header"
        assert "New LinkedIn Reply" in blocks[0]["text"]["text"]

        # Check that lead info is in the blocks
        block_text = str(blocks)
        assert "John Doe" in block_text
        assert "VP Engineering" in block_text
        assert "Acme Corp" in block_text

    def test_build_draft_message_no_title_company(self):
        """Should handle missing title and company."""
        blocks = build_draft_message(
            lead_name="Jane Smith",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/janesmith",
            lead_message="Hello!",
            ai_draft="Hi Jane!",
        )

        block_text = str(blocks)
        assert "Jane Smith" in block_text
        assert "Hello!" in block_text
        assert "Hi Jane!" in block_text

    def test_build_action_buttons(self):
        """Should build action buttons with correct actions."""
        draft_id = uuid.uuid4()
        actions = build_action_buttons(draft_id)

        assert len(actions) == 2  # Two action blocks

        # Flatten all elements
        all_elements = []
        for action_block in actions:
            all_elements.extend(action_block.get("elements", []))

        action_ids = [el.get("action_id") for el in all_elements]
        assert "approve" in action_ids
        assert "edit" in action_ids
        assert "regenerate" in action_ids
        assert "reject" in action_ids
        assert "snooze_1h" in action_ids

    def test_parse_action_payload_approve(self):
        """Should parse approve action payload."""
        draft_id = uuid.uuid4()
        payload = {
            "actions": [
                {
                    "action_id": "approve",
                    "value": str(draft_id)
                }
            ]
        }

        action_id, parsed_id = parse_action_payload(payload)

        assert action_id == "approve"
        assert parsed_id == draft_id

    def test_parse_action_payload_snooze(self):
        """Should parse snooze action payload."""
        draft_id = uuid.uuid4()
        payload = {
            "actions": [
                {
                    "action_id": "snooze_1h",
                    "value": str(draft_id)
                }
            ]
        }

        action_id, parsed_id = parse_action_payload(payload)

        assert action_id == "snooze_1h"
        assert parsed_id == draft_id

    def test_parse_action_payload_invalid(self):
        """Should raise error for invalid payload."""
        with pytest.raises(ValueError):
            parse_action_payload({})

        with pytest.raises(ValueError):
            parse_action_payload({"actions": []})


class TestSlackBot:
    """Tests for the Slack bot client."""

    @pytest.mark.asyncio
    async def test_send_draft_notification_success(self):
        """Should send a draft notification with action buttons."""
        draft_id = uuid.uuid4()

        with patch("app.services.slack.AsyncWebClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ts": "1234567890.123456"})
            MockClient.return_value = mock_client

            bot = SlackBot(bot_token="test_token", channel_id="C123456")
            message_ts = await bot.send_draft_notification(
                draft_id=draft_id,
                lead_name="John Doe",
                lead_title="CEO",
                lead_company="Tech Co",
                linkedin_url="https://linkedin.com/in/john",
                lead_message="Interested!",
                ai_draft="Great to hear!",
            )

            assert message_ts == "1234567890.123456"
            mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_draft_notification_error(self):
        """Should raise SlackError on failure."""
        draft_id = uuid.uuid4()

        with patch("app.services.slack.AsyncWebClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(side_effect=Exception("Slack API error"))
            MockClient.return_value = mock_client

            bot = SlackBot(bot_token="test_token", channel_id="C123456")

            with pytest.raises(SlackError) as exc_info:
                await bot.send_draft_notification(
                    draft_id=draft_id,
                    lead_name="John",
                    lead_title=None,
                    lead_company=None,
                    linkedin_url="https://linkedin.com/in/john",
                    lead_message="Hello",
                    ai_draft="Hi!",
                )

            assert "Slack API error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_update_message_success(self):
        """Should update an existing message."""
        with patch("app.services.slack.AsyncWebClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat_update = AsyncMock(return_value={"ok": True})
            MockClient.return_value = mock_client

            bot = SlackBot(bot_token="test_token", channel_id="C123456")
            await bot.update_message(
                message_ts="1234567890.123456",
                text="Updated message content",
            )

            mock_client.chat_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_buttons(self):
        """Should remove action buttons from message."""
        with patch("app.services.slack.AsyncWebClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat_update = AsyncMock(return_value={"ok": True})
            MockClient.return_value = mock_client

            bot = SlackBot(bot_token="test_token", channel_id="C123456")
            await bot.remove_buttons(
                message_ts="1234567890.123456",
                final_text="✅ Message sent successfully!"
            )

            mock_client.chat_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_confirmation(self):
        """Should send a confirmation message."""
        with patch("app.services.slack.AsyncWebClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ts": "9999999999.999999"})
            MockClient.return_value = mock_client

            bot = SlackBot(bot_token="test_token", channel_id="C123456")
            ts = await bot.send_confirmation("Message sent successfully!")

            assert ts == "9999999999.999999"
            mock_client.chat_postMessage.assert_called_once()

    def test_bot_initialization(self):
        """Should initialize with correct token and channel ID."""
        with patch("app.services.slack.AsyncWebClient"):
            bot = SlackBot(bot_token="my_token", channel_id="my_channel")
            assert bot._channel_id == "my_channel"
