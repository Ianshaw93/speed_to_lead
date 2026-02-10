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
    from app.routers.metrics import router as metrics_router
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
