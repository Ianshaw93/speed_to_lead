"""FastAPI application entry point."""

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# Configure logging early
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

logger.info("Starting app import...")

try:
    from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
    from sqlalchemy import select
    logger.info("FastAPI and SQLAlchemy imported")

    from app.config import settings
    logger.info(f"Settings loaded, database_url starts with: {settings.database_url[:20]}...")

    from app.database import async_session_factory
    logger.info("Database session factory created")

    from app.models import Conversation, Draft, DraftStatus, FunnelStage, MessageDirection, MessageLog, Prospect, ProspectSource
    logger.info("Models imported")

    from app.schemas import HeyReachWebhookPayload, HealthResponse
    logger.info("Schemas imported")

    from app.services.deepseek import generate_reply_draft
    from app.services.slack import SlackBot
    logger.info("Services imported")

    from app.routers.slack import router as slack_router
    from app.routers.metrics import router as metrics_router
    from app.routers.engagement import router as engagement_router
    from app.routers.changelog import router as changelog_router
    logger.info("Routers imported")
except Exception as e:
    logger.error(f"Import failed: {e}", exc_info=True)
    raise

logger.info("All imports successful")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    from app.services.scheduler import get_scheduler_service
    scheduler = get_scheduler_service()
    scheduler.start()
    logger.info("Scheduler started with daily/weekly report jobs")

    yield

    # Shutdown - cleanup resources
    scheduler.shutdown(wait=False)
    logger.info("Scheduler shut down")

    from app.services.heyreach import _client as heyreach_client
    if heyreach_client:
        await heyreach_client.close()


app = FastAPI(
    title="Speed to Lead",
    description="HeyReach webhook handler with AI-powered LinkedIn reply suggestions",
    version="0.1.0",
    lifespan=lifespan,
)

# Include routers
app.include_router(slack_router)
app.include_router(metrics_router)
app.include_router(engagement_router)
app.include_router(changelog_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests for debugging."""
    logger.info(f"Request: {request.method} {request.url.path}")
    logger.info(f"Headers: {dict(request.headers)}")
    response = await call_next(request)
    logger.info(f"Response status: {response.status_code}")
    return response


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint for Railway."""
    return HealthResponse(
        status="healthy",
        environment=settings.environment,
    )


@app.get("/version")
async def version() -> dict:
    """Return version info for debugging deployment issues."""
    return {
        "version": "2026-02-04-v1",
        "sender_id_type": "int|str",
        "migrations_retry": True,
    }


@app.post("/admin/run-migrations")
async def run_migrations(request: Request) -> dict:
    """Run database migrations.

    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"

    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")

        return {"status": "ok", "message": "Migrations completed successfully"}
    except Exception as e:
        logger.error(f"Migration error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def process_incoming_message(payload: HeyReachWebhookPayload) -> dict:
    """Process an incoming message from HeyReach webhook.

    This function orchestrates:
    1. Upserting conversation record
    2. Logging the inbound message
    3. Generating AI draft via DeepSeek
    4. Sending draft to Slack for approval
    5. Storing draft with pending status

    Args:
        payload: The webhook payload from HeyReach.

    Returns:
        Dict with draft_id if successful.
    """
    print("=== PROCESSING MESSAGE IN BACKGROUND ===", flush=True)
    try:
        async with async_session_factory() as session:
            print("Database session opened", flush=True)
            # 1. Upsert conversation record
            result = await session.execute(
                select(Conversation).where(
                    Conversation.heyreach_lead_id == payload.conversation_id
                )
            )
            conversation = result.scalar_one_or_none()

            # Build conversation history from recent messages
            history = [
                {"role": "lead", "content": msg.message, "time": msg.creation_time}
                for msg in payload.all_recent_messages
            ]

            # Get real LinkedIn profile URL from payload
            profile_url = payload.linkedin_profile_url or f"linkedin://conversation/{payload.conversation_id}"

            if conversation:
                # Update existing conversation
                conversation.conversation_history = history
                conversation.linkedin_account_id = payload.linkedin_account_id
                # Update profile URL if we now have the real one
                if payload.linkedin_profile_url and "linkedin://conversation/" in (conversation.linkedin_profile_url or ""):
                    conversation.linkedin_profile_url = payload.linkedin_profile_url
                logger.info(f"Updated conversation {conversation.id}")
            else:
                # Create new conversation
                conversation = Conversation(
                    heyreach_lead_id=payload.conversation_id,
                    linkedin_profile_url=profile_url,
                    lead_name=payload.lead_name,
                    linkedin_account_id=payload.linkedin_account_id,
                    conversation_history=history,
                )
                session.add(conversation)
                await session.flush()  # Get the ID
                logger.info(f"Created conversation {conversation.id}")

            # 2. Detect if this is the first reply (before logging this message)
            from sqlalchemy import func
            inbound_count_result = await session.execute(
                select(func.count(MessageLog.id)).where(
                    MessageLog.conversation_id == conversation.id,
                    MessageLog.direction == MessageDirection.INBOUND,
                )
            )
            inbound_count = inbound_count_result.scalar()
            is_first_reply = inbound_count == 0
            logger.info(f"First reply detection: inbound_count={inbound_count}, is_first_reply={is_first_reply}")

            # 2.5. Log the inbound message
            message_log = MessageLog(
                conversation_id=conversation.id,
                direction=MessageDirection.INBOUND,
                content=payload.latest_message,
            )
            session.add(message_log)

            # 2.6. Check if prospect should be removed from follow-up list
            # (if they replied within 24h of being added)
            lead_profile_url_for_followup = payload.lead.profile_url if payload.lead else None
            if lead_profile_url_for_followup:
                removed = await check_and_remove_from_followup(session, lead_profile_url_for_followup)
                if removed:
                    logger.info(f"Removed {lead_profile_url_for_followup} from follow-up list (replied within 24h)")

            # 3. Generate AI draft via DeepSeek (with stage detection)
            print(f"Generating AI draft for conversation {conversation.id}", flush=True)
            logger.info(f"Generating AI draft for conversation {conversation.id}")
            draft_result = await generate_reply_draft(
                lead_name=payload.lead_name,
                lead_message=payload.latest_message,
                conversation_history=history,
            )
            print(f"Detected stage: {draft_result.detected_stage.value}", flush=True)
            print(f"Generated draft: {draft_result.reply[:100]}...", flush=True)
            logger.info(f"Detected stage: {draft_result.detected_stage.value}")
            logger.info(f"Generated draft: {draft_result.reply[:100]}...")

            # Update conversation with detected funnel stage
            conversation.funnel_stage = draft_result.detected_stage

            # 4. Pre-generate draft ID so Slack buttons have correct ID
            draft_id = uuid.uuid4()

            # 5. Send draft to Slack for approval (with stage indicator)
            print("Sending to Slack...", flush=True)
            slack_bot = SlackBot()
            slack_ts = await slack_bot.send_draft_notification(
                draft_id=draft_id,
                lead_name=payload.lead_name,
                lead_title=None,  # Not in payload
                lead_company=payload.lead_company,
                linkedin_url=f"https://www.linkedin.com/messaging/thread/{payload.conversation_id}",
                lead_message=payload.latest_message,
                ai_draft=draft_result.reply,
                funnel_stage=draft_result.detected_stage,
                stage_reasoning=draft_result.stage_reasoning,
                is_first_reply=is_first_reply,
            )
            print(f"Slack notification sent, ts: {slack_ts}", flush=True)
            logger.info(f"Sent Slack notification, ts: {slack_ts}")

            # 6. Store draft with the same ID used in Slack buttons
            draft = Draft(
                id=draft_id,
                conversation_id=conversation.id,
                status=DraftStatus.PENDING,
                ai_draft=draft_result.reply,
                slack_message_ts=slack_ts,
                is_first_reply=is_first_reply,
            )
            session.add(draft)

            # 7. Link prospect to conversation (if exists)
            # Try to find by lead's LinkedIn URL from payload
            lead_profile_url = payload.lead.profile_url if payload.lead else None
            if lead_profile_url:
                normalized_url = lead_profile_url.lower().strip().rstrip("/")
                if "?" in normalized_url:
                    normalized_url = normalized_url.split("?")[0]

                prospect_result = await session.execute(
                    select(Prospect).where(Prospect.linkedin_url == normalized_url)
                )
                prospect = prospect_result.scalar_one_or_none()

                if prospect and not prospect.conversation_id:
                    prospect.conversation_id = conversation.id
                    logger.info(f"Linked prospect {prospect.id} to conversation {conversation.id}")

            await session.commit()

            logger.info(f"Created draft {draft.id} for conversation {conversation.id}")
            return {"draft_id": str(draft.id)}

    except Exception as e:
        print(f"!!! ERROR processing message: {e}", flush=True)
        logger.error(f"Error processing message: {e}", exc_info=True)
        return {"error": str(e)}


@app.get("/webhook/heyreach")
async def heyreach_webhook_verify() -> dict:
    """Handle GET requests for webhook verification."""
    logger.info("GET request to /webhook/heyreach - verification check")
    return {"status": "ok", "message": "Webhook endpoint active"}


@app.post("/webhook/heyreach")
async def heyreach_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Receive webhook from HeyReach when a reply is received.

    This endpoint:
    1. Validates the incoming payload
    2. Triggers background processing (AI draft, Slack notification)
    3. Returns immediately to acknowledge receipt

    Args:
        request: The incoming request.
        background_tasks: FastAPI background tasks handler.

    Returns:
        Acknowledgment response.
    """
    # Log raw body for debugging - use print with flush to ensure it appears
    print("=== WEBHOOK RECEIVED ===", flush=True)
    body = await request.body()
    print(f"Body length: {len(body)}", flush=True)
    logger.info(f"Raw webhook body: {body.decode('utf-8', errors='replace')}")

    # Parse the JSON body
    try:
        import json
        data = json.loads(body)
        logger.info(f"Parsed webhook data: {data}")
    except Exception as e:
        logger.error(f"Failed to parse webhook body: {e}")
        return {"status": "error", "message": "Invalid JSON"}

    # Try to parse with our schema
    try:
        payload = HeyReachWebhookPayload(**data)
        background_tasks.add_task(process_incoming_message, payload)
        return {
            "status": "received",
            "conversation_id": payload.conversation_id,
            "lead_name": payload.lead_name,
        }
    except Exception as e:
        logger.error(f"Schema validation failed: {e}")
        # Return success anyway to acknowledge receipt, log for debugging
        return {
            "status": "received_raw",
            "message": "Payload logged for analysis",
            "keys": list(data.keys()) if isinstance(data, dict) else "not a dict",
        }


async def process_outgoing_message(payload: HeyReachWebhookPayload) -> dict:
    """Process an outgoing message from HeyReach webhook.

    This function:
    1. Upserts conversation record
    2. Deduplicates against existing outbound messages (same content + sent_at within 5 min)
    3. Creates MessageLog with OUTBOUND direction and campaign info
    4. No AI draft, no Slack notification

    Args:
        payload: The webhook payload from HeyReach.

    Returns:
        Dict with message_log_id if successful.
    """
    print("=== PROCESSING OUTGOING MESSAGE IN BACKGROUND ===", flush=True)
    try:
        async with async_session_factory() as session:
            from datetime import datetime, timedelta, timezone
            from sqlalchemy import and_

            # 1. Upsert conversation record
            result = await session.execute(
                select(Conversation).where(
                    Conversation.heyreach_lead_id == payload.conversation_id
                )
            )
            conversation = result.scalar_one_or_none()

            # Build conversation history from recent messages
            history = [
                {"role": "lead", "content": msg.message, "time": msg.creation_time}
                for msg in payload.all_recent_messages
            ]

            profile_url = payload.linkedin_profile_url or f"linkedin://conversation/{payload.conversation_id}"

            if conversation:
                conversation.conversation_history = history
                conversation.linkedin_account_id = payload.linkedin_account_id
                if payload.linkedin_profile_url and "linkedin://conversation/" in (conversation.linkedin_profile_url or ""):
                    conversation.linkedin_profile_url = payload.linkedin_profile_url
                logger.info(f"Updated conversation {conversation.id} (outgoing)")
            else:
                conversation = Conversation(
                    heyreach_lead_id=payload.conversation_id,
                    linkedin_profile_url=profile_url,
                    lead_name=payload.lead_name,
                    linkedin_account_id=payload.linkedin_account_id,
                    conversation_history=history,
                )
                session.add(conversation)
                await session.flush()
                logger.info(f"Created conversation {conversation.id} (outgoing)")

            # 2. Log ALL messages from recent_messages (deduped)
            # This captures the full conversation as a safety net
            campaign_id = payload.campaign.id if payload.campaign else None
            campaign_name = payload.campaign.name if payload.campaign else None
            now = datetime.now(timezone.utc)
            dedup_window = timedelta(minutes=5)

            created = 0
            deduped = 0

            for msg in payload.all_recent_messages:
                if not msg.message:
                    continue

                # is_reply=True means lead sent it (INBOUND), False means we sent it (OUTBOUND)
                direction = MessageDirection.INBOUND if msg.is_reply else MessageDirection.OUTBOUND

                # Dedup: check for existing message with same content + direction + conversation
                existing_result = await session.execute(
                    select(MessageLog).where(
                        and_(
                            MessageLog.conversation_id == conversation.id,
                            MessageLog.direction == direction,
                            MessageLog.content == msg.message,
                            MessageLog.sent_at >= now - dedup_window,
                        )
                    )
                )
                existing_msg = existing_result.scalar_one_or_none()

                if existing_msg:
                    # Enrich outbound messages with campaign info if missing
                    if direction == MessageDirection.OUTBOUND:
                        if campaign_id and not existing_msg.campaign_id:
                            existing_msg.campaign_id = campaign_id
                        if campaign_name and not existing_msg.campaign_name:
                            existing_msg.campaign_name = campaign_name
                    deduped += 1
                    continue

                # Create new MessageLog
                message_log = MessageLog(
                    conversation_id=conversation.id,
                    direction=direction,
                    content=msg.message,
                    campaign_id=campaign_id if direction == MessageDirection.OUTBOUND else None,
                    campaign_name=campaign_name if direction == MessageDirection.OUTBOUND else None,
                )
                session.add(message_log)
                created += 1

            await session.commit()

            logger.info(
                f"Outgoing webhook for conversation {conversation.id}: "
                f"created={created}, deduped={deduped}"
            )
            return {"status": "logged", "created": created, "deduped": deduped}

    except Exception as e:
        print(f"!!! ERROR processing outgoing message: {e}", flush=True)
        logger.error(f"Error processing outgoing message: {e}", exc_info=True)
        return {"error": str(e)}


@app.get("/webhook/heyreach/outgoing")
async def heyreach_outgoing_webhook_verify() -> dict:
    """Handle GET requests for outgoing webhook verification."""
    logger.info("GET request to /webhook/heyreach/outgoing - verification check")
    return {"status": "ok", "message": "Outgoing webhook endpoint active"}


@app.post("/webhook/heyreach/outgoing")
async def heyreach_outgoing_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Receive webhook from HeyReach when an outgoing message is sent.

    This endpoint captures campaign-sent messages (initial outreach, automated follow-ups)
    to provide full conversation history and accurate metrics.

    Args:
        request: The incoming request.
        background_tasks: FastAPI background tasks handler.

    Returns:
        Acknowledgment response.
    """
    print("=== OUTGOING WEBHOOK RECEIVED ===", flush=True)
    body = await request.body()
    print(f"Body length: {len(body)}", flush=True)
    logger.info(f"Raw outgoing webhook body: {body.decode('utf-8', errors='replace')}")

    try:
        import json
        data = json.loads(body)
        logger.info(f"Parsed outgoing webhook data: {data}")
    except Exception as e:
        logger.error(f"Failed to parse outgoing webhook body: {e}")
        return {"status": "error", "message": "Invalid JSON"}

    try:
        payload = HeyReachWebhookPayload(**data)
        background_tasks.add_task(process_outgoing_message, payload)
        return {
            "status": "received",
            "conversation_id": payload.conversation_id,
            "lead_name": payload.lead_name,
            "direction": "outgoing",
        }
    except Exception as e:
        logger.error(f"Outgoing webhook schema validation failed: {e}")
        return {
            "status": "received_raw",
            "message": "Payload logged for analysis",
            "keys": list(data.keys()) if isinstance(data, dict) else "not a dict",
        }


# =============================================================================
# CONNECTION TRACKING WEBHOOKS
# =============================================================================


async def process_connection_sent(data: dict) -> dict:
    """Process a connection request sent event.

    Sets connection_sent_at on the matching Prospect.
    """
    print("=== PROCESSING CONNECTION SENT IN BACKGROUND ===", flush=True)
    try:
        async with async_session_factory() as session:
            from datetime import datetime, timezone

            # Try to extract LinkedIn URL from payload
            linkedin_url = _extract_linkedin_url(data)
            if not linkedin_url:
                logger.warning(f"Connection sent webhook: no LinkedIn URL found in payload")
                return {"status": "no_url"}

            normalized = normalize_linkedin_url(linkedin_url)
            result = await session.execute(
                select(Prospect).where(Prospect.linkedin_url == normalized)
            )
            prospect = result.scalar_one_or_none()

            if not prospect:
                logger.warning(f"Connection sent: prospect not found for {normalized}")
                return {"status": "not_found", "url": normalized}

            # Only set if not already set (dedup)
            if not prospect.connection_sent_at:
                prospect.connection_sent_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info(f"Connection sent recorded for {prospect.full_name} ({normalized})")
            else:
                logger.info(f"Connection sent already recorded for {normalized}, skipping")

            return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error processing connection sent: {e}", exc_info=True)
        return {"error": str(e)}


async def process_connection_accepted(data: dict) -> dict:
    """Process a connection accepted event.

    Sets connection_accepted_at on the matching Prospect.
    """
    print("=== PROCESSING CONNECTION ACCEPTED IN BACKGROUND ===", flush=True)
    try:
        async with async_session_factory() as session:
            from datetime import datetime, timezone

            linkedin_url = _extract_linkedin_url(data)
            if not linkedin_url:
                logger.warning(f"Connection accepted webhook: no LinkedIn URL found in payload")
                return {"status": "no_url"}

            normalized = normalize_linkedin_url(linkedin_url)
            result = await session.execute(
                select(Prospect).where(Prospect.linkedin_url == normalized)
            )
            prospect = result.scalar_one_or_none()

            if not prospect:
                logger.warning(f"Connection accepted: prospect not found for {normalized}")
                return {"status": "not_found", "url": normalized}

            # Only set if not already set (dedup)
            if not prospect.connection_accepted_at:
                prospect.connection_accepted_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info(f"Connection accepted recorded for {prospect.full_name} ({normalized})")
            else:
                logger.info(f"Connection accepted already recorded for {normalized}, skipping")

            return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error processing connection accepted: {e}", exc_info=True)
        return {"error": str(e)}


def _extract_linkedin_url(data: dict) -> str | None:
    """Extract LinkedIn profile URL from webhook payload.

    Tries multiple known payload shapes since we haven't confirmed
    the exact HeyReach connection event format yet.
    """
    # Try HeyReachWebhookPayload shape: data.lead.profile_url
    lead = data.get("lead", {})
    if isinstance(lead, dict):
        url = lead.get("profile_url") or lead.get("profileUrl")
        if url:
            return url

    # Try flat shape
    url = data.get("linkedin_profile_url") or data.get("profileUrl") or data.get("profile_url")
    if url:
        return url

    return None


@app.get("/webhook/heyreach/connection-sent")
async def heyreach_connection_sent_verify() -> dict:
    """Handle GET requests for connection-sent webhook verification."""
    logger.info("GET request to /webhook/heyreach/connection-sent - verification check")
    return {"status": "ok", "message": "Connection sent webhook endpoint active"}


@app.post("/webhook/heyreach/connection-sent")
async def heyreach_connection_sent_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Receive webhook from HeyReach when a connection request is sent."""
    print("=== CONNECTION SENT WEBHOOK RECEIVED ===", flush=True)
    body = await request.body()
    logger.info(f"Raw connection-sent webhook body: {body.decode('utf-8', errors='replace')}")

    try:
        import json
        data = json.loads(body)
        logger.info(f"Parsed connection-sent data keys: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")
    except Exception as e:
        logger.error(f"Failed to parse connection-sent webhook body: {e}")
        return {"status": "error", "message": "Invalid JSON"}

    background_tasks.add_task(process_connection_sent, data)
    return {"status": "received", "event": "connection_sent"}


@app.get("/webhook/heyreach/connection-accepted")
async def heyreach_connection_accepted_verify() -> dict:
    """Handle GET requests for connection-accepted webhook verification."""
    logger.info("GET request to /webhook/heyreach/connection-accepted - verification check")
    return {"status": "ok", "message": "Connection accepted webhook endpoint active"}


@app.post("/webhook/heyreach/connection-accepted")
async def heyreach_connection_accepted_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Receive webhook from HeyReach when a connection request is accepted."""
    print("=== CONNECTION ACCEPTED WEBHOOK RECEIVED ===", flush=True)
    body = await request.body()
    logger.info(f"Raw connection-accepted webhook body: {body.decode('utf-8', errors='replace')}")

    try:
        import json
        data = json.loads(body)
        logger.info(f"Parsed connection-accepted data keys: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")
    except Exception as e:
        logger.error(f"Failed to parse connection-accepted webhook body: {e}")
        return {"status": "error", "message": "Invalid JSON"}

    background_tasks.add_task(process_connection_accepted, data)
    return {"status": "received", "event": "connection_accepted"}


# =============================================================================
# PROSPECTS API - For tracking all outreach prospects
# =============================================================================

def normalize_linkedin_url(url: str) -> str:
    """Normalize LinkedIn URL for consistent matching."""
    if not url:
        return ""
    url = url.lower().strip().rstrip("/")
    # Remove query params
    if "?" in url:
        url = url.split("?")[0]
    return url


async def check_and_remove_from_followup(session, linkedin_url: str) -> bool:
    """Check if prospect should be removed from follow-up list and remove if so.

    A prospect should be removed if:
    1. They exist in the database
    2. They were added to a follow-up list
    3. They were added within the last 24 hours

    Args:
        session: Database session.
        linkedin_url: The prospect's LinkedIn profile URL.

    Returns:
        True if prospect was removed from follow-up list, False otherwise.
    """
    from datetime import datetime, timedelta, timezone
    from app.services.heyreach import get_heyreach_client, HeyReachError

    normalized_url = normalize_linkedin_url(linkedin_url)
    if not normalized_url:
        return False

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    # Find prospect who was added to follow-up list within last 24 hours
    result = await session.execute(
        select(Prospect).where(
            Prospect.linkedin_url == normalized_url,
            Prospect.followup_list_id.isnot(None),
            Prospect.added_to_followup_at > cutoff,
        )
    )
    prospect = result.scalar_one_or_none()

    if not prospect:
        return False

    # Prospect replied within 24h of being added to follow-up list
    # Remove them from the HeyReach list
    logger.info(
        f"Prospect {prospect.full_name} ({normalized_url}) replied within 24h of "
        f"being added to follow-up list {prospect.followup_list_id}. Removing..."
    )

    try:
        heyreach = get_heyreach_client()
        await heyreach.remove_lead_from_list(
            list_id=prospect.followup_list_id,
            linkedin_url=normalized_url,
        )

        # Clear the follow-up tracking fields
        prospect.followup_list_id = None
        prospect.added_to_followup_at = None
        await session.commit()

        logger.info(f"Successfully removed {normalized_url} from follow-up list")
        return True

    except HeyReachError as e:
        logger.error(f"Failed to remove prospect from follow-up list: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error removing prospect from follow-up list: {e}", exc_info=True)
        return False


@app.post("/api/prospects")
async def register_prospects(request: Request) -> dict:
    """Register prospects sent to HeyReach.

    Called by multichannel-outreach after uploading to HeyReach.
    Accepts a list of prospects with their metadata.
    """
    try:
        data = await request.json()
        prospects_data = data.get("prospects", [])
        source_type = data.get("source_type", "other")
        source_keyword = data.get("source_keyword")
        heyreach_list_id = data.get("heyreach_list_id")

        if not prospects_data:
            return {"status": "error", "message": "No prospects provided"}

        async with async_session_factory() as session:
            created = 0
            updated = 0
            errors = []

            for p in prospects_data:
                linkedin_url = normalize_linkedin_url(
                    p.get("linkedinUrl") or p.get("linkedin_url") or p.get("profileUrl") or ""
                )

                if not linkedin_url:
                    errors.append(f"Missing LinkedIn URL for {p.get('fullName', 'Unknown')}")
                    continue

                # Check if prospect exists
                result = await session.execute(
                    select(Prospect).where(Prospect.linkedin_url == linkedin_url)
                )
                existing = result.scalar_one_or_none()

                if existing:
                    # Update with new outreach data
                    existing.personalized_message = p.get("personalized_message") or existing.personalized_message
                    existing.heyreach_list_id = heyreach_list_id or existing.heyreach_list_id
                    if heyreach_list_id:
                        from datetime import datetime, timezone
                        existing.heyreach_uploaded_at = datetime.now(timezone.utc)
                    updated += 1
                else:
                    # Create new prospect
                    from datetime import datetime, timezone
                    # Parse dates if provided
                    post_date = None
                    scraped_at = None
                    if p.get("post_date"):
                        try:
                            post_date = datetime.fromisoformat(p["post_date"].replace("Z", "+00:00"))
                        except:
                            pass
                    if p.get("scraped_at"):
                        try:
                            scraped_at = datetime.fromisoformat(p["scraped_at"].replace("Z", "+00:00"))
                        except:
                            pass

                    prospect = Prospect(
                        linkedin_url=linkedin_url,
                        full_name=p.get("fullName") or p.get("full_name"),
                        first_name=p.get("firstName") or p.get("first_name"),
                        last_name=p.get("lastName") or p.get("last_name"),
                        job_title=p.get("jobTitle") or p.get("job_title") or p.get("position"),
                        company_name=p.get("companyName") or p.get("company_name") or p.get("company"),
                        company_industry=p.get("companyIndustry") or p.get("company_industry"),
                        location=p.get("addressWithCountry") or p.get("location"),
                        headline=p.get("headline"),
                        email=p.get("email") or p.get("emailAddress"),
                        source_type=ProspectSource(source_type) if source_type in [e.value for e in ProspectSource] else ProspectSource.OTHER,
                        source_keyword=source_keyword or p.get("source_keyword"),
                        source_post_url=p.get("source_post_url"),
                        engagement_type=p.get("engagement_type"),
                        post_date=post_date,
                        scraped_at=scraped_at or datetime.now(timezone.utc),
                        personalized_message=p.get("personalized_message"),
                        icp_match=p.get("icp_match"),
                        icp_reason=p.get("icp_reason"),
                        heyreach_list_id=heyreach_list_id,
                        heyreach_uploaded_at=datetime.now(timezone.utc) if heyreach_list_id else None,
                    )
                    session.add(prospect)
                    created += 1

            await session.commit()

            return {
                "status": "ok",
                "created": created,
                "updated": updated,
                "errors": errors[:10] if errors else [],
            }

    except Exception as e:
        logger.error(f"Error registering prospects: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/prospects/backfill")
async def backfill_prospects(request: Request) -> dict:
    """Backfill prospects from JSON data.

    Accepts a list of prospects with full metadata for bulk import.
    Used by multichannel-outreach to sync historical data.
    """
    try:
        data = await request.json()
        prospects_data = data.get("prospects", [])

        if not prospects_data:
            return {"status": "error", "message": "No prospects provided"}

        async with async_session_factory() as session:
            created = 0
            skipped = 0

            for p in prospects_data:
                linkedin_url = normalize_linkedin_url(
                    p.get("linkedin_url") or p.get("linkedinUrl") or p.get("profileUrl") or ""
                )

                if not linkedin_url:
                    continue

                # Check if exists
                result = await session.execute(
                    select(Prospect).where(Prospect.linkedin_url == linkedin_url)
                )
                if result.scalar_one_or_none():
                    skipped += 1
                    continue

                source_type_str = p.get("source_type", "other")
                try:
                    source_type = ProspectSource(source_type_str)
                except ValueError:
                    source_type = ProspectSource.OTHER

                from datetime import datetime, timezone
                prospect = Prospect(
                    linkedin_url=linkedin_url,
                    full_name=p.get("full_name") or p.get("fullName"),
                    first_name=p.get("first_name") or p.get("firstName"),
                    last_name=p.get("last_name") or p.get("lastName"),
                    job_title=p.get("job_title") or p.get("jobTitle"),
                    company_name=p.get("company_name") or p.get("companyName"),
                    company_industry=p.get("company_industry") or p.get("companyIndustry"),
                    location=p.get("location") or p.get("addressWithCountry"),
                    headline=p.get("headline"),
                    email=p.get("email") or p.get("emailAddress"),
                    source_type=source_type,
                    source_keyword=p.get("source_keyword"),
                    source_post_url=p.get("source_post_url"),
                    personalized_message=p.get("personalized_message"),
                    icp_match=p.get("icp_match"),
                    icp_reason=p.get("icp_reason"),
                    heyreach_list_id=p.get("heyreach_list_id"),
                    heyreach_uploaded_at=datetime.now(timezone.utc) if p.get("heyreach_list_id") else None,
                )
                session.add(prospect)
                created += 1

            await session.commit()

            # Link to conversations
            linked = 0
            convos_result = await session.execute(select(Conversation))
            conversations = convos_result.scalars().all()

            for convo in conversations:
                convo_url = normalize_linkedin_url(convo.linkedin_profile_url or "")
                if not convo_url or "linkedin://conversation/" in convo_url:
                    continue

                result = await session.execute(
                    select(Prospect).where(
                        Prospect.linkedin_url == convo_url,
                        Prospect.conversation_id.is_(None)
                    )
                )
                prospect = result.scalar_one_or_none()
                if prospect:
                    prospect.conversation_id = convo.id
                    linked += 1

            await session.commit()

            return {
                "status": "ok",
                "created": created,
                "skipped": skipped,
                "linked_to_conversations": linked,
            }

    except Exception as e:
        logger.error(f"Error in backfill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prospects/stats")
async def prospects_stats() -> dict:
    """Get prospect statistics."""
    from sqlalchemy import func

    async with async_session_factory() as session:
        # Total prospects
        total = await session.execute(select(func.count(Prospect.id)))
        total_count = total.scalar()

        # By source
        by_source = await session.execute(
            select(Prospect.source_type, func.count(Prospect.id))
            .group_by(Prospect.source_type)
        )
        source_counts = {str(row[0].value): row[1] for row in by_source}

        # With conversations (replied)
        with_convo = await session.execute(
            select(func.count(Prospect.id)).where(Prospect.conversation_id.isnot(None))
        )
        replied_count = with_convo.scalar()

        return {
            "total": total_count,
            "by_source": source_counts,
            "replied": replied_count,
            "reply_rate": f"{(replied_count / total_count * 100):.1f}%" if total_count > 0 else "0%",
        }


@app.get("/api/prospects/lookup")
async def lookup_prospect(
    email: str | None = None,
    name: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict:
    """Look up a prospect by email or name.

    Used to find prospects from Calendly bookings etc.
    Returns linkedin_url if found.

    Query params:
        email: Email address to search
        name: Full name to search (fuzzy match)
        first_name: First name to search
        last_name: Last name to search

    Returns:
        List of matching prospects with linkedin_url
    """
    from sqlalchemy import or_, func

    if not any([email, name, first_name, last_name]):
        raise HTTPException(
            status_code=400,
            detail="Must provide at least one of: email, name, first_name, last_name"
        )

    async with async_session_factory() as session:
        conditions = []

        # Email match (exact, case-insensitive)
        if email:
            conditions.append(func.lower(Prospect.email) == email.lower().strip())

        # Name matching
        if name:
            # Split name and try to match
            name_parts = name.strip().split()
            if len(name_parts) >= 2:
                # Try first + last name combo
                conditions.append(
                    (func.lower(Prospect.first_name) == name_parts[0].lower()) &
                    (func.lower(Prospect.last_name) == name_parts[-1].lower())
                )
            # Also try full_name contains
            conditions.append(func.lower(Prospect.full_name).contains(name.lower()))

        if first_name and last_name:
            conditions.append(
                (func.lower(Prospect.first_name) == first_name.lower().strip()) &
                (func.lower(Prospect.last_name) == last_name.lower().strip())
            )
        elif first_name:
            conditions.append(func.lower(Prospect.first_name) == first_name.lower().strip())
        elif last_name:
            conditions.append(func.lower(Prospect.last_name) == last_name.lower().strip())

        if not conditions:
            return {"matches": [], "count": 0}

        result = await session.execute(
            select(Prospect).where(or_(*conditions)).limit(10)
        )
        prospects = result.scalars().all()

        matches = []
        for p in prospects:
            matches.append({
                "linkedin_url": p.linkedin_url,
                "full_name": p.full_name,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "email": p.email,
                "company_name": p.company_name,
                "job_title": p.job_title,
            })

        return {
            "matches": matches,
            "count": len(matches),
        }


@app.get("/api/prospects/missing-linkedin")
async def get_prospects_missing_linkedin() -> dict:
    """Get prospects with missing or empty LinkedIn URLs."""
    from sqlalchemy import or_

    async with async_session_factory() as session:
        result = await session.execute(
            select(Prospect).where(
                or_(
                    Prospect.linkedin_url.is_(None),
                    Prospect.linkedin_url == "",
                )
            )
        )
        prospects = result.scalars().all()

        return {
            "count": len(prospects),
            "prospects": [
                {
                    "id": p.id,
                    "full_name": p.full_name,
                    "first_name": p.first_name,
                    "last_name": p.last_name,
                    "email": p.email,
                    "company_name": p.company_name,
                    "job_title": p.job_title,
                    "source_type": p.source_type.value if p.source_type else None,
                }
                for p in prospects
            ],
        }


@app.patch("/api/prospects/{prospect_id}")
async def update_prospect(prospect_id: int, request: Request) -> dict:
    """Update a prospect's fields."""
    data = await request.json()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Prospect).where(Prospect.id == prospect_id)
        )
        prospect = result.scalar_one_or_none()

        if not prospect:
            raise HTTPException(status_code=404, detail="Prospect not found")

        # Update allowed fields
        if "linkedin_url" in data:
            prospect.linkedin_url = normalize_linkedin_url(data["linkedin_url"])
        if "email" in data:
            prospect.email = data["email"]
        if "full_name" in data:
            prospect.full_name = data["full_name"]
        if "first_name" in data:
            prospect.first_name = data["first_name"]
        if "last_name" in data:
            prospect.last_name = data["last_name"]

        await session.commit()

        return {"status": "ok", "id": prospect_id}


# =============================================================================
# BUYING SIGNAL WEBHOOK
# =============================================================================

BUYING_SIGNAL_LOG = os.path.join(".tmp", "buying_signal_payloads.jsonl")


@app.post("/buying-signal")
async def receive_buying_signal(request: Request) -> dict:
    """Receives prospect payloads from buying signal agent and persists to DB.

    Also logs raw payload to .tmp/buying_signal_payloads.jsonl for debugging.
    """
    import json as _json
    data = await request.json()
    received_at = datetime.now(timezone.utc).isoformat()

    # Log raw payload for debugging
    entry = {"received_at": received_at, "payload": data}
    os.makedirs(".tmp", exist_ok=True)
    with open(BUYING_SIGNAL_LOG, "a") as f:
        f.write(_json.dumps(entry) + "\n")

    if not isinstance(data, dict):
        logger.warning("Buying signal payload is not a dict, skipping DB insert")
        return {"status": "received", "received_at": received_at, "persisted": False}

    # Build linkedin_url from vanity identifier
    li_identifier = data.get("linkedinIdentifier", "")
    if not li_identifier:
        logger.warning("No linkedinIdentifier in buying signal, skipping DB insert")
        return {"status": "received", "received_at": received_at, "persisted": False}

    linkedin_url = normalize_linkedin_url(f"https://linkedin.com/in/{li_identifier}")

    async with async_session_factory() as session:
        # Skip if already exists
        existing = await session.execute(
            select(Prospect).where(Prospect.linkedin_url == linkedin_url)
        )
        if existing.scalar_one_or_none():
            logger.info(f"Buying signal duplicate skipped: {linkedin_url}")
            return {"status": "duplicate", "received_at": received_at, "linkedin_url": linkedin_url}

        prospect = Prospect(
            linkedin_url=linkedin_url,
            full_name=data.get("fullName"),
            first_name=data.get("firstName"),
            last_name=data.get("lastName"),
            job_title=data.get("jobTitle"),
            company_name=data.get("company"),
            company_industry=data.get("industry"),
            location=data.get("location"),
            headline=data.get("profileBaseline"),
            email=data.get("email"),
            source_type=ProspectSource.BUYING_SIGNAL,
            source_keyword=data.get("intent_keyword"),
            icp_match=True,
            icp_reason=data.get("score_reasoning"),
        )
        session.add(prospect)
        await session.commit()

    logger.info(f"Buying signal persisted: {linkedin_url}")
    return {"status": "created", "received_at": received_at, "linkedin_url": linkedin_url}


@app.post("/buying-signal/process")
async def trigger_buying_signal_outreach(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Manually trigger buying signal outreach batch processing.

    Same logic as the scheduled 7am EST job. Runs in background.
    """
    from app.services.buying_signal_outreach import process_buying_signal_batch

    async def _run():
        try:
            result = await process_buying_signal_batch()
            logger.info(f"Manual buying signal batch result: {result}")

            # Send Slack summary
            from app.services.slack import get_slack_bot
            bot = get_slack_bot()
            summary = (
                f"*Buying Signal Outreach Batch Complete (manual trigger)*\n"
                f"- Prospects processed: {result['processed']}\n"
                f"- Messages generated: {result['messages_generated']}\n"
                f"- Uploaded to HeyReach: {result['uploaded']}\n"
                f"- Errors: {result['errors']}"
            )
            await bot.send_confirmation(summary)
        except Exception as e:
            logger.error(f"Manual buying signal batch failed: {e}", exc_info=True)

    background_tasks.add_task(_run)
    return {"status": "processing", "message": "Buying signal batch triggered"}


@app.get("/admin/conversation/{lead_name}")
async def get_conversation_detail(lead_name: str) -> dict:
    """Get full conversation detail including messages and drafts."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.lead_name.ilike(f"%{lead_name}%"))
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return {"error": f"No conversation found for '{lead_name}'"}

        # Get message logs
        msg_result = await session.execute(
            select(MessageLog)
            .where(MessageLog.conversation_id == conversation.id)
            .order_by(MessageLog.sent_at)
        )
        messages = msg_result.scalars().all()

        # Get drafts
        draft_result = await session.execute(
            select(Draft)
            .where(Draft.conversation_id == conversation.id)
            .order_by(Draft.created_at)
        )
        drafts = draft_result.scalars().all()

        return {
            "lead_name": conversation.lead_name,
            "linkedin_url": conversation.linkedin_profile_url,
            "funnel_stage": conversation.funnel_stage.value if conversation.funnel_stage else None,
            "conversation_history": conversation.conversation_history,
            "messages": [
                {
                    "direction": m.direction.value,
                    "content": m.content,
                    "sent_at": m.sent_at.isoformat(),
                }
                for m in messages
            ],
            "drafts": [
                {
                    "status": d.status.value,
                    "ai_draft": d.ai_draft,
                    "created_at": d.created_at.isoformat(),
                }
                for d in drafts
            ],
        }


@app.get("/admin/conversations/today")
async def get_today_conversations() -> dict:
    """Get conversations updated today with prospect funnel data."""
    from sqlalchemy import func

    async with async_session_factory() as session:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        query = (
            select(Conversation)
            .where(Conversation.updated_at >= today_start)
            .order_by(Conversation.updated_at.desc())
        )
        result = await session.execute(query)
        conversations = result.scalars().all()

        items = []
        for c in conversations:
            # Get linked prospect
            p_result = await session.execute(
                select(Prospect).where(Prospect.conversation_id == c.id)
            )
            p = p_result.scalar_one_or_none()

            items.append({
                "lead_name": c.lead_name,
                "linkedin_url": c.linkedin_profile_url,
                "funnel_stage": c.funnel_stage.value if c.funnel_stage else None,
                "updated_at": c.updated_at.isoformat(),
                "prospect_pitched_at": p.pitched_at.isoformat() if p and p.pitched_at else None,
                "prospect_calendar_sent_at": p.calendar_sent_at.isoformat() if p and p.calendar_sent_at else None,
                "prospect_booked_at": p.booked_at.isoformat() if p and p.booked_at else None,
                "prospect_positive_reply_at": p.positive_reply_at.isoformat() if p and p.positive_reply_at else None,
                "has_prospect": p is not None,
            })

        return {"total": len(items), "conversations": items}


@app.get("/admin/prospects/funnel")
async def get_funnel_prospects(stage: str = "pitched") -> dict:
    """Get prospects at pitched stage or further in the funnel.

    Returns prospects where pitched_at, calendar_sent_at, or booked_at is set,
    along with their conversation funnel_stage.
    """
    from sqlalchemy import or_

    async with async_session_factory() as session:
        query = (
            select(Prospect)
            .outerjoin(Conversation, Prospect.conversation_id == Conversation.id)
            .where(
                or_(
                    Prospect.pitched_at.isnot(None),
                    Prospect.calendar_sent_at.isnot(None),
                    Prospect.booked_at.isnot(None),
                )
            )
            .order_by(Prospect.updated_at.desc())
        )
        result = await session.execute(query)
        prospects = result.scalars().all()

        # Also get conversation funnel stages
        conv_ids = [p.conversation_id for p in prospects if p.conversation_id]
        conv_stages = {}
        if conv_ids:
            conv_query = select(Conversation).where(Conversation.id.in_(conv_ids))
            conv_result = await session.execute(conv_query)
            for conv in conv_result.scalars().all():
                conv_stages[str(conv.id)] = conv.funnel_stage.value if conv.funnel_stage else None

        return {
            "total": len(prospects),
            "prospects": [
                {
                    "name": p.full_name,
                    "job_title": p.job_title,
                    "company": p.company_name,
                    "linkedin_url": p.linkedin_url,
                    "source": p.source_type.value if p.source_type else None,
                    "funnel_stage": conv_stages.get(str(p.conversation_id)) if p.conversation_id else None,
                    "positive_reply_at": p.positive_reply_at.isoformat() if p.positive_reply_at else None,
                    "pitched_at": p.pitched_at.isoformat() if p.pitched_at else None,
                    "calendar_sent_at": p.calendar_sent_at.isoformat() if p.calendar_sent_at else None,
                    "booked_at": p.booked_at.isoformat() if p.booked_at else None,
                }
                for p in prospects
            ],
        }


@app.get("/buying-signal/log")
async def view_buying_signal_log(limit: int = 20) -> dict:
    """View recent buying signal payloads for format inspection."""
    import json as _json

    if not os.path.exists(BUYING_SIGNAL_LOG):
        return {"entries": [], "total": 0}

    entries = []
    with open(BUYING_SIGNAL_LOG, "r") as f:
        for line in f:
            if line.strip():
                entries.append(_json.loads(line))

    return {
        "entries": entries[-limit:],
        "total": len(entries),
    }
