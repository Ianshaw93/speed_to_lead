"""Telegram bot service for sending draft notifications."""

import uuid
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings


class TelegramError(Exception):
    """Custom exception for Telegram API errors."""

    pass


def build_draft_message(
    lead_name: str,
    lead_title: str | None,
    lead_company: str | None,
    linkedin_url: str,
    lead_message: str,
    ai_draft: str,
) -> str:
    """Build a formatted message for draft notification.

    Args:
        lead_name: Name of the lead.
        lead_title: Lead's job title (optional).
        lead_company: Lead's company (optional).
        linkedin_url: LinkedIn profile URL.
        lead_message: The lead's message.
        ai_draft: The AI-generated draft reply.

    Returns:
        Formatted message string.
    """
    # Build lead info line
    lead_info = lead_name
    if lead_title and lead_company:
        lead_info = f"{lead_name} ({lead_title} @ {lead_company})"
    elif lead_title:
        lead_info = f"{lead_name} ({lead_title})"
    elif lead_company:
        lead_info = f"{lead_name} @ {lead_company}"

    message = f"""📩 *New LinkedIn Reply*

*From:* {lead_info}
*LinkedIn:* [Profile]({linkedin_url})

*Their Message:*
_{lead_message}_

---
🤖 *Suggested Reply:*
{ai_draft}
"""
    return message


def build_inline_keyboard(draft_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Build inline keyboard with action buttons.

    Args:
        draft_id: The draft ID to include in callback data.

    Returns:
        InlineKeyboardMarkup with action buttons.
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ Send", callback_data=f"approve:{draft_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{draft_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Regenerate", callback_data=f"regenerate:{draft_id}"),
            InlineKeyboardButton("❌ Skip", callback_data=f"reject:{draft_id}"),
        ],
        [
            InlineKeyboardButton("⏰ Snooze", callback_data=f"snooze_menu:{draft_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_snooze_keyboard(draft_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Build inline keyboard for snooze options.

    Args:
        draft_id: The draft ID to include in callback data.

    Returns:
        InlineKeyboardMarkup with snooze options.
    """
    keyboard = [
        [
            InlineKeyboardButton("1 hour", callback_data=f"snooze:{draft_id}:1h"),
            InlineKeyboardButton("4 hours", callback_data=f"snooze:{draft_id}:4h"),
        ],
        [
            InlineKeyboardButton("Tomorrow 9am", callback_data=f"snooze:{draft_id}:tomorrow"),
            InlineKeyboardButton("Cancel", callback_data=f"snooze_cancel:{draft_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def parse_callback_data(data: str) -> tuple[str, uuid.UUID, str | None]:
    """Parse callback data from inline keyboard button.

    Args:
        data: Callback data string in format "action:uuid[:extra]".

    Returns:
        Tuple of (action, draft_id, extra_data).

    Raises:
        ValueError: If the callback data format is invalid.
    """
    parts = data.split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid callback data format: {data}")

    action = parts[0]
    try:
        draft_id = uuid.UUID(parts[1])
    except ValueError as e:
        raise ValueError(f"Invalid draft ID in callback data: {parts[1]}") from e

    extra = parts[2] if len(parts) > 2 else None

    return action, draft_id, extra


class TelegramBot:
    """Client for sending Telegram notifications."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
    ):
        """Initialize the Telegram bot.

        Args:
            token: Telegram bot token. Defaults to settings value.
            chat_id: Chat ID to send messages to. Defaults to settings value.
        """
        self._token = token or settings.telegram_bot_token
        self._chat_id = chat_id or settings.telegram_chat_id
        self._bot = Bot(token=self._token)

    async def send_draft_notification(
        self,
        draft_id: uuid.UUID,
        lead_name: str,
        lead_title: str | None,
        lead_company: str | None,
        linkedin_url: str,
        lead_message: str,
        ai_draft: str,
    ) -> int:
        """Send a draft notification to Telegram.

        Args:
            draft_id: The draft ID for callback data.
            lead_name: Name of the lead.
            lead_title: Lead's job title.
            lead_company: Lead's company.
            linkedin_url: LinkedIn profile URL.
            lead_message: The lead's message.
            ai_draft: The AI-generated draft reply.

        Returns:
            The Telegram message ID.

        Raises:
            TelegramError: If sending fails.
        """
        try:
            message_text = build_draft_message(
                lead_name=lead_name,
                lead_title=lead_title,
                lead_company=lead_company,
                linkedin_url=linkedin_url,
                lead_message=lead_message,
                ai_draft=ai_draft,
            )

            keyboard = build_inline_keyboard(draft_id)

            message = await self._bot.send_message(
                chat_id=self._chat_id,
                text=message_text,
                parse_mode="Markdown",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

            return message.message_id

        except Exception as e:
            raise TelegramError(f"Failed to send Telegram notification: {e}") from e

    async def update_message(
        self,
        message_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Update an existing message.

        Args:
            message_id: The message ID to update.
            text: New message text.
            keyboard: Optional new keyboard.

        Raises:
            TelegramError: If update fails.
        """
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=message_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError(f"Failed to update Telegram message: {e}") from e

    async def remove_keyboard(self, message_id: int) -> None:
        """Remove inline keyboard from a message.

        Args:
            message_id: The message ID to update.

        Raises:
            TelegramError: If update fails.
        """
        try:
            await self._bot.edit_message_reply_markup(
                chat_id=self._chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except Exception as e:
            raise TelegramError(f"Failed to remove keyboard: {e}") from e

    async def send_confirmation(self, text: str) -> int:
        """Send a simple confirmation message.

        Args:
            text: Confirmation message text.

        Returns:
            The message ID.

        Raises:
            TelegramError: If sending fails.
        """
        try:
            message = await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
            )
            return message.message_id
        except Exception as e:
            raise TelegramError(f"Failed to send confirmation: {e}") from e

    async def ask_for_edit(self, draft_id: uuid.UUID) -> int:
        """Ask the user to provide an edited message.

        Args:
            draft_id: The draft ID being edited.

        Returns:
            The message ID.

        Raises:
            TelegramError: If sending fails.
        """
        text = "📝 Please reply with your edited message:"
        return await self.send_confirmation(text)

    async def ask_for_regeneration_guidance(self, draft_id: uuid.UUID) -> int:
        """Ask the user for regeneration guidance.

        Args:
            draft_id: The draft ID being regenerated.

        Returns:
            The message ID.

        Raises:
            TelegramError: If sending fails.
        """
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Skip (no guidance)", callback_data=f"regenerate_now:{draft_id}")]
        ])

        try:
            message = await self._bot.send_message(
                chat_id=self._chat_id,
                text="🔄 Any specific direction for the new draft? Reply with guidance or tap Skip:",
                reply_markup=keyboard,
            )
            return message.message_id
        except Exception as e:
            raise TelegramError(f"Failed to ask for guidance: {e}") from e

    async def show_snooze_options(self, message_id: int, draft_id: uuid.UUID) -> None:
        """Update message to show snooze options.

        Args:
            message_id: The message ID to update.
            draft_id: The draft ID.

        Raises:
            TelegramError: If update fails.
        """
        try:
            keyboard = build_snooze_keyboard(draft_id)
            await self._bot.edit_message_reply_markup(
                chat_id=self._chat_id,
                message_id=message_id,
                reply_markup=keyboard,
            )
        except Exception as e:
            raise TelegramError(f"Failed to show snooze options: {e}") from e


# Global bot instance
_bot: TelegramBot | None = None


def get_telegram_bot() -> TelegramBot:
    """Get or create the Telegram bot singleton."""
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot
