"""Slack interactions router."""

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session_factory
from app.models import (
    Conversation,
    Draft,
    DraftStatus,
    EngagementPost,
    EngagementPostStatus,
    FunnelStage,
    ICPFeedback,
    MessageDirection,
    MessageLog,
    Prospect,
    ReplyClassification,
)
from app.services.deepseek import generate_reply_draft
from app.services.heyreach import get_heyreach_client, HeyReachError

# HeyReach list ID for follow-up sequences
HEYREACH_FOLLOW_UP_LIST_ID = 511495
from app.services.slack import (
    SlackBot,
    SlackError,
    build_action_buttons,
    build_draft_message,
    get_slack_bot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])

# Default FOLLOW_UP2 message
DEFAULT_FOLLOW_UP2 = "Would it even make sense for you to get clients on here? LI is not always a good fit"


def count_google_docs_links(conversation_history: list[dict] | None) -> int:
    """Count Google Docs links in conversation history.

    Args:
        conversation_history: List of message dicts with 'content' field.

    Returns:
        Number of Google Docs links found.
    """
    if not conversation_history:
        return 0

    import re
    docs_pattern = r'https?://docs\.google\.com/[^\s<>\"\']+'
    count = 0

    for message in conversation_history:
        content = message.get("content", "")
        matches = re.findall(docs_pattern, content)
        count += len(matches)

    return count


async def get_prospect_personalized_message(
    session,
    linkedin_url: str,
) -> str | None:
    """Get the personalized_message for a prospect by LinkedIn URL.

    Args:
        session: Database session.
        linkedin_url: The prospect's LinkedIn profile URL.

    Returns:
        The personalized_message or None if not found.
    """
    result = await session.execute(
        select(Prospect.personalized_message).where(
            Prospect.linkedin_url == linkedin_url
        )
    )
    row = result.scalar_one_or_none()
    return row


async def update_prospect_followup_tracking(
    session,
    linkedin_url: str,
    list_id: int,
) -> None:
    """Update prospect with follow-up list tracking info.

    Args:
        session: Database session.
        linkedin_url: The prospect's LinkedIn profile URL.
        list_id: The HeyReach follow-up list ID.
    """
    from datetime import datetime, timezone

    # Normalize the URL
    normalized_url = linkedin_url.lower().strip().rstrip("/")
    if "?" in normalized_url:
        normalized_url = normalized_url.split("?")[0]

    result = await session.execute(
        select(Prospect).where(Prospect.linkedin_url == normalized_url)
    )
    prospect = result.scalar_one_or_none()

    if prospect:
        prospect.followup_list_id = list_id
        prospect.added_to_followup_at = datetime.now(timezone.utc)
        logger.info(
            f"Updated prospect {prospect.id} with follow-up tracking: "
            f"list_id={list_id}, added_at={prospect.added_to_followup_at}"
        )
    else:
        logger.warning(
            f"Prospect not found for follow-up tracking: {normalized_url}"
        )


async def add_prospect_to_follow_up_list(
    conversation: Conversation,
    follow_up_messages: dict[str, str] | None = None,
) -> None:
    """Add a prospect to the HeyReach follow-up list after sending a message.

    Args:
        conversation: The conversation with prospect info.
        follow_up_messages: Dict with FOLLOW_UP1, FOLLOW_UP2, FOLLOW_UP3 values.
            If None, custom fields will be empty.
    """
    try:
        heyreach = get_heyreach_client()

        # Parse name into first/last if possible
        name_parts = conversation.lead_name.split(" ", 1)
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        # Build custom fields - values to be determined
        custom_fields = {}
        if follow_up_messages:
            if follow_up_messages.get("FOLLOW_UP1"):
                custom_fields["FOLLOW_UP1"] = follow_up_messages["FOLLOW_UP1"]
            if follow_up_messages.get("FOLLOW_UP2"):
                custom_fields["FOLLOW_UP2"] = follow_up_messages["FOLLOW_UP2"]
            if follow_up_messages.get("FOLLOW_UP3"):
                custom_fields["FOLLOW_UP3"] = follow_up_messages["FOLLOW_UP3"]

        lead_data = {
            "linkedin_url": conversation.linkedin_profile_url,
            "first_name": first_name,
            "last_name": last_name,
            "custom_fields": custom_fields,
        }

        logger.info(
            f"Adding to HeyReach list {HEYREACH_FOLLOW_UP_LIST_ID}: "
            f"linkedin_url={conversation.linkedin_profile_url}, "
            f"name={first_name} {last_name}, "
            f"custom_fields={list(custom_fields.keys())}"
        )

        result = await heyreach.add_leads_to_list(
            list_id=HEYREACH_FOLLOW_UP_LIST_ID,
            leads=[lead_data],
        )
        logger.info(
            f"HeyReach response for {conversation.lead_name}: {result}"
        )

    except HeyReachError as e:
        # Log but don't fail the main flow
        logger.error(
            f"Failed to add prospect to follow-up list: {e}. "
            f"Conversation: {conversation.id}"
        )
    except Exception as e:
        logger.error(
            f"Unexpected error adding prospect to follow-up list: {e}. "
            f"Conversation: {conversation.id}",
            exc_info=True,
        )


async def verify_slack_signature(request: Request) -> bytes:
    """Verify Slack request signature.

    Slack signs requests using HMAC-SHA256 with the signing secret.
    The signature is in X-Slack-Signature header, timestamp in X-Slack-Request-Timestamp.

    Args:
        request: The incoming request.

    Returns:
        The raw request body.

    Raises:
        HTTPException: If signature is invalid or missing.
    """
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing signature headers")

    # Reject old timestamps (prevent replay attacks)
    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            raise HTTPException(status_code=401, detail="Request too old")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp")

    body = await request.body()

    # Compute expected signature
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected_sig = "v0=" + hmac.new(
        settings.slack_signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    return body


def parse_slack_payload(body: bytes) -> dict[str, Any]:
    """Parse Slack's form-encoded payload.

    Slack sends interactions as application/x-www-form-urlencoded
    with a 'payload' field containing JSON.

    Args:
        body: Raw request body.

    Returns:
        Parsed payload dict.
    """
    parsed = parse_qs(body.decode("utf-8"))
    payload_str = parsed.get("payload", ["{}"])[0]
    return json.loads(payload_str)


async def handle_approve(
    draft_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle approve action - send message via HeyReach.

    Args:
        draft_id: The draft to approve.
        message_ts: Slack message timestamp for updates.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(_process_approve, draft_id, message_ts)


async def _process_approve(draft_id: uuid.UUID, message_ts: str) -> None:
    """Background task to process approval."""
    try:
        async with async_session_factory() as session:
            # Fetch draft with conversation
            result = await session.execute(
                select(Draft)
                .options(selectinload(Draft.conversation))
                .where(Draft.id == draft_id)
            )
            draft = result.scalar_one_or_none()

            if not draft:
                logger.error(f"Draft {draft_id} not found")
                slack_bot = get_slack_bot()
                await slack_bot.remove_buttons(
                    message_ts=message_ts,
                    final_text="Error: Draft not found. It may have been deleted."
                )
                return

            conversation = draft.conversation

            if not conversation.linkedin_account_id:
                logger.error(f"No linkedin_account_id for conversation {conversation.id}")
                slack_bot = get_slack_bot()
                await slack_bot.remove_buttons(
                    message_ts=message_ts,
                    final_text="Failed: Missing LinkedIn account ID. Cannot send message."
                )
                return

            # Send via HeyReach
            heyreach = get_heyreach_client()
            await heyreach.send_message(
                conversation_id=conversation.heyreach_lead_id,
                linkedin_account_id=conversation.linkedin_account_id,
                message=draft.ai_draft,
            )

            # Update draft status
            draft.status = DraftStatus.APPROVED

            # Log outbound message
            message_log = MessageLog(
                conversation_id=conversation.id,
                direction=MessageDirection.OUTBOUND,
                content=draft.ai_draft,
            )
            session.add(message_log)

            await session.commit()

            # Update Slack message
            slack_bot = get_slack_bot()
            await slack_bot.remove_buttons(
                message_ts=message_ts,
                final_text="Message sent successfully!"
            )

            # Send follow-up configuration message
            await slack_bot.send_follow_up_config_message(
                conversation_id=conversation.id,
                lead_name=conversation.lead_name,
            )

            logger.info(f"Approved and sent draft {draft_id}")

    except HeyReachError as e:
        logger.error(f"HeyReach error approving draft {draft_id}: {e}")
        slack_bot = get_slack_bot()
        await slack_bot.remove_buttons(
            message_ts=message_ts,
            final_text=f"Failed to send: {e}"
        )
    except Exception as e:
        logger.error(f"Error approving draft {draft_id}: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.remove_buttons(
            message_ts=message_ts,
            final_text=f"Error: {e}"
        )


async def handle_edit(
    draft_id: uuid.UUID,
    trigger_id: str,
) -> None:
    """Handle edit action - open modal with current draft.

    Must be synchronous (trigger_id expires quickly).

    Args:
        draft_id: The draft to edit.
        trigger_id: Slack trigger ID for opening modal.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Draft).where(Draft.id == draft_id)
        )
        draft = result.scalar_one_or_none()

        if not draft:
            logger.error(f"Draft {draft_id} not found for edit")
            slack_bot = get_slack_bot()
            await slack_bot.send_confirmation(
                "Error: Draft not found. It may have been deleted."
            )
            return

        slack_bot = get_slack_bot()
        await slack_bot.open_modal_for_edit(
            trigger_id=trigger_id,
            draft_id=draft_id,
            current_draft=draft.ai_draft,
        )


async def handle_regenerate(
    draft_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle regenerate action - generate new AI draft.

    Args:
        draft_id: The draft to regenerate.
        message_ts: Slack message timestamp for updates.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(_process_regenerate, draft_id, message_ts)


async def _process_regenerate(draft_id: uuid.UUID, message_ts: str) -> None:
    """Background task to process regeneration."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Draft)
                .options(selectinload(Draft.conversation))
                .where(Draft.id == draft_id)
            )
            draft = result.scalar_one_or_none()

            if not draft:
                logger.error(f"Draft {draft_id} not found for regenerate")
                slack_bot = get_slack_bot()
                await slack_bot.remove_buttons(
                    message_ts=message_ts,
                    final_text="Error: Draft not found. It may have been deleted."
                )
                return

            conversation = draft.conversation

            # Build lead_context from linked prospect
            lead_context = None
            if conversation.linkedin_profile_url:
                normalized_url = conversation.linkedin_profile_url.lower().strip().rstrip("/")
                if "?" in normalized_url:
                    normalized_url = normalized_url.split("?")[0]
                prospect_result = await session.execute(
                    select(Prospect).where(Prospect.linkedin_url == normalized_url)
                )
                prospect = prospect_result.scalar_one_or_none()
                if prospect:
                    lead_context = {
                        "company": prospect.company_name,
                        "title": prospect.job_title,
                        "triggering_message": draft.triggering_message,
                        "personalized_message": prospect.personalized_message,
                    }

            # Generate new AI draft
            new_draft = await generate_reply_draft(
                lead_name=conversation.lead_name,
                lead_message=conversation.conversation_history[-1].get("content", "") if conversation.conversation_history else "",
                conversation_history=conversation.conversation_history or [],
                lead_context=lead_context,
            )

            # Update draft in database
            draft.ai_draft = new_draft.reply
            await session.commit()

            # Rebuild Slack message with new draft
            slack_bot = get_slack_bot()
            blocks = build_draft_message(
                lead_name=conversation.lead_name,
                lead_title=None,
                lead_company=None,
                linkedin_url=f"https://www.linkedin.com/messaging/thread/{conversation.heyreach_lead_id}",
                lead_message=conversation.conversation_history[-1].get("content", "") if conversation.conversation_history else "",
                ai_draft=new_draft.reply,
            )
            blocks.extend(build_action_buttons(draft_id))

            await slack_bot.update_message(
                message_ts=message_ts,
                text=f"Regenerated reply for {conversation.lead_name}",
                blocks=blocks,
            )

            logger.info(f"Regenerated draft {draft_id}")

    except Exception as e:
        logger.error(f"Error regenerating draft {draft_id}: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Failed to regenerate draft: {e}")


async def handle_reject(
    draft_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle reject action - mark draft as rejected.

    Args:
        draft_id: The draft to reject.
        message_ts: Slack message timestamp for updates.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(_process_reject, draft_id, message_ts)


async def _process_reject(draft_id: uuid.UUID, message_ts: str) -> None:
    """Background task to process rejection."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Draft).where(Draft.id == draft_id)
            )
            draft = result.scalar_one_or_none()

            if not draft:
                logger.error(f"Draft {draft_id} not found for reject")
                slack_bot = get_slack_bot()
                await slack_bot.remove_buttons(
                    message_ts=message_ts,
                    final_text="Error: Draft not found. It may have been deleted."
                )
                return

            draft.status = DraftStatus.REJECTED
            await session.commit()

            slack_bot = get_slack_bot()
            await slack_bot.remove_buttons(
                message_ts=message_ts,
                final_text="Skipped - draft rejected."
            )

            logger.info(f"Rejected draft {draft_id}")

    except Exception as e:
        logger.error(f"Error rejecting draft {draft_id}: {e}", exc_info=True)


async def handle_snooze(
    draft_id: uuid.UUID,
    message_ts: str,
    duration: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle snooze action - delay draft for later.

    Args:
        draft_id: The draft to snooze.
        message_ts: Slack message timestamp for updates.
        duration: Snooze duration ("1h", "4h", "tomorrow").
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(_process_snooze, draft_id, message_ts, duration)


async def _process_snooze(draft_id: uuid.UUID, message_ts: str, duration: str) -> None:
    """Background task to process snooze."""
    try:
        # Calculate snooze end time
        now = datetime.now(timezone.utc)
        if duration == "1h":
            snooze_until = now + timedelta(hours=1)
            friendly = "1 hour"
        elif duration == "4h":
            snooze_until = now + timedelta(hours=4)
            friendly = "4 hours"
        elif duration == "tomorrow":
            # Tomorrow at 9am UTC
            tomorrow = now + timedelta(days=1)
            snooze_until = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
            friendly = "tomorrow at 9am UTC"
        else:
            logger.error(f"Unknown snooze duration: {duration}")
            return

        async with async_session_factory() as session:
            result = await session.execute(
                select(Draft).where(Draft.id == draft_id)
            )
            draft = result.scalar_one_or_none()

            if not draft:
                logger.error(f"Draft {draft_id} not found for snooze")
                slack_bot = get_slack_bot()
                await slack_bot.remove_buttons(
                    message_ts=message_ts,
                    final_text="Error: Draft not found. It may have been deleted."
                )
                return

            draft.status = DraftStatus.SNOOZED
            draft.snooze_until = snooze_until
            await session.commit()

            # TODO: Schedule reminder using scheduler service
            # scheduler = get_scheduler()
            # scheduler.add_snooze_reminder(draft_id, snooze_until)

            slack_bot = get_slack_bot()
            await slack_bot.remove_buttons(
                message_ts=message_ts,
                final_text=f"Snoozed for {friendly}. Will remind you later."
            )

            logger.info(f"Snoozed draft {draft_id} until {snooze_until}")

    except Exception as e:
        logger.error(f"Error snoozing draft {draft_id}: {e}", exc_info=True)


async def handle_modal_submit(
    draft_id: uuid.UUID,
    edited_text: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle modal submission - send edited draft.

    Args:
        draft_id: The draft that was edited.
        edited_text: The user's edited text.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(_process_modal_submit, draft_id, edited_text)


async def _process_modal_submit(draft_id: uuid.UUID, edited_text: str) -> None:
    """Background task to process modal submission."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Draft)
                .options(selectinload(Draft.conversation))
                .where(Draft.id == draft_id)
            )
            draft = result.scalar_one_or_none()

            if not draft:
                logger.error(f"Draft {draft_id} not found for modal submit")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation(
                    "Error: Draft not found. It may have been deleted."
                )
                return

            conversation = draft.conversation

            if not conversation.linkedin_account_id:
                logger.error(f"No linkedin_account_id for conversation {conversation.id}")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation(
                    "Failed: Missing LinkedIn account ID. Cannot send message."
                )
                return

            # Update draft with edited text
            draft.ai_draft = edited_text

            # Send via HeyReach
            heyreach = get_heyreach_client()
            await heyreach.send_message(
                conversation_id=conversation.heyreach_lead_id,
                linkedin_account_id=conversation.linkedin_account_id,
                message=edited_text,
            )

            # Update draft status
            draft.status = DraftStatus.APPROVED

            # Log outbound message
            message_log = MessageLog(
                conversation_id=conversation.id,
                direction=MessageDirection.OUTBOUND,
                content=edited_text,
            )
            session.add(message_log)

            await session.commit()

            # Update original Slack message if we have the ts
            slack_bot = get_slack_bot()
            if draft.slack_message_ts:
                await slack_bot.remove_buttons(
                    message_ts=draft.slack_message_ts,
                    final_text="Edited message sent successfully!"
                )

            # Send follow-up configuration message
            await slack_bot.send_follow_up_config_message(
                conversation_id=conversation.id,
                lead_name=conversation.lead_name,
            )

            logger.info(f"Sent edited draft {draft_id}")

    except HeyReachError as e:
        logger.error(f"HeyReach error sending edited draft {draft_id}: {e}")
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Failed to send edited message: {e}")
    except Exception as e:
        logger.error(f"Error sending edited draft {draft_id}: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error: {e}")


async def handle_configure_followups(
    conversation_id: uuid.UUID,
    trigger_id: str,
    message_ts: str,
) -> None:
    """Handle configure_followups button - open modal for follow-up config.

    Args:
        conversation_id: The conversation to configure follow-ups for.
        trigger_id: Slack trigger ID for opening modal.
        message_ts: Slack message timestamp for updates.
    """
    try:
        async with async_session_factory() as session:
            # Get conversation
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()

            if not conversation:
                logger.error(f"Conversation {conversation_id} not found")
                slack_bot = get_slack_bot()
                await slack_bot.remove_buttons(
                    message_ts=message_ts,
                    final_text="Error: Conversation not found."
                )
                return

            # Get personalized_message from Prospect
            personalized_message = await get_prospect_personalized_message(
                session, conversation.linkedin_profile_url
            )

            slack_bot = get_slack_bot()
            await slack_bot.open_follow_up_modal(
                trigger_id=trigger_id,
                conversation_id=conversation_id,
                personalized_message=personalized_message,
                suggested_follow_up1="",
            )

    except Exception as e:
        logger.error(f"Error opening follow-up modal: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error opening modal: {e}")


async def handle_skip_followups(
    conversation_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle skip_followups button - skip adding to list.

    Args:
        conversation_id: The conversation to skip follow-ups for.
        message_ts: Slack message timestamp for updates.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(_process_skip_followups, conversation_id, message_ts)


async def _process_skip_followups(
    conversation_id: uuid.UUID,
    message_ts: str,
) -> None:
    """Background task to process skip follow-ups."""
    try:
        slack_bot = get_slack_bot()
        await slack_bot.remove_buttons(
            message_ts=message_ts,
            final_text="Skipped - follow-ups not configured."
        )
        logger.info(f"Skipped follow-ups for conversation {conversation_id}")
    except Exception as e:
        logger.error(f"Error skipping follow-ups: {e}", exc_info=True)


async def handle_followup_modal_submit(
    conversation_id: uuid.UUID,
    follow_up1: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle follow-up modal submission.

    Args:
        conversation_id: The conversation to add to list.
        follow_up1: The FOLLOW_UP1 message from user input.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(_process_followup_submit, conversation_id, follow_up1)


async def _process_followup_submit(
    conversation_id: uuid.UUID,
    follow_up1: str,
) -> None:
    """Background task to process follow-up submission and add to list."""
    try:
        async with async_session_factory() as session:
            # Get conversation
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()

            if not conversation:
                logger.error(f"Conversation {conversation_id} not found for follow-up")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation(
                    "Error: Conversation not found when saving follow-ups."
                )
                return

            # Determine FOLLOW_UP2 based on Google Docs links
            docs_count = count_google_docs_links(conversation.conversation_history)
            if docs_count >= 2:
                follow_up2 = DEFAULT_FOLLOW_UP2
                logger.info(
                    f"Found {docs_count} Google Docs links, using default FOLLOW_UP2"
                )
            else:
                # Default to the message anyway as per user's request
                follow_up2 = DEFAULT_FOLLOW_UP2
                logger.info(
                    f"Found {docs_count} Google Docs links, defaulting to FOLLOW_UP2"
                )

            # Build follow-up messages
            follow_up_messages = {
                "FOLLOW_UP1": follow_up1,
                "FOLLOW_UP2": follow_up2,
                "FOLLOW_UP3": "",  # Leave empty for now
            }

            # Add to HeyReach list
            await add_prospect_to_follow_up_list(conversation, follow_up_messages)

            # Update prospect with follow-up tracking info
            await update_prospect_followup_tracking(
                session,
                conversation.linkedin_profile_url,
                HEYREACH_FOLLOW_UP_LIST_ID,
            )
            await session.commit()

            # Send confirmation
            slack_bot = get_slack_bot()
            await slack_bot.send_confirmation(
                f"Added {conversation.lead_name} to follow-up list with custom messages."
            )

    except Exception as e:
        logger.error(f"Error processing follow-up submission: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error saving follow-ups: {e}")


# =============================================================================
# Funnel Stage Handlers
# =============================================================================


async def handle_funnel_pitched(
    draft_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle funnel_pitched action - mark prospect as pitched."""
    background_tasks.add_task(_process_funnel_stage, draft_id, message_ts, "pitched")


async def handle_funnel_calendar_sent(
    draft_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle funnel_calendar_sent action - mark prospect as calendar sent."""
    background_tasks.add_task(_process_funnel_stage, draft_id, message_ts, "calendar_sent")


async def _process_funnel_stage(
    draft_id: uuid.UUID,
    message_ts: str,
    stage: str,
) -> None:
    """Background task to update prospect funnel stage."""
    stage_labels = {
        "pitched": "Pitched",
        "calendar_sent": "Calendar Shown",
    }

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Draft)
                .options(selectinload(Draft.conversation))
                .where(Draft.id == draft_id)
            )
            draft = result.scalar_one_or_none()

            if not draft:
                logger.error(f"Draft {draft_id} not found for funnel stage update")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation("Error: Draft not found.")
                return

            conversation = draft.conversation

            # Find linked prospect
            normalized_url = conversation.linkedin_profile_url.lower().strip().rstrip("/")
            if "?" in normalized_url:
                normalized_url = normalized_url.split("?")[0]

            prospect_result = await session.execute(
                select(Prospect).where(Prospect.linkedin_url == normalized_url)
            )
            prospect = prospect_result.scalar_one_or_none()

            now = datetime.now(timezone.utc)

            if prospect:
                if stage == "pitched":
                    if not prospect.pitched_at:
                        prospect.pitched_at = now
                elif stage == "calendar_sent":
                    if not prospect.pitched_at:
                        prospect.pitched_at = now
                    if not prospect.calendar_sent_at:
                        prospect.calendar_sent_at = now
            else:
                logger.warning(f"No prospect found for {normalized_url}")

            # Update conversation funnel stage too
            if stage == "pitched":
                conversation.funnel_stage = FunnelStage.PITCHED
                new_funnel_stage = FunnelStage.PITCHED
            elif stage == "calendar_sent":
                conversation.funnel_stage = FunnelStage.CALENDAR_SENT
                new_funnel_stage = FunnelStage.CALENDAR_SENT

            await session.commit()

            # Post or update pitched channel card
            if prospect and stage in ("pitched", "calendar_sent"):
                try:
                    await _post_or_update_pitched_card(session, prospect, new_funnel_stage)
                    await session.commit()
                except Exception as card_err:
                    logger.error(f"Failed to post/update pitched card: {card_err}", exc_info=True)

            label = stage_labels.get(stage, stage)
            slack_bot = get_slack_bot()
            name = prospect.full_name if prospect else conversation.lead_name
            await slack_bot.send_confirmation(
                f"Marked {name} as: {label}"
            )

            logger.info(f"Updated funnel stage to {stage} for draft {draft_id}")

    except Exception as e:
        logger.error(f"Error updating funnel stage for draft {draft_id}: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error updating funnel stage: {e}")


# =============================================================================
# Classification Handlers
# =============================================================================


async def handle_classify_positive(
    draft_id: uuid.UUID,
    message_ts: str,
    slack_user_id: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle classify_positive action - mark reply as positive.

    Args:
        draft_id: The draft to classify.
        message_ts: Slack message timestamp for updates.
        slack_user_id: ID of the Slack user who clicked the button.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(
        _process_classification, draft_id, message_ts, ReplyClassification.POSITIVE, slack_user_id
    )


async def handle_classify_not_interested(
    draft_id: uuid.UUID,
    message_ts: str,
    slack_user_id: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle classify_not_interested action.

    Args:
        draft_id: The draft to classify.
        message_ts: Slack message timestamp for updates.
        slack_user_id: ID of the Slack user who clicked the button.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(
        _process_classification, draft_id, message_ts, ReplyClassification.NOT_INTERESTED, slack_user_id
    )


async def handle_classify_not_icp(
    draft_id: uuid.UUID,
    trigger_id: str,
) -> None:
    """Handle classify_not_icp action - open modal for optional notes.

    Args:
        draft_id: The draft to classify.
        trigger_id: Slack trigger ID for opening modal.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Draft)
            .options(selectinload(Draft.conversation))
            .where(Draft.id == draft_id)
        )
        draft = result.scalar_one_or_none()

        if not draft:
            logger.error(f"Draft {draft_id} not found for Not ICP classification")
            slack_bot = get_slack_bot()
            await slack_bot.send_confirmation("Error: Draft not found.")
            return

        conversation = draft.conversation

        # Get prospect info for modal
        normalized_url = conversation.linkedin_profile_url.lower().strip().rstrip("/")
        if "?" in normalized_url:
            normalized_url = normalized_url.split("?")[0]

        prospect_result = await session.execute(
            select(Prospect).where(Prospect.linkedin_url == normalized_url)
        )
        prospect = prospect_result.scalar_one_or_none()

        lead_title = prospect.job_title if prospect else None
        lead_company = prospect.company_name if prospect else None

        slack_bot = get_slack_bot()
        await slack_bot.open_not_icp_modal(
            trigger_id=trigger_id,
            draft_id=draft_id,
            lead_name=conversation.lead_name,
            lead_title=lead_title,
            lead_company=lead_company,
        )


async def _process_classification(
    draft_id: uuid.UUID,
    message_ts: str,
    classification: ReplyClassification,
    slack_user_id: str,
    notes: str | None = None,
) -> None:
    """Background task to process classification.

    Args:
        draft_id: The draft to classify.
        message_ts: Slack message timestamp for updates (may be empty for modal).
        classification: The classification to apply.
        slack_user_id: ID of the Slack user who classified.
        notes: Optional notes (for Not ICP).
    """
    from datetime import datetime, timezone

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Draft)
                .options(selectinload(Draft.conversation))
                .where(Draft.id == draft_id)
            )
            draft = result.scalar_one_or_none()

            if not draft:
                logger.error(f"Draft {draft_id} not found for classification")
                if message_ts:
                    slack_bot = get_slack_bot()
                    await slack_bot.send_confirmation("Error: Draft not found.")
                return

            # Update draft classification
            draft.classification = classification
            draft.classified_at = datetime.now(timezone.utc)

            # Get conversation and prospect for classification updates
            conversation = draft.conversation
            normalized_url = conversation.linkedin_profile_url.lower().strip().rstrip("/")
            if "?" in normalized_url:
                normalized_url = normalized_url.split("?")[0]

            prospect_result = await session.execute(
                select(Prospect).where(Prospect.linkedin_url == normalized_url)
            )
            prospect = prospect_result.scalar_one_or_none()

            # If Positive, update prospect's positive_reply_at
            if classification == ReplyClassification.POSITIVE:
                if prospect:
                    prospect.positive_reply_at = datetime.now(timezone.utc)
                    logger.info(f"Set positive_reply_at for prospect {prospect.id}")
                else:
                    logger.warning(f"No prospect found for {normalized_url} to mark as positive")

            # If Not ICP, create ICPFeedback record
            elif classification == ReplyClassification.NOT_ICP:
                feedback = ICPFeedback(
                    lead_name=conversation.lead_name,
                    linkedin_url=normalized_url,
                    job_title=prospect.job_title if prospect else None,
                    company_name=prospect.company_name if prospect else None,
                    original_icp_match=prospect.icp_match if prospect else None,
                    original_icp_reason=prospect.icp_reason if prospect else None,
                    notes=notes,
                    marked_by_slack_user=slack_user_id,
                    draft_id=draft_id,
                )
                session.add(feedback)
                logger.info(f"Created ICPFeedback for draft {draft_id}")

            await session.commit()

            # Send confirmation
            classification_labels = {
                ReplyClassification.POSITIVE: "\U0001f44d Positive Reply",
                ReplyClassification.NOT_INTERESTED: "\U0001f44e Not Interested",
                ReplyClassification.NOT_ICP: "\U0001f6ab Not ICP",
            }
            label = classification_labels.get(classification, str(classification.value))

            slack_bot = get_slack_bot()
            await slack_bot.send_confirmation(f"Classified as: {label}")

            logger.info(f"Classified draft {draft_id} as {classification.value}")

    except Exception as e:
        logger.error(f"Error classifying draft {draft_id}: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error classifying: {e}")


async def handle_not_icp_modal_submit(
    draft_id: uuid.UUID,
    notes: str | None,
    slack_user_id: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle Not ICP modal submission.

    Args:
        draft_id: The draft to classify.
        notes: Optional notes from the modal.
        slack_user_id: ID of the Slack user who submitted.
        background_tasks: FastAPI background tasks.
    """
    background_tasks.add_task(
        _process_classification, draft_id, "", ReplyClassification.NOT_ICP, slack_user_id, notes
    )


# =============================================================================
# Engagement Action Handlers
# =============================================================================


async def handle_engagement_done(
    post_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle engagement_done action - mark post as DONE."""
    background_tasks.add_task(_process_engagement_done, post_id, message_ts)


async def _process_engagement_done(post_id: uuid.UUID, message_ts: str) -> None:
    """Background task to process engagement done."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(EngagementPost).where(EngagementPost.id == post_id)
            )
            post = result.scalar_one_or_none()

            if not post:
                logger.error(f"EngagementPost {post_id} not found")
                return

            post.status = EngagementPostStatus.DONE
            await session.commit()

            slack_bot = get_slack_bot()
            await slack_bot.update_engagement_message(
                message_ts=message_ts,
                text="Commented - done!",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "Commented - done!"},
                    }
                ],
            )

            logger.info(f"Engagement post {post_id} marked as DONE")

    except Exception as e:
        logger.error(f"Error marking engagement done {post_id}: {e}", exc_info=True)


async def handle_engagement_edit(
    post_id: uuid.UUID,
    trigger_id: str,
) -> None:
    """Handle engagement_edit action - open modal with current draft."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(EngagementPost).where(EngagementPost.id == post_id)
        )
        post = result.scalar_one_or_none()

        if not post:
            logger.error(f"EngagementPost {post_id} not found for edit")
            slack_bot = get_slack_bot()
            await slack_bot.send_confirmation("Error: Engagement post not found.")
            return

        slack_bot = get_slack_bot()
        await slack_bot.open_engagement_edit_modal(
            trigger_id=trigger_id,
            post_id=post_id,
            current_comment=post.draft_comment or "",
        )


async def handle_engagement_skip(
    post_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle engagement_skip action - mark post as SKIPPED."""
    background_tasks.add_task(_process_engagement_skip, post_id, message_ts)


async def _process_engagement_skip(post_id: uuid.UUID, message_ts: str) -> None:
    """Background task to process engagement skip."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(EngagementPost).where(EngagementPost.id == post_id)
            )
            post = result.scalar_one_or_none()

            if not post:
                logger.error(f"EngagementPost {post_id} not found for skip")
                return

            post.status = EngagementPostStatus.SKIPPED
            await session.commit()

            slack_bot = get_slack_bot()
            await slack_bot.update_engagement_message(
                message_ts=message_ts,
                text="Skipped",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "Skipped"},
                    }
                ],
            )

            logger.info(f"Engagement post {post_id} marked as SKIPPED")

    except Exception as e:
        logger.error(f"Error skipping engagement {post_id}: {e}", exc_info=True)


async def handle_engagement_edit_submit(
    post_id: uuid.UUID,
    edited_comment: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle engagement edit modal submission."""
    background_tasks.add_task(_process_engagement_edit_submit, post_id, edited_comment)


async def _process_engagement_edit_submit(
    post_id: uuid.UUID,
    edited_comment: str,
) -> None:
    """Background task to process engagement edit submission."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(EngagementPost).where(EngagementPost.id == post_id)
            )
            post = result.scalar_one_or_none()

            if not post:
                logger.error(f"EngagementPost {post_id} not found for edit submit")
                return

            post.draft_comment = edited_comment
            post.status = EngagementPostStatus.EDITED
            await session.commit()

            # Update Slack message to show it was edited
            if post.slack_message_ts:
                slack_bot = get_slack_bot()
                await slack_bot.update_engagement_message(
                    message_ts=post.slack_message_ts,
                    text="Comment edited and saved",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"Comment edited:\n```{edited_comment}```",
                            },
                        }
                    ],
                )

            logger.info(f"Engagement post {post_id} comment edited")

    except Exception as e:
        logger.error(f"Error editing engagement {post_id}: {e}", exc_info=True)


# =============================================================================
# Gift Leads Handlers
# =============================================================================


async def handle_gift_leads(
    draft_id: uuid.UUID,
    trigger_id: str,
) -> None:
    """Handle gift_leads button click - open modal with ICP input."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Draft)
                .options(selectinload(Draft.conversation))
                .where(Draft.id == draft_id)
            )
            draft = result.scalar_one_or_none()

            if not draft:
                logger.error(f"Draft {draft_id} not found for gift leads")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation("Error: Draft not found.")
                return

            conversation = draft.conversation
            normalized_url = conversation.linkedin_profile_url.lower().strip().rstrip("/")
            if "?" in normalized_url:
                normalized_url = normalized_url.split("?")[0]

            prospect_result = await session.execute(
                select(Prospect).where(Prospect.linkedin_url == normalized_url)
            )
            prospect = prospect_result.scalar_one_or_none()

            prospect_name = conversation.lead_name
            prospect_id = prospect.id if prospect else uuid.uuid4()
            prefill_icp = prospect.icp_reason or "" if prospect else ""

            slack_bot = get_slack_bot()
            await slack_bot.open_gift_leads_modal(
                trigger_id=trigger_id,
                prospect_id=prospect_id,
                prospect_name=prospect_name,
                prefill_icp=prefill_icp,
            )

    except Exception as e:
        logger.error(f"Error opening gift leads modal: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error opening gift leads: {e}")


async def _process_gift_leads(
    prospect_id: uuid.UUID,
    keywords: list[str],
    prospect_name: str,
) -> None:
    """Background task to search DB and post gift leads results."""
    try:
        async with async_session_factory() as session:
            from sqlalchemy import or_, func

            conditions = []
            for kw in keywords:
                kw = kw.strip()
                if not kw:
                    continue
                conditions.append(Prospect.job_title.ilike(f"%{kw}%"))
                conditions.append(Prospect.headline.ilike(f"%{kw}%"))

            if not conditions:
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation("No valid keywords provided.")
                return

            pool_result = await session.execute(
                select(func.count(Prospect.id)).where(
                    Prospect.activity_score.isnot(None)
                )
            )
            pool_size = pool_result.scalar() or 0

            result = await session.execute(
                select(Prospect)
                .where(
                    or_(*conditions),
                    Prospect.activity_score.isnot(None),
                )
                .order_by(Prospect.activity_score.desc().nullslast())
                .limit(15)
            )
            prospects = result.scalars().all()

            leads = [
                {
                    "full_name": p.full_name,
                    "job_title": p.job_title,
                    "company_name": p.company_name,
                    "location": p.location,
                    "headline": p.headline,
                    "activity_score": float(p.activity_score) if p.activity_score else 0,
                    "linkedin_url": p.linkedin_url,
                }
                for p in prospects
            ]

        slack_bot = get_slack_bot()

        if leads:
            await slack_bot.send_gift_leads_results(
                prospect_name=prospect_name,
                leads=leads,
                pool_size=pool_size,
                keywords=keywords,
            )
        else:
            await slack_bot.send_confirmation(
                f"No matching leads found for keywords: {', '.join(keywords)}\n"
                f"Pool has {pool_size} prospects with activity scores.\n"
                f"Run the gift leads pipeline locally to build the pool."
            )

    except Exception as e:
        logger.error(f"Error processing gift leads: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error finding gift leads: {e}")


# =============================================================================
# Pitched Channel Helpers and Handlers
# =============================================================================


async def _get_recent_inbound_messages(
    session,
    prospect: Prospect,
) -> list[dict[str, str]]:
    """Get the last 3 inbound messages for a prospect's conversation.

    Args:
        session: Database session.
        prospect: The prospect (must have conversation_id).

    Returns:
        List of dicts with 'content' key, newest first.
    """
    if not prospect.conversation_id:
        return []

    result = await session.execute(
        select(MessageLog)
        .where(
            MessageLog.conversation_id == prospect.conversation_id,
            MessageLog.direction == MessageDirection.INBOUND,
        )
        .order_by(MessageLog.sent_at.desc())
        .limit(3)
    )
    messages = result.scalars().all()
    return [{"content": m.content} for m in messages]


async def _post_or_update_pitched_card(
    session,
    prospect: Prospect,
    funnel_stage: FunnelStage,
) -> None:
    """Post a new pitched card or update an existing one.

    Args:
        session: Database session (caller must commit).
        prospect: The prospect to create/update card for.
        funnel_stage: The current funnel stage.
    """
    slack_bot = get_slack_bot()
    recent_messages = await _get_recent_inbound_messages(session, prospect)

    if prospect.pitched_slack_ts:
        # Update existing card
        await slack_bot.update_pitched_card(
            message_ts=prospect.pitched_slack_ts,
            prospect_id=prospect.id,
            lead_name=prospect.full_name or "Unknown",
            lead_title=prospect.job_title,
            lead_company=prospect.company_name,
            linkedin_url=prospect.linkedin_url,
            funnel_stage=funnel_stage,
            recent_messages=recent_messages,
        )
    else:
        # Post new card
        ts = await slack_bot.send_pitched_card(
            prospect_id=prospect.id,
            lead_name=prospect.full_name or "Unknown",
            lead_title=prospect.job_title,
            lead_company=prospect.company_name,
            linkedin_url=prospect.linkedin_url,
            funnel_stage=funnel_stage,
            recent_messages=recent_messages,
        )
        prospect.pitched_slack_ts = ts


async def handle_pitched_send_message(
    prospect_id: uuid.UUID,
    trigger_id: str,
) -> None:
    """Handle pitched_send_message action - open modal to compose message.

    Args:
        prospect_id: The prospect to send to.
        trigger_id: Slack trigger ID for opening modal.
    """
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Prospect).where(Prospect.id == prospect_id)
            )
            prospect = result.scalar_one_or_none()

            if not prospect:
                logger.error(f"Prospect {prospect_id} not found for pitched send message")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation("Error: Prospect not found.")
                return

            slack_bot = get_slack_bot()
            await slack_bot.open_pitched_send_message_modal(
                trigger_id=trigger_id,
                prospect_id=prospect_id,
                lead_name=prospect.full_name or "Unknown",
            )

    except Exception as e:
        logger.error(f"Error opening pitched send modal: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error opening modal: {e}")


async def handle_pitched_calendar_sent(
    prospect_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle pitched_calendar_sent action from pitched channel card."""
    background_tasks.add_task(
        _process_pitched_stage_update, prospect_id, message_ts, "calendar_sent"
    )


async def handle_pitched_booked(
    prospect_id: uuid.UUID,
    message_ts: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle pitched_booked action from pitched channel card."""
    background_tasks.add_task(
        _process_pitched_stage_update, prospect_id, message_ts, "booked"
    )


async def _process_pitched_stage_update(
    prospect_id: uuid.UUID,
    message_ts: str,
    stage: str,
) -> None:
    """Background task to update prospect stage from pitched channel buttons.

    Args:
        prospect_id: The prospect to update.
        message_ts: The pitched card message_ts.
        stage: 'calendar_sent' or 'booked'.
    """
    stage_labels = {
        "calendar_sent": "Calendar Sent",
        "booked": "Booked",
    }

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Prospect).where(Prospect.id == prospect_id)
            )
            prospect = result.scalar_one_or_none()

            if not prospect:
                logger.error(f"Prospect {prospect_id} not found for pitched stage update")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation("Error: Prospect not found.")
                return

            now = datetime.now(timezone.utc)

            if stage == "calendar_sent":
                if not prospect.pitched_at:
                    prospect.pitched_at = now
                if not prospect.calendar_sent_at:
                    prospect.calendar_sent_at = now
                new_funnel_stage = FunnelStage.CALENDAR_SENT
            elif stage == "booked":
                if not prospect.pitched_at:
                    prospect.pitched_at = now
                if not prospect.calendar_sent_at:
                    prospect.calendar_sent_at = now
                if not prospect.booked_at:
                    prospect.booked_at = now
                new_funnel_stage = FunnelStage.BOOKED

            # Update conversation funnel stage if linked
            if prospect.conversation_id:
                conv_result = await session.execute(
                    select(Conversation).where(
                        Conversation.id == prospect.conversation_id
                    )
                )
                conversation = conv_result.scalar_one_or_none()
                if conversation:
                    conversation.funnel_stage = new_funnel_stage

            await session.commit()

            # Update the pitched card
            await _post_or_update_pitched_card(session, prospect, new_funnel_stage)
            await session.commit()

            label = stage_labels.get(stage, stage)
            slack_bot = get_slack_bot()
            await slack_bot.send_confirmation(
                f"Marked {prospect.full_name or 'prospect'} as: {label}"
            )

            logger.info(f"Updated pitched stage to {stage} for prospect {prospect_id}")

    except Exception as e:
        logger.error(f"Error updating pitched stage for {prospect_id}: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error updating stage: {e}")


async def handle_pitched_send_message_submit(
    prospect_id: uuid.UUID,
    message_text: str,
    schedule_time: int | None,
    background_tasks: BackgroundTasks,
) -> None:
    """Handle pitched send message modal submission.

    Args:
        prospect_id: The prospect to send to.
        message_text: The message content.
        schedule_time: Optional Unix timestamp for scheduled send.
        background_tasks: FastAPI background tasks.
    """
    if schedule_time:
        # Schedule for later
        background_tasks.add_task(
            _schedule_pitched_message, prospect_id, message_text, schedule_time
        )
    else:
        # Send immediately
        background_tasks.add_task(
            _send_pitched_message_now, prospect_id, message_text
        )


async def _schedule_pitched_message(
    prospect_id: uuid.UUID,
    message_text: str,
    schedule_timestamp: int,
) -> None:
    """Schedule a message to be sent later via APScheduler."""
    from app.services.scheduler import get_scheduler_service

    run_time = datetime.fromtimestamp(schedule_timestamp, tz=timezone.utc)
    scheduler = get_scheduler_service()
    job_id = scheduler.add_scheduled_message(prospect_id, message_text, run_time)

    slack_bot = get_slack_bot()
    await slack_bot.send_confirmation(
        f"Message scheduled for {run_time.strftime('%b %d at %H:%M UTC')}."
    )

    logger.info(f"Scheduled pitched message for prospect {prospect_id} at {run_time}, job={job_id}")


async def _send_pitched_message_now(
    prospect_id: uuid.UUID,
    message_text: str,
) -> None:
    """Send a message immediately from the pitched channel."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Prospect).where(Prospect.id == prospect_id)
            )
            prospect = result.scalar_one_or_none()

            if not prospect:
                logger.error(f"Prospect {prospect_id} not found")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation(
                    f"Error: Prospect {prospect_id} not found."
                )
                return

            # Auto-link conversation if missing
            if not prospect.conversation_id and prospect.linkedin_url:
                conv_search = await session.execute(
                    select(Conversation).where(
                        func.lower(Conversation.linkedin_profile_url)
                        == prospect.linkedin_url.lower().strip().rstrip("/")
                    )
                )
                found_conv = conv_search.scalar_one_or_none()
                if found_conv:
                    prospect.conversation_id = found_conv.id
                    await session.commit()
                    logger.info(
                        f"Auto-linked conversation {found_conv.id} to prospect {prospect_id}"
                    )

            if not prospect.conversation_id:
                logger.error(
                    f"Prospect {prospect.full_name} ({prospect_id}) has no conversation linked"
                )
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation(
                    f"Cannot send to {prospect.full_name or 'prospect'}: "
                    "no HeyReach conversation found. They may not have replied yet."
                )
                return

            conv_result = await session.execute(
                select(Conversation).where(
                    Conversation.id == prospect.conversation_id
                )
            )
            conversation = conv_result.scalar_one_or_none()

            if not conversation or not conversation.linkedin_account_id:
                logger.error(f"No linkedin_account_id for prospect {prospect_id}")
                slack_bot = get_slack_bot()
                await slack_bot.send_confirmation(
                    "Failed: Missing LinkedIn account ID. Cannot send message."
                )
                return

            # Send via HeyReach
            heyreach = get_heyreach_client()
            await heyreach.send_message(
                conversation_id=conversation.heyreach_lead_id,
                linkedin_account_id=conversation.linkedin_account_id,
                message=message_text,
            )

            # Log outbound message
            message_log = MessageLog(
                conversation_id=conversation.id,
                direction=MessageDirection.OUTBOUND,
                content=message_text,
            )
            session.add(message_log)
            await session.commit()

            slack_bot = get_slack_bot()
            await slack_bot.send_confirmation(
                f"Message sent to {prospect.full_name or 'prospect'}."
            )

            logger.info(f"Sent pitched message to prospect {prospect_id}")

    except HeyReachError as e:
        logger.error(f"HeyReach error sending pitched message: {e}")
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Failed to send: {e}")
    except Exception as e:
        logger.error(f"Error sending pitched message: {e}", exc_info=True)
        slack_bot = get_slack_bot()
        await slack_bot.send_confirmation(f"Error: {e}")


@router.post("/interactions")
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Handle Slack interaction callbacks (button clicks, modal submissions).

    This endpoint receives:
    - block_actions: When user clicks a button
    - view_submission: When user submits a modal

    Slack expects a response within 3 seconds, so slow operations
    are handled in background tasks.
    """
    print("=== SLACK INTERACTION RECEIVED ===", flush=True)
    # Verify signature
    body = await verify_slack_signature(request)
    print(f"Signature verified, body length: {len(body)}", flush=True)

    # Parse payload
    payload = parse_slack_payload(body)
    payload_type = payload.get("type", "")

    logger.info(f"Received Slack interaction: type={payload_type}")

    if payload_type == "block_actions":
        # Handle button clicks
        actions = payload.get("actions", [])
        if not actions:
            return {"ok": True}

        action = actions[0]
        action_id = action.get("action_id", "")
        value_str = action.get("value", "")
        message_ts = payload.get("message", {}).get("ts", "")
        trigger_id = payload.get("trigger_id", "")
        print(f"Action: {action_id}, value: {value_str[:50] if value_str else 'none'}", flush=True)

        # Handle engagement actions
        if action_id in ("engagement_done", "engagement_edit", "engagement_skip", "engagement_open_post"):
            if action_id == "engagement_open_post":
                # Link button - no server-side action needed
                return {"ok": True}

            try:
                post_id = uuid.UUID(value_str)
            except ValueError:
                logger.error(f"Invalid engagement post_id: {value_str}")
                return {"ok": True}

            if action_id == "engagement_done":
                await handle_engagement_done(post_id, message_ts, background_tasks)
            elif action_id == "engagement_edit":
                await handle_engagement_edit(post_id, trigger_id)
            elif action_id == "engagement_skip":
                await handle_engagement_skip(post_id, message_ts, background_tasks)
            return {"ok": True}

        # Handle pitched channel actions (use prospect_id)
        if action_id in ("pitched_send_message", "pitched_calendar_sent", "pitched_booked"):
            try:
                prospect_id = uuid.UUID(value_str)
            except ValueError:
                logger.error(f"Invalid prospect_id: {value_str}")
                return {"ok": True}

            if action_id == "pitched_send_message":
                await handle_pitched_send_message(prospect_id, trigger_id)
            elif action_id == "pitched_calendar_sent":
                await handle_pitched_calendar_sent(prospect_id, message_ts, background_tasks)
            elif action_id == "pitched_booked":
                await handle_pitched_booked(prospect_id, message_ts, background_tasks)
            return {"ok": True}

        # Handle follow-up configuration actions (use conversation_id)
        if action_id in ("configure_followups", "skip_followups"):
            try:
                conversation_id = uuid.UUID(value_str)
            except ValueError:
                logger.error(f"Invalid conversation_id: {value_str}")
                return {"ok": True}

            if action_id == "configure_followups":
                await handle_configure_followups(conversation_id, trigger_id, message_ts)
            elif action_id == "skip_followups":
                await handle_skip_followups(conversation_id, message_ts, background_tasks)
            return {"ok": True}

        # Handle draft actions (use draft_id)
        try:
            draft_id = uuid.UUID(value_str)
        except ValueError:
            logger.error(f"Invalid draft_id: {value_str}")
            return {"ok": True}

        # Get slack user ID for classification tracking
        slack_user_id = payload.get("user", {}).get("id", "unknown")

        # Handle gift leads action (uses draft_id)
        if action_id == "gift_leads":
            await handle_gift_leads(draft_id, trigger_id)
            return {"ok": True}

        # Route to appropriate handler
        if action_id == "approve":
            await handle_approve(draft_id, message_ts, background_tasks)
        elif action_id == "edit":
            await handle_edit(draft_id, trigger_id)
        elif action_id == "regenerate":
            await handle_regenerate(draft_id, message_ts, background_tasks)
        elif action_id == "reject":
            await handle_reject(draft_id, message_ts, background_tasks)
        elif action_id.startswith("snooze_"):
            duration = action_id.replace("snooze_", "")
            await handle_snooze(draft_id, message_ts, duration, background_tasks)
        # Funnel stage actions
        elif action_id == "funnel_pitched":
            await handle_funnel_pitched(draft_id, message_ts, background_tasks)
        elif action_id == "funnel_calendar_sent":
            await handle_funnel_calendar_sent(draft_id, message_ts, background_tasks)
        # Classification actions
        elif action_id == "classify_positive":
            await handle_classify_positive(draft_id, message_ts, slack_user_id, background_tasks)
        elif action_id == "classify_not_interested":
            await handle_classify_not_interested(draft_id, message_ts, slack_user_id, background_tasks)
        elif action_id == "classify_not_icp":
            await handle_classify_not_icp(draft_id, trigger_id)
        else:
            logger.warning(f"Unknown action_id: {action_id}")

    elif payload_type == "view_submission":
        # Handle modal submissions
        view = payload.get("view", {})
        callback_id = view.get("callback_id", "")
        private_metadata = view.get("private_metadata", "")
        values = view.get("state", {}).get("values", {})

        # Handle follow-up configuration modal
        if callback_id == "configure_followups_submit":
            try:
                conversation_id = uuid.UUID(private_metadata)
            except ValueError:
                logger.error(f"Invalid conversation_id in metadata: {private_metadata}")
                return {"ok": True}

            # Extract FOLLOW_UP1 from view values
            follow_up1 = (
                values.get("follow_up1_input", {})
                .get("follow_up1_text", {})
                .get("value", "")
            )

            await handle_followup_modal_submit(
                conversation_id, follow_up1, background_tasks
            )

        # Handle draft edit modal (format: edit_draft_{uuid})
        elif callback_id.startswith("edit_draft_"):
            try:
                draft_id = uuid.UUID(private_metadata)
            except ValueError:
                logger.error(f"Invalid draft_id in metadata: {private_metadata}")
                return {"ok": True}

            # Extract edited text from view values
            edited_text = (
                values.get("draft_input", {})
                .get("draft_text", {})
                .get("value", "")
            )

            if edited_text:
                await handle_modal_submit(draft_id, edited_text, background_tasks)

        # Handle engagement edit modal submission
        elif callback_id == "engagement_edit_submit":
            try:
                post_id = uuid.UUID(private_metadata)
            except ValueError:
                logger.error(f"Invalid post_id in metadata: {private_metadata}")
                return {"ok": True}

            edited_comment = (
                values.get("comment_input", {})
                .get("comment_text", {})
                .get("value", "")
            )

            if edited_comment:
                await handle_engagement_edit_submit(post_id, edited_comment, background_tasks)

        # Handle pitched send message modal submission
        elif callback_id == "pitched_send_message_submit":
            try:
                prospect_id = uuid.UUID(private_metadata)
            except ValueError:
                logger.error(f"Invalid prospect_id in metadata: {private_metadata}")
                return {"ok": True}

            message_text = (
                values.get("message_input", {})
                .get("message_text", {})
                .get("value", "")
            )
            schedule_time = (
                values.get("schedule_input", {})
                .get("schedule_time", {})
                .get("selected_date_time")
            )

            if message_text:
                logger.info(
                    f"Pitched message submit for prospect {prospect_id}: "
                    f"message={message_text!r}, schedule={schedule_time}"
                )
                await handle_pitched_send_message_submit(
                    prospect_id, message_text, schedule_time, background_tasks
                )

        # Handle Not ICP modal submission
        elif callback_id == "not_icp_submit":
            try:
                draft_id = uuid.UUID(private_metadata)
            except ValueError:
                logger.error(f"Invalid draft_id in metadata: {private_metadata}")
                return {"ok": True}

            # Extract notes from view values (optional)
            notes = (
                values.get("not_icp_notes_input", {})
                .get("not_icp_notes_text", {})
                .get("value")
            )

            slack_user_id = payload.get("user", {}).get("id", "unknown")
            await handle_not_icp_modal_submit(draft_id, notes, slack_user_id, background_tasks)

        # Handle gift leads modal submission
        elif callback_id == "gift_leads_submit":
            try:
                prospect_id = uuid.UUID(private_metadata)
            except ValueError:
                logger.error(f"Invalid prospect_id in metadata: {private_metadata}")
                return {"ok": True}

            keywords_text = (
                values.get("keywords_input", {})
                .get("keywords_text", {})
                .get("value", "")
            )

            keywords = [k.strip() for k in keywords_text.split(",") if k.strip()]

            if keywords:
                prospect_name = "Prospect"
                try:
                    async with async_session_factory() as session:
                        p_result = await session.execute(
                            select(Prospect).where(Prospect.id == prospect_id)
                        )
                        p = p_result.scalar_one_or_none()
                        if p:
                            prospect_name = p.full_name or "Prospect"
                except Exception:
                    pass

                background_tasks.add_task(
                    _process_gift_leads, prospect_id, keywords, prospect_name
                )

    return {"ok": True}


@router.post("/test-followup-message")
async def test_followup_message(
    lead_name: str = "Test Lead",
    conversation_id: str | None = None,
) -> dict:
    """Test endpoint to send a follow-up configuration message.

    This lets you test the modal flow without sending actual HeyReach messages.

    Args:
        lead_name: Name to display in the message.
        conversation_id: Optional conversation ID (uses random if not provided).

    Returns:
        Status and message timestamp.
    """
    import uuid as uuid_module

    test_conv_id = (
        uuid_module.UUID(conversation_id)
        if conversation_id
        else uuid_module.uuid4()
    )

    slack_bot = get_slack_bot()
    ts = await slack_bot.send_follow_up_config_message(
        conversation_id=test_conv_id,
        lead_name=lead_name,
    )

    return {
        "status": "sent",
        "message_ts": ts,
        "conversation_id": str(test_conv_id),
        "note": "Click 'Configure Follow-ups' button to test the modal. "
                "Submission will fail gracefully since no real conversation exists.",
    }


@router.post("/test-pitched-card")
async def test_pitched_card(linkedin_url: str, set_pitched: bool = False) -> dict:
    """Test endpoint to post a pitched card for a prospect by LinkedIn URL.

    This simulates what happens when you click "Pitched" on a draft.

    Args:
        linkedin_url: The prospect's LinkedIn URL.
        set_pitched: If True, also sets pitched_at and conversation funnel_stage.

    Returns:
        Status and card details.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Prospect).where(Prospect.linkedin_url == linkedin_url)
        )
        prospect = result.scalar_one_or_none()

        if not prospect:
            return {"error": f"Prospect not found: {linkedin_url}"}

        funnel_stage = FunnelStage.PITCHED
        if prospect.booked_at:
            funnel_stage = FunnelStage.BOOKED
        elif prospect.calendar_sent_at:
            funnel_stage = FunnelStage.CALENDAR_SENT

        if set_pitched and not prospect.pitched_at:
            prospect.pitched_at = datetime.now(timezone.utc)
            # Also update conversation funnel_stage
            if prospect.conversation_id:
                conv_result = await session.execute(
                    select(Conversation).where(
                        Conversation.id == prospect.conversation_id
                    )
                )
                conv = conv_result.scalar_one_or_none()
                if conv and funnel_stage == FunnelStage.PITCHED:
                    conv.funnel_stage = FunnelStage.PITCHED

        try:
            await _post_or_update_pitched_card(session, prospect, funnel_stage)
            await session.commit()
        except Exception as e:
            return {"error": str(e)}

        return {
            "status": "posted",
            "prospect": prospect.full_name,
            "funnel_stage": funnel_stage.value,
            "pitched_slack_ts": prospect.pitched_slack_ts,
        }
