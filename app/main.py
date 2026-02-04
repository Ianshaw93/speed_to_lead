"""FastAPI application entry point."""

import logging
import sys
import uuid
from contextlib import asynccontextmanager

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
    logger.info("Routers imported")
except Exception as e:
    logger.error(f"Import failed: {e}", exc_info=True)
    raise

logger.info("All imports successful")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    yield
    # Shutdown - cleanup resources
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

            # 2. Log the inbound message
            message_log = MessageLog(
                conversation_id=conversation.id,
                direction=MessageDirection.INBOUND,
                content=payload.latest_message,
            )
            session.add(message_log)

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
