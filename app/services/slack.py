"""Slack service for sending draft notifications and reports."""

import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from app.config import settings
from app.models import FunnelStage, WatchedProfileCategory
from app.services.reports import format_minutes

logger = logging.getLogger(__name__)

# Default path to strategy file (module-level so tests can patch it)
_STRATEGY_FILE_PATH = str(Path(__file__).resolve().parents[2] / ".claude" / "strategy.md")


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
    triggering_message: str | None = None,
    judge_score: float | None = None,
    revision_count: int = 0,
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
        triggering_message: The outbound message that triggered this reply (optional).

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

    blocks.append({
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
    })

    # Show the outbound message that triggered this reply (if available)
    if triggering_message:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Our Message:*\n{triggering_message}"
            }
        })

    blocks.extend([
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
    ])

    # Add judge quality score if available
    if judge_score is not None:
        if judge_score >= 4.0:
            dot = ":large_green_circle:"
        elif judge_score >= 3.5:
            dot = ":large_yellow_circle:"
        else:
            dot = ":red_circle:"
        score_text = f"{dot} *Quality: {judge_score:.1f}/5*"
        if revision_count > 0:
            score_text += f"  (revised {revision_count}x)"
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": score_text}]
        })

    blocks.extend([
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
    prospect_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Build classification buttons for metrics tracking.

    Args:
        draft_id: The draft ID to include in action values.
        is_first_reply: Whether this is the first reply from the lead.
            If True, includes the "Positive Reply" button.
        prospect_id: If provided, Gift Leads button uses confirm_icp_gift_leads
            with prospect_id. If None, falls back to old gift_leads with draft_id.

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

    # Funnel stage buttons - always show
    elements.extend([
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "\U0001f3af Pitched", "emoji": True},
            "action_id": "funnel_pitched",
            "value": str(draft_id),
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "\U0001f4c5 Calendar Shown", "emoji": True},
            "action_id": "funnel_calendar_sent",
            "value": str(draft_id),
        },
    ])

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

    # Gift Leads button (always show)
    # When prospect_id is available, use the streamlined confirm_icp flow
    # Otherwise fall back to the legacy gift_leads flow (uses draft_id)
    if prospect_id is not None:
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "\U0001f381 Gift Leads", "emoji": True},
            "action_id": "confirm_icp_gift_leads",
            "value": str(prospect_id),
        })
    else:
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "\U0001f381 Gift Leads", "emoji": True},
            "action_id": "gift_leads",
            "value": str(draft_id),
        })

    return [
        {
            "type": "actions",
            "elements": elements,
        }
    ]


def build_qa_annotation(
    qa_score: float,
    qa_verdict: str | None = None,
    qa_issues: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Build QA annotation blocks for the Slack message.

    Args:
        qa_score: QA score (1.0-5.0).
        qa_verdict: pass/flag/block.
        qa_issues: List of issue dicts.

    Returns:
        Slack blocks showing QA status.
    """
    # Choose badge based on score
    if qa_score >= 4.0:
        badge = f":large_green_circle: QA Pass ({qa_score:.1f}/5)"
    elif qa_score >= 3.0:
        badge = f":large_yellow_circle: QA Flag ({qa_score:.1f}/5)"
    else:
        badge = f":red_circle: QA Block ({qa_score:.1f}/5)"

    blocks: list[dict[str, Any]] = [
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": badge}],
        },
    ]

    # Add issue details for flagged drafts
    if qa_issues and qa_score < 4.0:
        issue_lines = []
        for issue in qa_issues[:3]:  # Max 3 issues shown
            severity = issue.get("severity", "medium")
            icon = ":warning:" if severity == "high" else ":information_source:"
            issue_lines.append(f"{icon} {issue.get('type', 'issue')}: {issue.get('detail', '')}")
        if issue_lines:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "\n".join(issue_lines)}],
            })

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


def _get_current_focus(path: str | None = None) -> str | None:
    """Read the 'This Week's Focus' section from strategy.md.

    Args:
        path: Override path to the strategy file (for testing).

    Returns:
        The focus section text, or None if unavailable.
    """
    file_path = path or _STRATEGY_FILE_PATH
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None

    # Extract text between "## This Week's Focus" and the next "## " heading (or EOF)
    match = re.search(
        r"## This Week's Focus\n+(.*?)(?=\n## |\Z)",
        text,
        re.DOTALL,
    )
    if not match:
        return None

    focus = match.group(1).strip()
    return focus or None


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
    ]

    # Append "This Week's Focus" if available
    focus_text = _get_current_focus()
    if focus_text:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*:dart: This Week's Focus*\n{focus_text}",
            },
        })

    blocks.extend([
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
    ])

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


def build_trend_scout_report_blocks(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for trend scout report.

    Args:
        result: Summary dict from run_trend_scout_task() with
            batch_id, topics_found, topics_saved, topics.

    Returns:
        List of Slack Block Kit blocks.
    """
    batch_id = result.get("batch_id", "?")
    found = result.get("topics_found", 0)
    saved = result.get("topics_saved", 0)
    topics = result.get("topics", [])

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Trend Scout Report",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Topics found:* {found}"},
                {"type": "mrkdwn", "text": f"*Topics saved:* {saved}"},
            ],
        },
    ]

    # Top topics (up to 5)
    if topics:
        top = sorted(topics, key=lambda t: t.get("relevance_score", 0), reverse=True)[:5]
        lines = []
        for t in top:
            score = t.get("relevance_score", "?")
            platform = t.get("source_platform", "?")
            lines.append(f"[{score}/10] *{t.get('topic', '?')}* ({platform})")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Top Topics:*\n" + "\n".join(lines)},
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"Batch: `{batch_id}`"},
        ],
    })

    return blocks


def build_pitched_card_blocks(
    lead_name: str,
    lead_title: str | None,
    lead_company: str | None,
    linkedin_url: str,
    funnel_stage: FunnelStage,
    recent_messages: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for a pitched channel card.

    Args:
        lead_name: Name of the lead.
        lead_title: Lead's job title (optional).
        lead_company: Lead's company (optional).
        linkedin_url: LinkedIn profile URL.
        funnel_stage: Current funnel stage.
        recent_messages: List of recent inbound messages with 'content' key.

    Returns:
        List of Slack Block Kit blocks.
    """
    # Build header with lead info
    header_text = lead_name
    if lead_title and lead_company:
        header_text = f"{lead_name} ({lead_title} @ {lead_company})"
    elif lead_title:
        header_text = f"{lead_name} ({lead_title})"
    elif lead_company:
        header_text = f"{lead_name} @ {lead_company}"

    # Truncate header if too long for Slack (150 char limit for plain_text headers)
    if len(header_text) > 148:
        header_text = header_text[:145] + "..."

    stage_label, stage_desc = STAGE_DISPLAY.get(
        funnel_stage, (funnel_stage.value, "")
    )

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": header_text,
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*Status:* {stage_label} - {stage_desc}"}
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*LinkedIn:* <{linkedin_url}|View Profile>",
            },
        },
    ]

    # Add recent inbound messages
    if recent_messages:
        msg_lines = []
        for msg in recent_messages[:3]:
            content = msg.get("content", "")
            if len(content) > 200:
                content = content[:197] + "..."
            msg_lines.append(f"> _{content}_")
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Recent Messages:*\n" + "\n".join(msg_lines),
            },
        })

    blocks.append({"type": "divider"})

    return blocks


def build_pitched_card_buttons(prospect_id: uuid.UUID) -> list[dict[str, Any]]:
    """Build action buttons for pitched channel card.

    Args:
        prospect_id: The prospect ID for action values.

    Returns:
        List of Slack Block Kit action blocks.
    """
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Send Message", "emoji": True},
                    "style": "primary",
                    "action_id": "pitched_send_message",
                    "value": str(prospect_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Calendar Sent", "emoji": True},
                    "action_id": "pitched_calendar_sent",
                    "value": str(prospect_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Booked", "emoji": True},
                    "action_id": "pitched_booked",
                    "value": str(prospect_id),
                },
            ],
        }
    ]


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


def build_health_check_alert_blocks(
    report: "HealthCheckReport",
) -> list[dict[str, Any]]:
    """Build Slack Block Kit message for health check alert.

    Only called when there are failures. Lists failing checks with emoji.

    Args:
        report: The health check report with results.

    Returns:
        List of Slack Block Kit blocks.
    """
    from app.services.health_check import CheckStatus

    status_emoji = {
        CheckStatus.WARNING: ":warning:",
        CheckStatus.CRITICAL: ":rotating_light:",
    }

    header_text = f"System Health Check - {report.status.value.upper()}"

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": header_text,
                "emoji": True,
            },
        },
        {"type": "divider"},
    ]

    for check in report.failing:
        emoji = status_emoji.get(check.status, ":question:")
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} *{check.name}* [{check.status.value}]\n{check.message}",
            },
        })

    total = len(report.checks)
    passing = report.passing
    ts = report.timestamp.strftime("%Y-%m-%d %H:%M UTC")

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"{passing}/{total} checks passing | {ts}",
            }
        ],
    })

    return blocks


class SlackBot:
    """Client for sending Slack notifications."""

    def __init__(
        self,
        bot_token: str | None = None,
        channel_id: str | None = None,
        metrics_channel_id: str | None = None,
        engagement_channel_id: str | None = None,
        pitched_channel_id: str | None = None,
    ):
        """Initialize the Slack bot.

        Args:
            bot_token: Slack bot token. Defaults to settings value.
            channel_id: Channel ID for draft notifications. Defaults to settings value.
            metrics_channel_id: Channel ID for metrics reports. Defaults to settings value.
            engagement_channel_id: Channel ID for engagement notifications. Defaults to settings value.
            pitched_channel_id: Channel ID for pitched prospect cards. Defaults to settings value.
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
        self._pitched_channel_id = (
            pitched_channel_id
            or settings.slack_pitched_channel_id
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
        triggering_message: str | None = None,
        prospect_id: uuid.UUID | None = None,
        judge_score: float | None = None,
        revision_count: int = 0,
        qa_score: float | None = None,
        qa_verdict: str | None = None,
        qa_issues: list[dict] | None = None,
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
            triggering_message: The outbound message that triggered this reply (optional).
            prospect_id: The prospect ID for Gift Leads button (optional).
            qa_score: QA agent score 1.0-5.0 (optional).
            qa_verdict: QA verdict - pass/flag/block (optional).
            qa_issues: List of QA issue dicts (optional).

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
                triggering_message=triggering_message,
                judge_score=judge_score,
                revision_count=revision_count,
            )

            # Add QA annotation block if QA was run
            if qa_score is not None:
                blocks.extend(build_qa_annotation(qa_score, qa_verdict, qa_issues))

            # Add classification buttons (above action buttons)
            blocks.extend(build_classification_buttons(draft_id, is_first_reply, prospect_id))
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

    async def send_pipeline_progress(self, text: str, thread_ts: str) -> str:
        """Send a progress update as a threaded reply.

        Args:
            text: Progress message text.
            thread_ts: Thread timestamp to reply under.

        Returns:
            The reply message timestamp.
        """
        try:
            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                text=text,
                thread_ts=thread_ts,
            )
            return response["ts"]
        except SlackApiError as e:
            raise SlackError(f"Failed to send pipeline progress: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send pipeline progress: {e}") from e

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

    async def open_gift_leads_modal(
        self,
        trigger_id: str,
        prospect_id: uuid.UUID,
        prospect_name: str,
        prefill_icp: str = "",
    ) -> None:
        """Open a modal for Gift Leads with ICP text input.

        Args:
            trigger_id: Slack trigger ID from the interaction.
            prospect_id: The prospect to find leads for.
            prospect_name: Name for display.
            prefill_icp: Pre-filled ICP description.

        Raises:
            SlackError: If opening modal fails.
        """
        try:
            await self._client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "callback_id": "gift_leads_submit",
                    "title": {"type": "plain_text", "text": "Gift Leads"},
                    "submit": {"type": "plain_text", "text": "Find Leads"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Find leads for:* {prospect_name}\n\nDescribe their ICP and we'll search the prospect pool.",
                            },
                        },
                        {
                            "type": "input",
                            "block_id": "icp_input",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "icp_text",
                                "multiline": True,
                                "initial_value": prefill_icp,
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "e.g., naturopath clinic owners, wellness practitioners",
                                },
                            },
                            "label": {"type": "plain_text", "text": "ICP Description"},
                        },
                        {
                            "type": "input",
                            "block_id": "keywords_input",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "keywords_text",
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "e.g., naturopath, ND, clinic owner, wellness",
                                },
                            },
                            "label": {"type": "plain_text", "text": "Search Keywords (comma-separated)"},
                        },
                    ],
                    "private_metadata": str(prospect_id),
                },
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to open gift leads modal: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to open gift leads modal: {e}") from e

    async def send_gift_leads_results(
        self,
        prospect_name: str,
        leads: list[dict],
        pool_size: int,
        keywords: list[str],
    ) -> str:
        """Post gift leads results to Slack with formatted table and CSV.

        Args:
            prospect_name: Name of the prospect we're gifting leads to.
            leads: List of lead dicts.
            pool_size: Total prospects in the DB pool.
            keywords: Keywords that were searched.

        Returns:
            The Slack message timestamp.

        Raises:
            SlackError: If sending fails.
        """
        try:
            rows = []
            for i, lead in enumerate(leads, 1):
                name = lead.get("full_name") or "Unknown"
                title = lead.get("job_title") or ""
                company = lead.get("company_name") or ""
                score = lead.get("activity_score") or 0
                url = lead.get("linkedin_url") or ""
                rows.append(f"{i}. *{name}* - {title} @ {company} (score: {score}) <{url}|LI>")

            table_text = "\n".join(rows) if rows else "No leads found."

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"Gift Leads for {prospect_name}",
                        "emoji": True,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Keywords: {', '.join(keywords)} | {len(leads)} leads from {pool_size} prospects in DB",
                        }
                    ],
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": table_text[:3000],
                    },
                },
            ]

            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                blocks=blocks,
                text=f"Gift Leads for {prospect_name}: {len(leads)} matches",
            )

            if leads:
                import csv
                import io

                output = io.StringIO()
                writer = csv.DictWriter(
                    output,
                    fieldnames=["full_name", "job_title", "company_name", "location", "headline", "activity_score", "icp_reason", "linkedin_url"],
                    extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(leads)

                await self._client.files_upload_v2(
                    channel=self._channel_id,
                    content=output.getvalue(),
                    filename=f"gift_leads_{prospect_name.replace(' ', '_')}.csv",
                    title=f"Gift Leads CSV - {prospect_name}",
                    thread_ts=response["ts"],
                )

            return response["ts"]

        except SlackApiError as e:
            raise SlackError(f"Failed to send gift leads results: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send gift leads results: {e}") from e

    async def open_confirm_icp_gift_leads_modal(
        self,
        trigger_id: str,
        prospect_id: uuid.UUID,
        prospect_name: str,
        lead_reply: str = "",
        prefill_icp: str = "",
        prefill_keywords: str = "",
    ) -> None:
        """Open confirm ICP modal for the streamlined gift leads flow.

        Args:
            trigger_id: Slack trigger ID from the interaction.
            prospect_id: The prospect to find leads for.
            prospect_name: Name for display.
            lead_reply: The prospect's latest reply (context).
            prefill_icp: Pre-filled ICP description.
            prefill_keywords: Pre-filled search keywords.

        Raises:
            SlackError: If opening modal fails.
        """
        blocks = []

        # Show prospect's reply as read-only context
        if lead_reply:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{prospect_name}'s reply:*\n> _{lead_reply[:500]}_",
                },
            })
            blocks.append({"type": "divider"})

        blocks.extend([
            {
                "type": "input",
                "block_id": "icp_input",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "icp_text",
                    "multiline": True,
                    "initial_value": prefill_icp,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "e.g., naturopath clinic owners, wellness practitioners",
                    },
                },
                "label": {"type": "plain_text", "text": "ICP Description"},
            },
            {
                "type": "input",
                "block_id": "keywords_input",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "keywords_text",
                    "initial_value": prefill_keywords,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "e.g., naturopath, ND, clinic owner, wellness",
                    },
                },
                "label": {"type": "plain_text", "text": "Search Keywords (comma-separated)"},
            },
        ])

        try:
            await self._client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "callback_id": "confirm_icp_gift_leads_submit",
                    "title": {"type": "plain_text", "text": "Confirm ICP & Send Leads"},
                    "submit": {"type": "plain_text", "text": "Find Leads"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": blocks,
                    "private_metadata": str(prospect_id),
                },
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to open confirm ICP modal: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to open confirm ICP modal: {e}") from e

    async def send_gift_leads_results_with_send_button(
        self,
        prospect_id: uuid.UUID,
        prospect_name: str,
        leads: list[dict],
        pool_size: int,
        keywords: list[str],
        sheet_url: str | None = None,
    ) -> str:
        """Post gift leads results with a Send Leads DM button.

        Same as send_gift_leads_results but adds a button to compose
        a LinkedIn DM with the leads list.

        Args:
            prospect_id: The prospect to send leads to.
            prospect_name: Name of the prospect.
            leads: List of lead dicts.
            pool_size: Total prospects in the DB pool.
            keywords: Keywords that were searched.
            sheet_url: Google Sheet URL (if created).

        Returns:
            The Slack message timestamp.

        Raises:
            SlackError: If sending fails.
        """
        try:
            rows = []
            for i, lead in enumerate(leads, 1):
                name = lead.get("full_name") or "Unknown"
                title = lead.get("job_title") or ""
                company = lead.get("company_name") or ""
                score = lead.get("activity_score") or 0
                url = lead.get("linkedin_url") or ""
                rows.append(f"{i}. *{name}* - {title} @ {company} (score: {score}) <{url}|LI>")

            table_text = "\n".join(rows) if rows else "No leads found."

            context_text = f"Keywords: {', '.join(keywords)} | {len(leads)} leads from {pool_size} prospects in DB"
            if sheet_url:
                context_text += f" | <{sheet_url}|Google Sheet>"

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"Gift Leads for {prospect_name}",
                        "emoji": True,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": context_text,
                        }
                    ],
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": table_text[:3000],
                    },
                },
            ]

            # Add Send Leads button if there are results
            if leads:
                import json as _json

                # Encode prospect_id and sheet_url in button value
                button_value = _json.dumps({
                    "prospect_id": str(prospect_id),
                    "sheet_url": sheet_url,
                })
                blocks.append({
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": f"Send Leads to {prospect_name}", "emoji": True},
                            "style": "primary",
                            "action_id": "send_gift_leads_dm",
                            "value": button_value,
                        }
                    ],
                })

            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                blocks=blocks,
                text=f"Gift Leads for {prospect_name}: {len(leads)} matches",
            )

            # Upload CSV in thread
            if leads:
                import csv
                import io

                output = io.StringIO()
                writer = csv.DictWriter(
                    output,
                    fieldnames=["full_name", "job_title", "company_name", "location", "headline", "activity_score", "icp_reason", "linkedin_url"],
                    extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(leads)

                await self._client.files_upload_v2(
                    channel=self._channel_id,
                    content=output.getvalue(),
                    filename=f"gift_leads_{prospect_name.replace(' ', '_')}.csv",
                    title=f"Gift Leads CSV - {prospect_name}",
                    thread_ts=response["ts"],
                )

            return response["ts"]

        except SlackApiError as e:
            raise SlackError(f"Failed to send gift leads results: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send gift leads results: {e}") from e

    async def send_gift_leads_ready(
        self,
        prospect_id: uuid.UUID | None,
        prospect_name: str,
        lead_count: int,
        icp: str,
        context: str,
        draft_dm: str,
        sheet_url: str | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> str:
        """Post gift leads ready message with Send/Edit buttons.

        Shows the draft DM and provides buttons to send as-is or edit first.

        Args:
            prospect_id: The prospect to send leads to (or None).
            prospect_name: Name of the prospect.
            lead_count: Number of leads found.
            icp: ICP description.
            context: Last message context from the prospect.
            draft_dm: Pre-composed DM text.
            sheet_url: Google Sheet URL (if created).
            conversation_id: Fallback conversation ID when no prospect record.

        Returns:
            The Slack message timestamp.
        """
        try:
            import json as _json

            sheet_text = f"\n*Sheet:* <{sheet_url}|Open Google Sheet>" if sheet_url else ""

            button_value = _json.dumps({
                "prospect_id": str(prospect_id) if prospect_id else "",
                "conversation_id": str(conversation_id) if conversation_id else "",
                "sheet_url": sheet_url,
                "draft_dm": draft_dm,
            })

            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"Gift Leads Ready: {prospect_name}", "emoji": True},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*ICP:* {icp}\n"
                            f"*Leads:* {lead_count}"
                            f"{sheet_text}\n"
                            f"*Context:* {context}"
                        ),
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Draft DM:*\n>>>{draft_dm}",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Send as is", "emoji": True},
                            "style": "primary",
                            "action_id": "send_gift_leads_as_is",
                            "value": button_value,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Edit & Send", "emoji": True},
                            "action_id": "edit_gift_leads_dm",
                            "value": button_value,
                        },
                    ],
                },
            ]

            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                blocks=blocks,
                text=f"Gift Leads Ready: {prospect_name} ({lead_count} leads)",
            )
            return response["ts"]

        except SlackApiError as e:
            raise SlackError(f"Failed to send gift leads ready: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send gift leads ready: {e}") from e

    async def send_gift_leads_auto_sent_notification(
        self,
        prospect_name: str,
        lead_count: int,
        sheet_url: str | None,
        keywords: list[str],
    ) -> str:
        """Post notification that gift leads were auto-sent to a prospect.

        Used by the auto-trigger flow for buying signal prospects.

        Args:
            prospect_name: Name of the prospect.
            lead_count: Number of leads sent.
            sheet_url: Google Sheet URL (if created).
            keywords: Keywords that were searched.

        Returns:
            The Slack message timestamp.
        """
        try:
            sheet_text = f" | <{sheet_url}|View Sheet>" if sheet_url else ""
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Auto-sent gift leads to {prospect_name}*\n"
                            f"{lead_count} leads (keywords: {', '.join(keywords)}){sheet_text}"
                        ),
                    },
                },
            ]

            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                blocks=blocks,
                text=f"Auto-sent {lead_count} gift leads to {prospect_name}",
            )
            return response["ts"]

        except SlackApiError as e:
            raise SlackError(f"Failed to send auto-sent notification: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send auto-sent notification: {e}") from e

    async def open_send_gift_leads_dm_modal(
        self,
        trigger_id: str,
        prospect_id: uuid.UUID | None,
        prospect_name: str,
        draft_dm: str,
        conversation_id: uuid.UUID | None = None,
    ) -> None:
        """Open modal with pre-formatted LinkedIn DM containing leads list.

        Args:
            trigger_id: Slack trigger ID from the interaction.
            prospect_id: The prospect to send to (or None if using conversation_id).
            prospect_name: Name for display.
            draft_dm: Pre-formatted DM text (editable).
            conversation_id: Fallback conversation ID when no prospect record exists.

        Raises:
            SlackError: If opening modal fails.
        """
        try:
            await self._client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "callback_id": "send_gift_leads_dm_submit",
                    "title": {"type": "plain_text", "text": "Send Leads DM"},
                    "submit": {"type": "plain_text", "text": "Send via LinkedIn"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*To:* {prospect_name}",
                            },
                        },
                        {
                            "type": "input",
                            "block_id": "dm_input",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "dm_text",
                                "multiline": True,
                                "initial_value": draft_dm,
                            },
                            "label": {"type": "plain_text", "text": "LinkedIn Message"},
                        },
                    ],
                    "private_metadata": json.dumps({
                        "prospect_id": str(prospect_id) if prospect_id else "",
                        "conversation_id": str(conversation_id) if conversation_id else "",
                    }),
                },
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to open send DM modal: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to open send DM modal: {e}") from e

    async def send_pitched_card(
        self,
        prospect_id: uuid.UUID,
        lead_name: str,
        lead_title: str | None,
        lead_company: str | None,
        linkedin_url: str,
        funnel_stage: FunnelStage,
        recent_messages: list[dict[str, str]] | None = None,
    ) -> str:
        """Post a pitched prospect card to the pitched channel.

        Args:
            prospect_id: The prospect ID for button actions.
            lead_name: Name of the lead.
            lead_title: Lead's job title.
            lead_company: Lead's company.
            linkedin_url: LinkedIn profile URL.
            funnel_stage: Current funnel stage.
            recent_messages: Recent inbound messages.

        Returns:
            The Slack message timestamp (ts).

        Raises:
            SlackError: If sending fails.
        """
        try:
            blocks = build_pitched_card_blocks(
                lead_name=lead_name,
                lead_title=lead_title,
                lead_company=lead_company,
                linkedin_url=linkedin_url,
                funnel_stage=funnel_stage,
                recent_messages=recent_messages,
            )
            blocks.extend(build_pitched_card_buttons(prospect_id))

            response = await self._client.chat_postMessage(
                channel=self._pitched_channel_id,
                blocks=blocks,
                text=f"Pitched: {lead_name}",
            )

            return response["ts"]

        except SlackApiError as e:
            raise SlackError(f"Failed to send pitched card: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send pitched card: {e}") from e

    async def update_pitched_card(
        self,
        message_ts: str,
        prospect_id: uuid.UUID,
        lead_name: str,
        lead_title: str | None,
        lead_company: str | None,
        linkedin_url: str,
        funnel_stage: FunnelStage,
        recent_messages: list[dict[str, str]] | None = None,
    ) -> None:
        """Update an existing pitched card in the pitched channel.

        If stage is BOOKED, replaces buttons with confirmation context.

        Args:
            message_ts: The message timestamp to update.
            prospect_id: The prospect ID for button actions.
            lead_name: Name of the lead.
            lead_title: Lead's job title.
            lead_company: Lead's company.
            linkedin_url: LinkedIn profile URL.
            funnel_stage: Current funnel stage.
            recent_messages: Recent inbound messages.

        Raises:
            SlackError: If update fails.
        """
        try:
            blocks = build_pitched_card_blocks(
                lead_name=lead_name,
                lead_title=lead_title,
                lead_company=lead_company,
                linkedin_url=linkedin_url,
                funnel_stage=funnel_stage,
                recent_messages=recent_messages,
            )

            if funnel_stage == FunnelStage.BOOKED:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "Meeting booked!"}
                    ],
                })
            else:
                blocks.extend(build_pitched_card_buttons(prospect_id))

            await self._client.chat_update(
                channel=self._pitched_channel_id,
                ts=message_ts,
                text=f"Pitched: {lead_name}",
                blocks=blocks,
            )

        except SlackApiError as e:
            raise SlackError(f"Failed to update pitched card: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to update pitched card: {e}") from e

    async def open_pitched_send_message_modal(
        self,
        trigger_id: str,
        prospect_id: uuid.UUID,
        lead_name: str,
    ) -> None:
        """Open a modal for sending a message from the pitched channel.

        Args:
            trigger_id: Slack trigger ID from the interaction.
            prospect_id: The prospect to send to.
            lead_name: Name of the lead (for display).

        Raises:
            SlackError: If opening modal fails.
        """
        try:
            await self._client.views_open(
                trigger_id=trigger_id,
                view={
                    "type": "modal",
                    "callback_id": "pitched_send_message_submit",
                    "title": {"type": "plain_text", "text": "Send Message"},
                    "submit": {"type": "plain_text", "text": "Send"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*To:* {lead_name}",
                            },
                        },
                        {
                            "type": "input",
                            "block_id": "message_input",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "message_text",
                                "multiline": True,
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "Type your message...",
                                },
                            },
                            "label": {"type": "plain_text", "text": "Message"},
                        },
                        {
                            "type": "input",
                            "block_id": "schedule_input",
                            "optional": True,
                            "element": {
                                "type": "datetimepicker",
                                "action_id": "schedule_time",
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "Schedule for later (optional)",
                            },
                        },
                    ],
                    "private_metadata": str(prospect_id),
                },
            )
        except SlackApiError as e:
            raise SlackError(f"Failed to open pitched send modal: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to open pitched send modal: {e}") from e

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

    async def send_trend_scout_report(
        self,
        result: dict[str, Any],
    ) -> str:
        """Send trend scout report to Slack metrics channel.

        Args:
            result: Summary dict from run_trend_scout_task().

        Returns:
            The Slack message timestamp.

        Raises:
            SlackError: If sending fails.
        """
        try:
            blocks = build_trend_scout_report_blocks(result)
            response = await self._client.chat_postMessage(
                channel=self._metrics_channel_id,
                blocks=blocks,
                text=f"Trend Scout: {result.get('topics_saved', 0)} topics saved",
            )
            return response["ts"]
        except SlackApiError as e:
            raise SlackError(f"Failed to send trend scout report: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send trend scout report: {e}") from e

    async def send_health_check_alert(
        self,
        report: "HealthCheckReport",
    ) -> str:
        """Send health check alert to metrics channel.

        Only sends when report has WARNING or CRITICAL checks.

        Args:
            report: The health check report.

        Returns:
            The Slack message timestamp.

        Raises:
            SlackError: If sending fails.
        """
        try:
            blocks = build_health_check_alert_blocks(report)
            response = await self._client.chat_postMessage(
                channel=self._metrics_channel_id,
                blocks=blocks,
                text=f"System Health Check - {report.status.value.upper()}",
            )
            return response["ts"]
        except SlackApiError as e:
            raise SlackError(f"Failed to send health check alert: {e.response['error']}") from e
        except Exception as e:
            raise SlackError(f"Failed to send health check alert: {e}") from e

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
