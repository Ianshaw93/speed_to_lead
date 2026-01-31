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
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session_factory
from app.models import Conversation, Draft, DraftStatus, MessageDirection, MessageLog
from app.services.deepseek import generate_reply_draft
from app.services.heyreach import get_heyreach_client, HeyReachError
from app.services.slack import (
    SlackBot,
    SlackError,
    build_action_buttons,
    build_draft_message,
    get_slack_bot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])


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

            # Generate new AI draft
            new_draft = await generate_reply_draft(
                lead_name=conversation.lead_name,
                lead_message=conversation.conversation_history[-1].get("content", "") if conversation.conversation_history else "",
                conversation_history=conversation.conversation_history or [],
            )

            # Update draft in database
            draft.ai_draft = new_draft
            await session.commit()

            # Rebuild Slack message with new draft
            slack_bot = get_slack_bot()
            blocks = build_draft_message(
                lead_name=conversation.lead_name,
                lead_title=None,
                lead_company=None,
                linkedin_url=f"https://www.linkedin.com/messaging/thread/{conversation.heyreach_lead_id}",
                lead_message=conversation.conversation_history[-1].get("content", "") if conversation.conversation_history else "",
                ai_draft=new_draft,
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
            if draft.slack_message_ts:
                slack_bot = get_slack_bot()
                await slack_bot.remove_buttons(
                    message_ts=draft.slack_message_ts,
                    final_text="Edited message sent successfully!"
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
    # Verify signature
    body = await verify_slack_signature(request)

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
        draft_id_str = action.get("value", "")
        message_ts = payload.get("message", {}).get("ts", "")
        trigger_id = payload.get("trigger_id", "")

        try:
            draft_id = uuid.UUID(draft_id_str)
        except ValueError:
            logger.error(f"Invalid draft_id: {draft_id_str}")
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
        else:
            logger.warning(f"Unknown action_id: {action_id}")

    elif payload_type == "view_submission":
        # Handle modal submissions
        view = payload.get("view", {})
        callback_id = view.get("callback_id", "")
        private_metadata = view.get("private_metadata", "")

        # Extract draft_id from callback_id (format: edit_draft_{uuid})
        if callback_id.startswith("edit_draft_"):
            try:
                draft_id = uuid.UUID(private_metadata)
            except ValueError:
                logger.error(f"Invalid draft_id in metadata: {private_metadata}")
                return {"ok": True}

            # Extract edited text from view values
            values = view.get("state", {}).get("values", {})
            edited_text = (
                values.get("draft_input", {})
                .get("draft_text", {})
                .get("value", "")
            )

            if edited_text:
                await handle_modal_submit(draft_id, edited_text, background_tasks)

    return {"ok": True}
