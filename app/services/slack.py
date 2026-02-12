"""Slack service for sending draft notifications and reports."""

import uuid
from datetime import date
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from app.config import settings
from app.models import FunnelStage, WatchedProfileCategory
from app.services.reports import format_minutes


class SlackError(Exception):
    """Custom exception for Slack API errors."""

    pass


# Stage display names and emojis
STAGE_DISPLAY = {
    FunnelStage.INITIATED: ("1️⃣ Initiated", "Awaiting first reply"),
    FunnelStage.POSITIVE_REPLY: ("2️⃣ Positive Reply", "Building rapport"),
    FunnelStage.PITCHED: ("3️⃣ Pitched", "Call proposed"),
    FunnelStage.CALENDAR_SENT: ("4️⃣ Calendar Sent", "Awaiting booking"),
    FunnelStage.BOOKED: ("5️⃣ Booked", "Meeting confirmed"),
    FunnelStage.REGENERATION: ("🔄 Re-engagement", "Nurturing"),
}


def build_draft_message(
    lead_name: str,
    lead_title: str | None,
    lead_company: str | None,
    linkedin_url: str,
    lead_message: str,
    ai_draft: str,
    funnel_stage: FunnelStage | None = None,
    stage_reasoning: str | None = None,
) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for draft notification.

    Args:
        lead_name: Name of the lead.
        lead_title: Lead's job title (optional).
        lead_company: Lead's company (optional).
        linkedin_url: LinkedIn profile URL.
        lead_message: The lead's message.
        ai_draft: The AI-generated draft reply.
        funnel_stage: The detected funnel stage (optional).
        stage_reasoning: AI reasoning for stage detection (optional).

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
    ]

    # Add funnel stage context if available
    if funnel_stage:
        stage_label, stage_desc = STAGE_DISPLAY.get(
            funnel_stage, (funnel_stage.value, "")
        )
        stage_text = f"*Stage:* {stage_label}"
        if stage_reasoning:
            stage_text += f"\n_{stage_reasoning}_"

        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": stage_text}]
        })

    blocks.extend([
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
    ])

    return blocks


def build_classification_buttons(
    draft_id: uuid.UUID,
    is_first_reply: bool = False,
) -> list[dict[str, Any]]:
    """Build classification buttons for metrics tracking.

    Args:
        draft_id: The draft ID to include in action values.
        is_first_reply: Whether this is the first reply from the lead.
            If True, includes the "Positive Reply" button.

    Returns:
        List of Slack Block Kit blocks (context + actions).
    """
    elements = []

    # Only show Positive Reply button on first reply
    if is_first_reply:
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "\U0001f44d Positive Reply", "emoji": True},
            "action_id": "classify_positive",
            "value": str(draft_id),
        })

    # Always show Not Interested and Not ICP buttons
    elements.extend([
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "\U0001f44e Not Interested", "emoji": True},
            "action_id": "classify_not_interested",
            "value": str(draft_id),
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "\U0001f6ab Not ICP", "emoji": True},
            "action_id": "classify_not_icp",
            "value": str(draft_id),
        },
    ])

    return [
        {
            "type": "actions",
            "elements": elements,
        }
    ]


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


def build_daily_report_blocks(
    report_date: date,
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for daily metrics report.

    Args:
        report_date: The date of the report.
        metrics: Metrics dict from get_daily_dashboard_metrics.

    Returns:
        List of Slack Block Kit blocks.
    """
    outreach = metrics.get("outreach", {})
    conversations = metrics.get("conversations", {})
    funnel = metrics.get("funnel", {})
    content = metrics.get("content", {})
    costs = outreach.get("costs", {})
    classifications = conversations.get("classifications", {})
    speed_metrics = metrics.get("speed_metrics", {})

    total_cost = costs.get("apify", 0) + costs.get("deepseek", 0)

    # Extract speed metrics
    speed_to_lead = speed_metrics.get("speed_to_lead")
    speed_to_reply = speed_metrics.get("speed_to_reply")
    stl_avg = format_minutes(speed_to_lead.get("avg_minutes") if speed_to_lead else None)
    stl_count = speed_to_lead.get("count", 0) if speed_to_lead else 0
    str_avg = format_minutes(speed_to_reply.get("avg_minutes") if speed_to_reply else None)
    str_count = speed_to_reply.get("count", 0) if speed_to_reply else 0

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Daily Metrics - {report_date.strftime('%b %d, %Y')}",
                "emoji": True
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "*Outreach*\n"
                        f"Profiles: {outreach.get('profiles_scraped', 0)}\n"
                        f"ICP Qualified: {outreach.get('icp_qualified', 0)}\n"
                        f"Uploaded: {outreach.get('heyreach_uploaded', 0)}"
                    )
                },
                {
                    "type": "mrkdwn",
                    "text": (
                        "*Conversations*\n"
                        f"New: {conversations.get('new', 0)}\n"
                        f"Approved: {conversations.get('drafts_approved', 0)}\n"
                        f"Positive: {classifications.get('positive', 0)}"
                    )
                }
            ]
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "*Funnel*\n"
                        f"Pitched: {funnel.get('pitched', 0)}\n"
                        f"Calendar Sent: {funnel.get('calendar_sent', 0)}\n"
                        f"Booked: {funnel.get('booked', 0)}"
                    )
                },
                {
                    "type": "mrkdwn",
                    "text": (
                        "*Content*\n"
                        f"Created: {content.get('drafts_created', 0)}\n"
                        f"Scheduled: {content.get('drafts_scheduled', 0)}\n"
                        f"Posted: {content.get('drafts_posted', 0)}"
                    )
                }
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Response Speed*\n"
                    f"Speed to Lead: {stl_avg} avg ({stl_count} replies)\n"
                    f"Our Response: {str_avg} avg ({str_count} sent)"
                )
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Cost: ${total_cost:.2f}"
                }
            ]
        }
    ]

    return blocks


def build_weekly_report_blocks(
    start_date: date,
    end_date: date,
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for weekly metrics report.

    Args:
        start_date: Start of the week.
        end_date: End of the week.
        metrics: Metrics dict from get_weekly_dashboard_metrics.

    Returns:
        List of Slack Block Kit blocks.
    """
    outreach = metrics.get("outreach", {})
    conversations = metrics.get("conversations", {})
    funnel = metrics.get("funnel", {})
    content = metrics.get("content", {})
    costs = outreach.get("costs", {})
    classifications = conversations.get("classifications", {})
    speed_metrics = metrics.get("speed_metrics", {})

    # Calculate conversion rates
    positive = classifications.get("positive", 0)
    pitched = funnel.get("pitched", 0)
    calendar_sent = funnel.get("calendar_sent", 0)
    booked = funnel.get("booked", 0)

    positive_to_pitched = f"{(pitched / positive * 100):.0f}%" if positive > 0 else "N/A"
    pitched_to_calendar = f"{(calendar_sent / pitched * 100):.0f}%" if pitched > 0 else "N/A"
    calendar_to_booked = f"{(booked / calendar_sent * 100):.0f}%" if calendar_sent > 0 else "N/A"

    # Extract speed metrics
    speed_to_lead = speed_metrics.get("speed_to_lead")
    speed_to_reply = speed_metrics.get("speed_to_reply")
    stl_avg = format_minutes(speed_to_lead.get("avg_minutes") if speed_to_lead else None)
    stl_count = speed_to_lead.get("count", 0) if speed_to_lead else 0
    str_avg = format_minutes(speed_to_reply.get("avg_minutes") if speed_to_reply else None)
    str_count = speed_to_reply.get("count", 0) if speed_to_reply else 0

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Weekly Summary - Week of {start_date.strftime('%b %d, %Y')}",
                "emoji": True
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Outreach Pipeline*\n"
                    f"* Profiles Scraped: {outreach.get('profiles_scraped', 0):,}\n"
                    f"* ICP Qualified: {outreach.get('icp_qualified', 0):,} ({outreach.get('icp_rate', 0)}%)\n"
                    f"* Uploaded to HeyReach: {outreach.get('heyreach_uploaded', 0):,}"
                )
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Conversations*\n"
                    f"* New: {conversations.get('new', 0)}\n"
                    f"* Positive Reply Rate: {conversations.get('positive_reply_rate', 0)}%"
                )
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Funnel Performance*\n"
                    f"* Positive -> Pitched: {positive_to_pitched}\n"
                    f"* Pitched -> Calendar: {pitched_to_calendar}\n"
                    f"* Calendar -> Booked: {calendar_to_booked}\n"
                    f"* Total Booked: {booked}"
                )
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Response Speed*\n"
                    f"* Speed to Lead: {stl_avg} avg ({stl_count} replies)\n"
                    f"* Our Response: {str_avg} avg ({str_count} sent)"
                )
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Content*\n"
                    f"* Created: {content.get('drafts_created', 0)} | "
                    f"Scheduled: {content.get('drafts_scheduled', 0)} | "
                    f"Posted: {content.get('drafts_posted', 0)}"
                )
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Weekly Cost: ${costs.get('total', 0):.2f}"
                }
            ]
        }
    ]

    return blocks


# Category display labels
CATEGORY_DISPLAY = {
    WatchedProfileCategory.PROSPECT: "Prospect",
    WatchedProfileCategory.INFLUENCER: "Influencer",
    WatchedProfileCategory.ICP_PEER: "ICP Peer",
    WatchedProfileCategory.COMPETITOR: "Competitor",
}


def build_engagement_message(
    author_name: str,
    author_headline: str | None,
    author_category: WatchedProfileCategory,
    post_url: str,
    post_summary: str,
    draft_comment: str,
) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for engagement notification.

    Args:
        author_name: Name of the post author.
        author_headline: Author's LinkedIn headline.
        author_category: Category of the watched profile.
        post_url: URL to the LinkedIn post.
        post_summary: AI-generated summary of the post.
        draft_comment: AI-generated draft comment.

    Returns:
        List of Slack Block Kit blocks.
    """
    category_label = CATEGORY_DISPLAY.get(author_category, author_category.value)

    # Author info line
    author_info = f"*{author_name}*"
    if author_headline:
        author_info += f"\n_{author_headline}_"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "LinkedIn Engagement Opportunity",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*Category:* {category_label}"}
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": author_info},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open Post", "emoji": True},
                "url": post_url,
                "action_id": "engagement_open_post",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Summary:*\n{post_summary}",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Draft Comment:*\n```{draft_comment}```",
            },
        },
        {"type": "divider"},
    ]

    return blocks


def build_engagement_buttons(post_id: uuid.UUID) -> list[dict[str, Any]]:
    """Build action buttons for engagement post.

    Args:
        post_id: The engagement post ID for action values.

    Returns:
        List of Slack Block Kit action blocks.
    """
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Done", "emoji": True},
                    "style": "primary",
                    "action_id": "engagement_done",
                    "value": str(post_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit", "emoji": True},
                    "action_id": "engagement_edit",
                    "value": str(post_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Skip", "emoji": True},
                    "style": "danger",
                    "action_id": "engagement_skip",
                    "value": str(post_id),
                },
            ],
        }
    ]


class SlackBot:
    """Client for sending Slack notifications."""

    def __init__(
        self,
        bot_token: str | None = None,
        channel_id: str | None = None,
        metrics_channel_id: str | None = None,
        engagement_channel_id: str | None = None,
    ):
        """Initialize the Slack bot.

        Args:
            bot_token: Slack bot token. Defaults to settings value.
            channel_id: Channel ID for draft notifications. Defaults to settings value.
            metrics_channel_id: Channel ID for metrics reports. Defaults to settings value.
            engagement_channel_id: Channel ID for engagement notifications. Defaults to settings value.
        """
        self._bot_token = bot_token or settings.slack_bot_token
        self._channel_id = channel_id or settings.slack_channel_id
        self._metrics_channel_id = (
            metrics_channel_id
            or settings.slack_metrics_channel_id
            or self._channel_id
        )
        self._engagement_channel_id = (
            engagement_channel_id
            or settings.slack_engagement_channel_id
            or self._channel_id
        )
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
        funnel_stage: FunnelStage | None = None,
        stage_reasoning: str | None = None,
        is_first_reply: bool = False,
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
            funnel_stage: The detected funnel stage (optional).
            stage_reasoning: AI reasoning for stage detection (optional).
            is_first_reply: Whether this is the lead's first reply.

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
                funnel_stage=funnel_stage,
                stage_reasoning=stage_reasoning,
            )
            # Add classification buttons (above action buttons)
            blocks.extend(build_classification_buttons(draft_id, is_first_reply))
            # Add action buttons (Send, Edit, etc.)
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

    async def send_follow_up_config_message(
        self,
        conversation_id: uuid.UUID,
        lead_name: str,
    ) -> str:
        """Send a message with button to configure follow-ups.

        Args:
            conversation_id: The conversation ID for the follow-up.
            lead_name: Name of the lead.

        Returns:
            The message timestamp.

        Raises:
            SlackError: If sending fails.
        """
        try:
            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                text=f"Configure follow-ups for {lead_name}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Configure Follow-up Messages for {lead_name}*\n"
                                    "Click below to set up the follow-up sequence."
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Configure Follow-ups", "emoji": True},
                                "style": "primary",
                                "action_id": "configure_followups",
                                "value": str(conversation_id),
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Skip", "emoji": True},
                                "action_id": "skip_followups",
                                "value": str(conversation_id),
                            }
                        ]
                    }
                ],
            )
            return response["ts"]
        except SlackApiError as e:
            raise SlackError(f"Failed to send follow-up config message: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send follow-up config message: {e}") from e

    async def open_follow_up_modal(
        self,
        trigger_id: str,
        conversation_id: uuid.UUID,
        personalized_message: str | None,
        suggested_follow_up1: str = "",
    ) -> None:
        """Open modal for configuring follow-up messages.

        Args:
            trigger_id: Slack trigger ID from the interaction.
            conversation_id: The conversation to configure follow-ups for.
            personalized_message: The original personalized message to display.
            suggested_follow_up1: Pre-filled suggestion for FOLLOW_UP1.

        Raises:
            SlackError: If opening modal fails.
        """
        blocks = []

        # Display personalized_message if available
        if personalized_message:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Original Personalized Message:*\n_{personalized_message}_"
                }
            })
            blocks.append({"type": "divider"})

        # Input for FOLLOW_UP1
        blocks.append({
            "type": "input",
            "block_id": "follow_up1_input",
            "element": {
                "type": "plain_text_input",
                "action_id": "follow_up1_text",
                "multiline": True,
                "initial_value": suggested_follow_up1,
                "placeholder": {
                    "type": "plain_text",
                    "text": "Enter the first follow-up message..."
                }
            },
            "label": {"type": "plain_text", "text": "FOLLOW_UP1 Message"}
        })

        try:
            await self._client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "callback_id": "configure_followups_submit",
                    "title": {"type": "plain_text", "text": "Follow-up Config"},
                    "submit": {"type": "plain_text", "text": "Save & Add to List"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": blocks,
                    "private_metadata": str(conversation_id),
                }
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to open follow-up modal: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to open follow-up modal: {e}") from e

    async def open_not_icp_modal(
        self,
        trigger_id: str,
        draft_id: uuid.UUID,
        lead_name: str,
        lead_title: str | None = None,
        lead_company: str | None = None,
    ) -> None:
        """Open a modal for Not ICP classification with optional notes.

        Args:
            trigger_id: Slack trigger ID from the interaction.
            draft_id: The draft being classified.
            lead_name: Name of the lead.
            lead_title: Lead's job title (optional).
            lead_company: Lead's company (optional).

        Raises:
            SlackError: If opening modal fails.
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
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Lead:* {lead_info}\n\nWhy doesn't this prospect match your ICP?"
                }
            },
            {
                "type": "input",
                "block_id": "not_icp_notes_input",
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "not_icp_notes_text",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Optional: Add notes for ICP improvement..."
                    }
                },
                "label": {"type": "plain_text", "text": "Notes (optional)"}
            }
        ]

        try:
            await self._client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "callback_id": "not_icp_submit",
                    "title": {"type": "plain_text", "text": "Not ICP"},
                    "submit": {"type": "plain_text", "text": "Confirm"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": blocks,
                    "private_metadata": str(draft_id),
                }
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to open Not ICP modal: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to open Not ICP modal: {e}") from e

    async def send_daily_report(
        self,
        report_date: date,
        metrics: dict[str, Any],
    ) -> str:
        """Send daily metrics report to Slack.

        Args:
            report_date: The date of the report.
            metrics: Metrics dict from get_daily_dashboard_metrics.

        Returns:
            The Slack message timestamp.

        Raises:
            SlackError: If sending fails.
        """
        try:
            blocks = build_daily_report_blocks(report_date, metrics)
            response = await self._client.chat_postMessage(
                channel=self._metrics_channel_id,
                blocks=blocks,
                text=f"Daily Metrics - {report_date.strftime('%b %d, %Y')}",
            )
            return response["ts"]
        except SlackApiError as e:
            raise SlackError(f"Failed to send daily report: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send daily report: {e}") from e

    async def send_weekly_report(
        self,
        start_date: date,
        end_date: date,
        metrics: dict[str, Any],
    ) -> str:
        """Send weekly metrics report to Slack.

        Args:
            start_date: Start of the week.
            end_date: End of the week.
            metrics: Metrics dict from get_weekly_dashboard_metrics.

        Returns:
            The Slack message timestamp.

        Raises:
            SlackError: If sending fails.
        """
        try:
            blocks = build_weekly_report_blocks(start_date, end_date, metrics)
            response = await self._client.chat_postMessage(
                channel=self._metrics_channel_id,
                blocks=blocks,
                text=f"Weekly Summary - Week of {start_date.strftime('%b %d, %Y')}",
            )
            return response["ts"]
        except SlackApiError as e:
            raise SlackError(f"Failed to send weekly report: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send weekly report: {e}") from e

    async def send_engagement_notification(
        self,
        post_id: uuid.UUID,
        author_name: str,
        author_headline: str | None,
        author_category: "WatchedProfileCategory",
        post_url: str,
        post_summary: str,
        draft_comment: str,
    ) -> str:
        """Send an engagement notification to the engagement Slack channel.

        Args:
            post_id: The engagement post ID for button actions.
            author_name: Name of the post author.
            author_headline: Author's LinkedIn headline.
            author_category: Category of the watched profile.
            post_url: URL to the LinkedIn post.
            post_summary: AI-generated summary.
            draft_comment: AI-generated draft comment.

        Returns:
            The Slack message timestamp (ts).

        Raises:
            SlackError: If sending fails.
        """
        try:
            blocks = build_engagement_message(
                author_name=author_name,
                author_headline=author_headline,
                author_category=author_category,
                post_url=post_url,
                post_summary=post_summary,
                draft_comment=draft_comment,
            )
            blocks.extend(build_engagement_buttons(post_id))

            response = await self._client.chat_postMessage(
                channel=self._engagement_channel_id,
                blocks=blocks,
                text=f"Engagement opportunity: {author_name} posted on LinkedIn",
            )

            return response["ts"]

        except SlackApiError as e:
            raise SlackError(
                f"Failed to send engagement notification: {e.response['error']}"
            ) from e
        except Exception as e:
            raise SlackError(
                f"Failed to send engagement notification: {e}"
            ) from e

    async def update_engagement_message(
        self,
        message_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update an engagement message (e.g., after Done/Skip).

        Args:
            message_ts: The message timestamp to update.
            text: New message text.
            blocks: Optional new blocks.

        Raises:
            SlackError: If update fails.
        """
        try:
            await self._client.chat_update(
                channel=self._engagement_channel_id,
                ts=message_ts,
                text=text,
                blocks=blocks,
            )
        except SlackApiError as e:
            raise SlackError(
                f"Failed to update engagement message: {e.response['error']}"
            ) from e
        except Exception as e:
            raise SlackError(
                f"Failed to update engagement message: {e}"
            ) from e

    async def open_engagement_edit_modal(
        self,
        trigger_id: str,
        post_id: uuid.UUID,
        current_comment: str,
    ) -> None:
        """Open a modal for editing the engagement comment.

        Args:
            trigger_id: Slack trigger ID from the interaction.
            post_id: The engagement post being edited.
            current_comment: Current draft comment to pre-fill.

        Raises:
            SlackError: If opening modal fails.
        """
        try:
            await self._client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "callback_id": "engagement_edit_submit",
                    "title": {"type": "plain_text", "text": "Edit Comment"},
                    "submit": {"type": "plain_text", "text": "Save"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "comment_input",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "comment_text",
                                "multiline": True,
                                "initial_value": current_comment,
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "Your Comment",
                            },
                        }
                    ],
                    "private_metadata": str(post_id),
                },
            )
        except SlackApiError as e:
            raise SlackError(
                f"Failed to open engagement edit modal: {e.response['error']}"
            ) from e
        except Exception as e:
            raise SlackError(
                f"Failed to open engagement edit modal: {e}"
            ) from e


# Global bot instance
_bot: SlackBot | None = None


def get_slack_bot() -> SlackBot:
    """Get or create the Slack bot singleton."""
    global _bot
    if _bot is None:
        _bot = SlackBot()
    return _bot
