"""Tests for Telegram bot service."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.telegram import (
    TelegramBot,
    TelegramError,
    build_draft_message,
    build_inline_keyboard,
    parse_callback_data,
)


class TestMessageFormatting:
    """Tests for Telegram message formatting."""

    def test_build_draft_message_basic(self):
        """Should build a properly formatted draft message."""
        message = build_draft_message(
            lead_name="John Doe",
            lead_title="VP Engineering",
            lead_company="Acme Corp",
            linkedin_url="https://linkedin.com/in/johndoe",
            lead_message="I'm interested in learning more!",
            ai_draft="Hi John! Thanks for your interest...",
        )

        assert "John Doe" in message
        assert "VP Engineering" in message
        assert "Acme Corp" in message
        assert "interested in learning more" in message
        assert "Hi John! Thanks" in message
        assert "linkedin.com" in message

    def test_build_draft_message_no_title_company(self):
        """Should handle missing title and company."""
        message = build_draft_message(
            lead_name="Jane Smith",
            lead_title=None,
            lead_company=None,
            linkedin_url="https://linkedin.com/in/janesmith",
            lead_message="Hello!",
            ai_draft="Hi Jane!",
        )

        assert "Jane Smith" in message
        assert "Hello!" in message
        assert "Hi Jane!" in message

    def test_build_inline_keyboard(self):
        """Should build inline keyboard with all action buttons."""
        draft_id = uuid.uuid4()
        keyboard = build_inline_keyboard(draft_id)

        # Should have action buttons
        assert keyboard is not None
        # Verify it's the right structure for telegram keyboard
        assert hasattr(keyboard, "inline_keyboard")

    def test_parse_callback_data_approve(self):
        """Should parse approve callback data."""
        draft_id = uuid.uuid4()
        callback_data = f"approve:{draft_id}"

        action, parsed_id, extra = parse_callback_data(callback_data)

        assert action == "approve"
        assert parsed_id == draft_id
        assert extra is None

    def test_parse_callback_data_snooze_with_duration(self):
        """Should parse snooze callback with duration."""
        draft_id = uuid.uuid4()
        callback_data = f"snooze:{draft_id}:1h"

        action, parsed_id, extra = parse_callback_data(callback_data)

        assert action == "snooze"
        assert parsed_id == draft_id
        assert extra == "1h"

    def test_parse_callback_data_invalid(self):
        """Should raise error for invalid callback data."""
        with pytest.raises(ValueError):
            parse_callback_data("invalid")


class TestTelegramBot:
    """Tests for the Telegram bot client."""

    @pytest.mark.asyncio
    async def test_send_draft_notification_success(self):
        """Should send a draft notification with inline keyboard."""
        draft_id = uuid.uuid4()

        with patch("app.services.telegram.Bot") as MockBot:
            mock_bot_instance = MagicMock()
            mock_message = MagicMock()
            mock_message.message_id = 12345
            mock_bot_instance.send_message = AsyncMock(return_value=mock_message)
            MockBot.return_value = mock_bot_instance

            bot = TelegramBot(token="test_token", chat_id="123456")
            message_id = await bot.send_draft_notification(
                draft_id=draft_id,
                lead_name="John Doe",
                lead_title="CEO",
                lead_company="Tech Co",
                linkedin_url="https://linkedin.com/in/john",
                lead_message="Interested!",
                ai_draft="Great to hear!",
            )

            assert message_id == 12345
            mock_bot_instance.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_draft_notification_error(self):
        """Should raise TelegramError on failure."""
        draft_id = uuid.uuid4()

        with patch("app.services.telegram.Bot") as MockBot:
            mock_bot_instance = MagicMock()
            mock_bot_instance.send_message = AsyncMock(side_effect=Exception("Telegram API error"))
            MockBot.return_value = mock_bot_instance

            bot = TelegramBot(token="test_token", chat_id="123456")

            with pytest.raises(TelegramError) as exc_info:
                await bot.send_draft_notification(
                    draft_id=draft_id,
                    lead_name="John",
                    lead_title=None,
                    lead_company=None,
                    linkedin_url="https://linkedin.com/in/john",
                    lead_message="Hello",
                    ai_draft="Hi!",
                )

            assert "Telegram API error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_update_message_success(self):
        """Should update an existing message."""
        with patch("app.services.telegram.Bot") as MockBot:
            mock_bot_instance = MagicMock()
            mock_bot_instance.edit_message_text = AsyncMock()
            MockBot.return_value = mock_bot_instance

            bot = TelegramBot(token="test_token", chat_id="123456")
            await bot.update_message(
                message_id=12345,
                text="Updated message content",
            )

            mock_bot_instance.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_keyboard(self):
        """Should remove inline keyboard from message."""
        with patch("app.services.telegram.Bot") as MockBot:
            mock_bot_instance = MagicMock()
            mock_bot_instance.edit_message_reply_markup = AsyncMock()
            MockBot.return_value = mock_bot_instance

            bot = TelegramBot(token="test_token", chat_id="123456")
            await bot.remove_keyboard(message_id=12345)

            mock_bot_instance.edit_message_reply_markup.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_confirmation(self):
        """Should send a confirmation message."""
        with patch("app.services.telegram.Bot") as MockBot:
            mock_bot_instance = MagicMock()
            mock_message = MagicMock()
            mock_message.message_id = 99999
            mock_bot_instance.send_message = AsyncMock(return_value=mock_message)
            MockBot.return_value = mock_bot_instance

            bot = TelegramBot(token="test_token", chat_id="123456")
            await bot.send_confirmation("Message sent successfully!")

            mock_bot_instance.send_message.assert_called_once()

    def test_bot_initialization(self):
        """Should initialize with correct token and chat ID."""
        with patch("app.services.telegram.Bot"):
            bot = TelegramBot(token="my_token", chat_id="my_chat")
            assert bot._chat_id == "my_chat"
