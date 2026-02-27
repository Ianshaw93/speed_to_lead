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

        assert len(actions) == 1  # Single consolidated actions block

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


class TestReportBlocksWithSpeedMetrics:
    """Tests for report block formatting with speed metrics."""

    def test_build_daily_report_with_speed_metrics(self):
        """Should include speed metrics in daily report."""
        from datetime import date
        from app.services.slack import build_daily_report_blocks

        metrics = {
            "outreach": {
                "profiles_scraped": 100,
                "icp_qualified": 40,
                "heyreach_uploaded": 35,
                "costs": {"apify": 1.50, "deepseek": 0.30},
            },
            "conversations": {
                "new": 5,
                "drafts_approved": 10,
                "classifications": {"positive": 3, "not_interested": 1, "not_icp": 1},
            },
            "funnel": {
                "positive_reply": 15,
                "pitched": 8,
                "booked": 2,
            },
            "content": {
                "drafts_created": 3,
                "drafts_scheduled": 2,
                "drafts_posted": 1,
            },
            "speed_metrics": {
                "speed_to_lead": {"avg_minutes": 135, "count": 12},
                "speed_to_reply": {"avg_minutes": 45, "count": 28},
            },
        }

        blocks = build_daily_report_blocks(date(2026, 2, 9), metrics)
        block_text = str(blocks)

        # Should include speed metrics section
        assert "Response Speed" in block_text or "Speed to Lead" in block_text
        assert "2h 15m" in block_text  # 135 minutes formatted
        assert "45m" in block_text  # 45 minutes formatted
        assert "12" in block_text  # count
        assert "28" in block_text  # count

    def test_build_daily_report_no_speed_metrics(self):
        """Should handle missing speed metrics gracefully."""
        from datetime import date
        from app.services.slack import build_daily_report_blocks

        metrics = {
            "outreach": {
                "profiles_scraped": 100,
                "icp_qualified": 40,
                "heyreach_uploaded": 35,
                "costs": {"apify": 1.50, "deepseek": 0.30},
            },
            "conversations": {
                "new": 5,
                "drafts_approved": 10,
                "classifications": {"positive": 3},
            },
            "funnel": {
                "positive_reply": 15,
                "pitched": 8,
                "booked": 2,
            },
            "content": {
                "drafts_created": 3,
                "drafts_scheduled": 2,
                "drafts_posted": 1,
            },
            # No speed_metrics key
        }

        # Should not raise an error
        blocks = build_daily_report_blocks(date(2026, 2, 9), metrics)
        assert len(blocks) > 0

    def test_build_weekly_report_with_speed_metrics(self):
        """Should include speed metrics in weekly report."""
        from datetime import date
        from app.services.slack import build_weekly_report_blocks

        metrics = {
            "outreach": {
                "profiles_scraped": 700,
                "icp_qualified": 280,
                "icp_rate": 40,
                "heyreach_uploaded": 250,
                "costs": {"apify": 10.50, "deepseek": 2.10, "total": 12.60},
            },
            "conversations": {
                "new": 35,
                "drafts_approved": 70,
                "positive_reply_rate": 42.5,
                "classifications": {"positive": 21, "not_interested": 7, "not_icp": 7},
            },
            "funnel": {
                "positive_reply": 45,
                "pitched": 25,
                "calendar_sent": 10,
                "booked": 8,
            },
            "content": {
                "drafts_created": 21,
                "drafts_scheduled": 14,
                "drafts_posted": 7,
            },
            "speed_metrics": {
                "speed_to_lead": {"avg_minutes": 180, "count": 45},
                "speed_to_reply": {"avg_minutes": 52, "count": 120},
            },
        }

        blocks = build_weekly_report_blocks(date(2026, 2, 3), date(2026, 2, 9), metrics)
        block_text = str(blocks)

        # Should include speed metrics
        assert "3h" in block_text  # 180 minutes = 3h
        assert "52m" in block_text or "52" in block_text  # 52 minutes
