"""Slack service for sending draft notifications."""

import uuid
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from app.config import settings


class SlackError(Exception):
    """Custom exception for Slack API errors."""

    pass


def build_draft_message(
    lead_name: str,
    lead_title: str | None,
    lead_company: str | None,
    linkedin_url: str,
    lead_message: str,
    ai_draft: str,
) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for draft notification.

    Args:
        lead_name: Name of the lead.
        lead_title: Lead's job title (optional).
        lead_company: Lead's company (optional).
        linkedin_url: LinkedIn profile URL.
        lead_message: The lead's message.
        ai_draft: The AI-generated draft reply.

    Returns:
        List of Slack Block Kit blocks.
    """
    # Build lead info line
    lead_info = lead_name
    if lead_title and lead_company:
        lead_info = f"{lead_name} ({lead_title} @ {lead_company})"
    elif lead_title:
        lead_info = f"{lead_name} ({lead_title})"
    elif lead_company:
        lead_info = f"{lead_name} @ {lead_company}"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📩 New LinkedIn Reply",
                "emoji": True
            }
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*From:*\n{lead_info}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*LinkedIn:*\n<{linkedin_url}|View Profile>"
                }
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Their Message:*\n_{lead_message}_"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🤖 Suggested Reply:*\n{ai_draft}"
            }
        },
        {
            "type": "divider"
        }
    ]

    return blocks


def build_action_buttons(draft_id: uuid.UUID) -> list[dict[str, Any]]:
    """Build action buttons for the draft message.

    Args:
        draft_id: The draft ID to include in action values.

    Returns:
        List of Slack Block Kit action elements.
    """
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Send", "emoji": True},
                    "style": "primary",
                    "action_id": "approve",
                    "value": str(draft_id)
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✏️ Edit", "emoji": True},
                    "action_id": "edit",
                    "value": str(draft_id)
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔄 Regenerate", "emoji": True},
                    "action_id": "regenerate",
                    "value": str(draft_id)
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Skip", "emoji": True},
                    "style": "danger",
                    "action_id": "reject",
                    "value": str(draft_id)
                }
            ]
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏰ Snooze 1h", "emoji": True},
                    "action_id": "snooze_1h",
                    "value": str(draft_id)
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏰ Snooze 4h", "emoji": True},
                    "action_id": "snooze_4h",
                    "value": str(draft_id)
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏰ Tomorrow", "emoji": True},
                    "action_id": "snooze_tomorrow",
                    "value": str(draft_id)
                }
            ]
        }
    ]


def parse_action_payload(payload: dict) -> tuple[str, uuid.UUID]:
    """Parse Slack action payload.

    Args:
        payload: Slack interaction payload.

    Returns:
        Tuple of (action_id, draft_id).

    Raises:
        ValueError: If payload format is invalid.
    """
    try:
        actions = payload.get("actions", [])
        if not actions:
            raise ValueError("No actions in payload")

        action = actions[0]
        action_id = action.get("action_id", "")
        draft_id = uuid.UUID(action.get("value", ""))

        return action_id, draft_id
    except Exception as e:
        raise ValueError(f"Invalid action payload: {e}") from e


class SlackBot:
    """Client for sending Slack notifications."""

    def __init__(
        self,
        bot_token: str | None = None,
        channel_id: str | None = None,
    ):
        """Initialize the Slack bot.

        Args:
            bot_token: Slack bot token. Defaults to settings value.
            channel_id: Channel ID to send messages to. Defaults to settings value.
        """
        self._bot_token = bot_token or settings.slack_bot_token
        self._channel_id = channel_id or settings.slack_channel_id
        self._client = AsyncWebClient(token=self._bot_token)

    async def send_draft_notification(
        self,
        draft_id: uuid.UUID,
        lead_name: str,
        lead_title: str | None,
        lead_company: str | None,
        linkedin_url: str,
        lead_message: str,
        ai_draft: str,
    ) -> str:
        """Send a draft notification to Slack.

        Args:
            draft_id: The draft ID for action values.
            lead_name: Name of the lead.
            lead_title: Lead's job title.
            lead_company: Lead's company.
            linkedin_url: LinkedIn profile URL.
            lead_message: The lead's message.
            ai_draft: The AI-generated draft reply.

        Returns:
            The Slack message timestamp (ts) for updates.

        Raises:
            SlackError: If sending fails.
        """
        try:
            blocks = build_draft_message(
                lead_name=lead_name,
                lead_title=lead_title,
                lead_company=lead_company,
                linkedin_url=linkedin_url,
                lead_message=lead_message,
                ai_draft=ai_draft,
            )
            blocks.extend(build_action_buttons(draft_id))

            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                blocks=blocks,
                text=f"New LinkedIn reply from {lead_name}",  # Fallback text
            )

            return response["ts"]

        except SlackApiError as e:
            raise SlackError(f"Failed to send Slack notification: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send Slack notification: {e}") from e

    async def update_message(
        self,
        message_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update an existing message.

        Args:
            message_ts: The message timestamp to update.
            text: New message text.
            blocks: Optional new blocks.

        Raises:
            SlackError: If update fails.
        """
        try:
            await self._client.chat_update(
                channel=self._channel_id,
                ts=message_ts,
                text=text,
                blocks=blocks,
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to update Slack message: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to update Slack message: {e}") from e

    async def remove_buttons(self, message_ts: str, final_text: str) -> None:
        """Remove action buttons from a message.

        Args:
            message_ts: The message timestamp to update.
            final_text: Text to show after buttons are removed.

        Raises:
            SlackError: If update fails.
        """
        try:
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": final_text
                    }
                }
            ]
            await self._client.chat_update(
                channel=self._channel_id,
                ts=message_ts,
                text=final_text,
                blocks=blocks,
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to remove buttons: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to remove buttons: {e}") from e

    async def send_confirmation(self, text: str) -> str:
        """Send a simple confirmation message.

        Args:
            text: Confirmation message text.

        Returns:
            The message timestamp.

        Raises:
            SlackError: If sending fails.
        """
        try:
            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                text=text,
            )
            return response["ts"]
        except SlackApiError as e:
            raise SlackError(f"Failed to send confirmation: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send confirmation: {e}") from e

    async def open_modal_for_edit(
        self,
        trigger_id: str,
        draft_id: uuid.UUID,
        current_draft: str,
    ) -> None:
        """Open a modal for editing the draft.

        Args:
            trigger_id: Slack trigger ID from the interaction.
            draft_id: The draft being edited.
            current_draft: Current draft text to pre-fill.

        Raises:
            SlackError: If opening modal fails.
        """
        try:
            await self._client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "callback_id": f"edit_draft_{draft_id}",
                    "title": {"type": "plain_text", "text": "Edit Reply"},
                    "submit": {"type": "plain_text", "text": "Send"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "draft_input",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "draft_text",
                                "multiline": True,
                                "initial_value": current_draft,
                            },
                            "label": {"type": "plain_text", "text": "Your Reply"}
                        }
                    ],
                    "private_metadata": str(draft_id),
                }
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to open modal: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to open modal: {e}") from e


# Global bot instance
_bot: SlackBot | None = None


def get_slack_bot() -> SlackBot:
    """Get or create the Slack bot singleton."""
    global _bot
    if _bot is None:
        _bot = SlackBot()
    return _bot
