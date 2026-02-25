"""FastAPI application entry point."""

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

# Configure logging early
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

logger.info("Starting app import...")

try:
    from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
    from sqlalchemy import func, select
    logger.info("FastAPI and SQLAlchemy imported")

    from app.config import settings
    logger.info(f"Settings loaded, database_url starts with: {settings.database_url[:20]}...")

    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import async_session_factory, get_db
    logger.info("Database session factory created")

    from app.models import Conversation, Draft, DraftStatus, FunnelStage, MessageDirection, MessageLog, PipelineRun, Prospect, ProspectSource
    logger.info("Models imported")

    from app.schemas import HeyReachWebhookPayload, HealthResponse
    logger.info("Schemas imported")

    from app.services.deepseek import generate_reply_draft
    from app.services.example_retriever import get_similar_examples, format_examples_for_prompt
    from app.services.slack import SlackBot
    logger.info("Services imported")

    from app.routers.slack import router as slack_router
    from app.routers.metrics import router as metrics_router
    from app.routers.engagement import router as engagement_router
    from app.routers.changelog import router as changelog_router
    from app.routers.pipelines import router as pipelines_router
    from app.routers.costs import router as costs_router
    from app.routers.clients import router as clients_router
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
app.include_router(pipelines_router)
app.include_router(costs_router)
app.include_router(clients_router)


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


@app.post("/admin/backfill-history-roles")
async def backfill_history_roles(request: Request) -> dict:
    """Backfill conversation_history roles using MessageLog direction.

    Fixes the bug where all messages were stored with role:"lead".
    Cross-references each history entry's content against MessageLog
    to determine the correct role (lead vs you).

    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"
    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        async with async_session_factory() as session:
            # Get all conversations with history
            result = await session.execute(select(Conversation))
            conversations = result.scalars().all()

            fixed = 0
            rebuilt_count = 0
            skipped = 0
            already_correct = 0

            for conv in conversations:
                if not conv.conversation_history:
                    skipped += 1
                    continue

                # Check if any messages already have role="you" (already fixed)
                roles = {msg.get("role") for msg in conv.conversation_history}
                if "you" in roles:
                    already_correct += 1
                    continue

                # Get all MessageLogs for this conversation
                msg_result = await session.execute(
                    select(MessageLog).where(
                        MessageLog.conversation_id == conv.id
                    )
                )
                msg_logs = msg_result.scalars().all()

                if not msg_logs:
                    skipped += 1
                    continue

                # Build a set of outbound message contents for fast lookup
                outbound_contents = {
                    ml.content.strip()
                    for ml in msg_logs
                    if ml.direction == MessageDirection.OUTBOUND
                }

                # Fix roles in conversation_history
                updated = False
                new_history = []
                for msg in conv.conversation_history:
                    content = (msg.get("content") or "").strip()
                    if content in outbound_contents:
                        new_msg = {**msg, "role": "you"}
                        updated = True
                    else:
                        new_msg = {**msg, "role": "lead"}
                    new_history.append(new_msg)

                if updated:
                    conv.conversation_history = new_history
                    fixed += 1
                else:
                    # Content matching didn't work — rebuild history
                    # entirely from MessageLog (which has correct direction)
                    sorted_logs = sorted(msg_logs, key=lambda ml: ml.sent_at)
                    rebuilt = [
                        {
                            "role": "lead" if ml.direction == MessageDirection.INBOUND else "you",
                            "content": ml.content,
                            "time": ml.sent_at.isoformat() if ml.sent_at else "",
                        }
                        for ml in sorted_logs
                    ]
                    conv.conversation_history = rebuilt
                    rebuilt_count += 1

            await session.commit()

            return {
                "status": "ok",
                "fixed": fixed,
                "rebuilt_from_message_log": rebuilt_count,
                "already_correct": already_correct,
                "skipped": skipped,
                "total": len(conversations),
            }

    except Exception as e:
        logger.error(f"Backfill error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/sync-funnel-stages")
async def sync_funnel_stages(request: Request) -> dict:
    """Sync conversation.funnel_stage from prospect timestamps.

    Fixes mismatches where prospect has pitched_at/calendar_sent_at/booked_at
    but the linked conversation.funnel_stage wasn't updated.

    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"
    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        async with async_session_factory() as session:
            # Get all prospects with funnel timestamps and a linked conversation
            result = await session.execute(
                select(Prospect).where(
                    Prospect.conversation_id.isnot(None),
                    Prospect.pitched_at.isnot(None),
                )
            )
            prospects = result.scalars().all()

            synced = 0
            already_correct = 0
            no_conv = 0

            for prospect in prospects:
                # Determine the correct stage from prospect timestamps
                if prospect.booked_at:
                    correct_stage = FunnelStage.BOOKED
                elif prospect.calendar_sent_at:
                    correct_stage = FunnelStage.CALENDAR_SENT
                else:
                    correct_stage = FunnelStage.PITCHED

                # Get linked conversation
                conv_result = await session.execute(
                    select(Conversation).where(
                        Conversation.id == prospect.conversation_id
                    )
                )
                conv = conv_result.scalar_one_or_none()

                if not conv:
                    no_conv += 1
                    continue

                if conv.funnel_stage == correct_stage:
                    already_correct += 1
                    continue

                logger.info(
                    f"Syncing {prospect.full_name}: "
                    f"{conv.funnel_stage} -> {correct_stage.value}"
                )
                conv.funnel_stage = correct_stage
                synced += 1

            await session.commit()

            return {
                "status": "ok",
                "synced": synced,
                "already_correct": already_correct,
                "no_conversation": no_conv,
                "total_prospects_checked": len(prospects),
            }

    except Exception as e:
        logger.error(f"Funnel sync error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/health-check")
async def admin_health_check(request: Request) -> dict:
    """Run all health checks and return full results.

    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"

    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from app.services.health_check import run_health_check

    async with async_session_factory() as session:
        report = await run_health_check(session)

    return {
        "status": report.status.value,
        "timestamp": report.timestamp.isoformat(),
        "passing": report.passing,
        "total": len(report.checks),
        "checks": [
            {
                "name": c.name,
                "status": c.status.value,
                "message": c.message,
                "details": c.details,
            }
            for c in report.checks
        ],
    }


@app.get("/admin/health-check/status")
async def admin_health_check_status() -> dict:
    """Quick health check summary - no auth required.

    Returns overall status and names of failing checks only.
    """
    from app.services.health_check import run_health_check

    async with async_session_factory() as session:
        report = await run_health_check(session)

    return {
        "status": report.status.value,
        "passing": report.passing,
        "total": len(report.checks),
        "failing": [c.name for c in report.failing],
    }


@app.post("/admin/campaign-fuel-check")
async def admin_campaign_fuel_check(request: Request) -> dict:
    """Run campaign fuel check manually.

    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"

    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from app.services.campaign_monitor import monitor_and_topup

    result = await monitor_and_topup()
    return result


@app.post("/admin/run-competitor-pipeline")
async def admin_run_competitor_pipeline(
    request: Request,
    background_tasks: BackgroundTasks,
    keywords: str = "ceos",
    days_back: int = 7,
    min_reactions: int = 50,
    dry_run: bool = False,
) -> dict:
    """Trigger the competitor post pipeline manually.

    Runs in the background so the response returns immediately.
    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"

    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from app.services.prospect_pipeline import run_competitor_post_pipeline

    background_tasks.add_task(
        run_competitor_post_pipeline,
        keywords=keywords,
        days_back=days_back,
        min_reactions=min_reactions,
        dry_run=dry_run,
    )
    return {"status": "started", "keywords": keywords, "days_back": days_back, "dry_run": dry_run}


@app.post("/admin/run-lead-finder")
async def admin_run_lead_finder(
    request: Request,
    background_tasks: BackgroundTasks,
    job_titles: str | None = None,
    company_keywords: str | None = None,
    location: str = "united states",
    fetch_count: int = 100,
    dry_run: bool = False,
) -> dict:
    """Trigger the lead finder pipeline manually.

    job_titles and company_keywords are comma-separated strings.
    Runs in the background so the response returns immediately.
    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"

    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from app.services.lead_finder_pipeline import run_lead_finder_pipeline

    titles = [t.strip() for t in job_titles.split(",")] if job_titles else None
    keywords = [k.strip() for k in company_keywords.split(",")] if company_keywords else None

    background_tasks.add_task(
        run_lead_finder_pipeline,
        job_titles=titles,
        company_keywords=keywords,
        location=location,
        fetch_count=fetch_count,
        dry_run=dry_run,
    )
    return {"status": "started", "job_titles": titles, "company_keywords": keywords, "fetch_count": fetch_count, "dry_run": dry_run}


@app.get("/admin/pipeline-runs")
async def admin_list_pipeline_runs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = 10,
) -> list[dict]:
    """List recent pipeline runs with status and metrics.

    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"

    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = await db.execute(
        select(PipelineRun)
        .order_by(PipelineRun.created_at.desc())
        .limit(limit)
    )
    runs = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "run_type": r.run_type,
            "status": r.status,
            "posts_found": r.posts_found,
            "engagers_found": r.engagers_found,
            "profiles_scraped": r.profiles_scraped,
            "icp_qualified": r.icp_qualified,
            "final_leads": r.final_leads,
            "cost_total": str(r.cost_total),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "duration_seconds": r.duration_seconds,
            "error_message": r.error_message,
        }
        for r in runs
    ]


@app.post("/admin/expire-stale-drafts")
async def admin_expire_stale_drafts(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Expire stale PENDING drafts (aged, classified, or superseded).

    Protected by SECRET_KEY in the Authorization header.
    """
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.secret_key}"

    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from app.services.scheduler import expire_stale_drafts_task

    await expire_stale_drafts_task(session=db)

    return {"status": "ok"}


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
                {
                    "role": "lead" if msg.is_reply else "you",
                    "content": msg.message,
                    "time": msg.creation_time,
                }
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

            # 1.5. Extract the last outbound message (the one that triggered this reply)
            triggering_msg = None
            for msg in reversed(payload.all_recent_messages):
                if msg.is_reply is False:  # Explicit False = we sent it
                    triggering_msg = msg.message
                    break

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

            # 2.5. Log the inbound message (with dedup)
            from sqlalchemy import and_
            existing_inbound = await session.execute(
                select(MessageLog).where(
                    and_(
                        MessageLog.conversation_id == conversation.id,
                        MessageLog.direction == MessageDirection.INBOUND,
                        MessageLog.content == payload.latest_message,
                    )
                )
            )
            if not existing_inbound.scalar_one_or_none():
                message_log = MessageLog(
                    conversation_id=conversation.id,
                    direction=MessageDirection.INBOUND,
                    content=payload.latest_message,
                )
                session.add(message_log)
            else:
                logger.info(f"Skipped duplicate inbound message for conversation {conversation.id}")

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

            # Build lead context for better AI drafts
            lead_context = {
                "company": payload.lead_company,
                "title": payload.lead.position if payload.lead else None,
                "triggering_message": triggering_msg,
                "is_first_reply": is_first_reply,
                "personalized_message": payload.lead.personalized_message if payload.lead else None,
            }

            # Step 1: Detect funnel stage
            from app.services.deepseek import get_deepseek_client
            deepseek = get_deepseek_client()
            detected_stage, stage_reasoning = await deepseek.detect_stage(
                lead_name=payload.lead_name,
                lead_message=payload.latest_message,
                conversation_history=history,
                lead_context=lead_context,
            )

            # Step 2: Retrieve similar past conversations as dynamic examples
            dynamic_examples_str = ""
            try:
                similar_examples = await get_similar_examples(
                    stage=detected_stage,
                    lead_context=lead_context,
                    current_lead_message=payload.latest_message,
                    db=session,
                )
                dynamic_examples_str = format_examples_for_prompt(similar_examples)
                if similar_examples:
                    logger.info(f"Retrieved {len(similar_examples)} dynamic examples for stage {detected_stage.value}")
            except Exception as ex:
                logger.warning(f"Example retrieval failed (non-fatal): {ex}")

            # Step 3: Generate reply with stage-specific prompt + dynamic examples
            reply = await deepseek.generate_with_stage(
                lead_name=payload.lead_name,
                lead_message=payload.latest_message,
                stage=detected_stage,
                conversation_history=history,
                lead_context=lead_context,
                dynamic_examples=dynamic_examples_str,
            )

            from app.services.deepseek import DraftResult
            draft_result = DraftResult(
                detected_stage=detected_stage,
                stage_reasoning=stage_reasoning,
                reply=reply,
            )
            print(f"Detected stage: {draft_result.detected_stage.value}", flush=True)
            print(f"Generated draft: {draft_result.reply[:100]}...", flush=True)
            logger.info(f"Detected stage: {draft_result.detected_stage.value}")
            logger.info(f"Generated draft: {draft_result.reply[:100]}...")

            # Update conversation with detected funnel stage
            conversation.funnel_stage = draft_result.detected_stage

            # 4. Link prospect to conversation (if exists) - before Slack notification
            # so we have prospect_id available for the Gift Leads button
            prospect = None
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
                    # Backfill name from conversation if prospect has no name
                    if payload.lead_name and not prospect.full_name:
                        parts = payload.lead_name.strip().split(" ", 1)
                        prospect.full_name = payload.lead_name
                        prospect.first_name = parts[0]
                        prospect.last_name = parts[1] if len(parts) > 1 else None
                    logger.info(f"Linked prospect {prospect.id} to conversation {conversation.id}")

            # 5. QA check before sending to Slack
            print("Running QA check on draft...", flush=True)
            logger.info(f"Running QA check on draft for {payload.lead_name}")

            qa_result = None
            final_draft_text = draft_result.reply
            try:
                from app.services.qa_agent import (
                    qa_check_with_regen,
                    load_guidelines_for_stage,
                )

                guidelines = await load_guidelines_for_stage(
                    session, draft_result.detected_stage.value
                )

                qa_result, final_draft_text = await qa_check_with_regen(
                    lead_name=payload.lead_name,
                    lead_message=payload.latest_message,
                    ai_draft=draft_result.reply,
                    detected_stage=draft_result.detected_stage.value,
                    stage_reasoning=draft_result.stage_reasoning,
                    conversation_history=history,
                    lead_context=lead_context,
                    guidelines=guidelines,
                )

                logger.info(
                    f"QA result for {payload.lead_name}: "
                    f"score={qa_result.score}, verdict={qa_result.verdict}"
                )

                # If blocked after regen, store draft but skip Slack
                if qa_result.verdict == "block":
                    logger.info(
                        f"QA blocked draft for {payload.lead_name} "
                        f"(score={qa_result.score}). Not sending to Slack."
                    )
                    draft_id = uuid.uuid4()
                    draft = Draft(
                        id=draft_id,
                        conversation_id=conversation.id,
                        status=DraftStatus.REJECTED,
                        ai_draft=final_draft_text,
                        original_ai_draft=draft_result.reply,
                        triggering_message=triggering_msg,
                        is_first_reply=is_first_reply,
                        judge_score=draft_result.judge_score,
                        judge_feedback=draft_result.judge_feedback,
                        revision_count=draft_result.revision_count,
                        qa_score=qa_result.score,
                        qa_verdict=qa_result.verdict,
                        qa_issues=[{"type": i.type, "detail": i.detail, "severity": i.severity} for i in qa_result.issues],
                        qa_model=qa_result.model,
                        qa_cost_usd=qa_result.cost_usd,
                    )
                    session.add(draft)
                    await session.commit()
                    return {"draft_id": str(draft_id), "qa_blocked": True}

            except Exception as e:
                logger.warning(f"QA check failed, proceeding without QA: {e}")
                qa_result = None
                final_draft_text = draft_result.reply

            # 6. Save draft to DB FIRST so Slack buttons always reference an existing draft
            draft_id = uuid.uuid4()
            draft = Draft(
                id=draft_id,
                conversation_id=conversation.id,
                status=DraftStatus.PENDING,
                ai_draft=final_draft_text,
                original_ai_draft=draft_result.reply,
                slack_message_ts=None,  # Will be set after Slack send
                triggering_message=triggering_msg,
                is_first_reply=is_first_reply,
                judge_score=draft_result.judge_score,
                judge_feedback=draft_result.judge_feedback,
                revision_count=draft_result.revision_count,
                qa_score=qa_result.score if qa_result else None,
                qa_verdict=qa_result.verdict if qa_result else None,
                qa_issues=[{"type": i.type, "detail": i.detail, "severity": i.severity} for i in qa_result.issues] if qa_result and qa_result.issues else None,
                qa_model=qa_result.model if qa_result else None,
                qa_cost_usd=qa_result.cost_usd if qa_result else None,
            )
            session.add(draft)
            await session.commit()

            # 7. Send draft to Slack for approval (with QA annotation)
            print("Sending to Slack...", flush=True)
            slack_bot = SlackBot()
            slack_ts = await slack_bot.send_draft_notification(
                draft_id=draft_id,
                lead_name=payload.lead_name,
                lead_title=None,  # Not in payload
                lead_company=payload.lead_company,
                linkedin_url=f"https://www.linkedin.com/messaging/thread/{payload.conversation_id}",
                lead_message=payload.latest_message,
                ai_draft=final_draft_text,
                funnel_stage=draft_result.detected_stage,
                stage_reasoning=draft_result.stage_reasoning,
                is_first_reply=is_first_reply,
                triggering_message=triggering_msg,
                prospect_id=prospect.id if prospect else None,
                judge_score=draft_result.judge_score,
                revision_count=draft_result.revision_count,
                qa_score=qa_result.score if qa_result else None,
                qa_verdict=qa_result.verdict if qa_result else None,
                qa_issues=[{"type": i.type, "detail": i.detail, "severity": i.severity} for i in qa_result.issues] if qa_result and qa_result.issues else None,
            )
            print(f"Slack notification sent, ts: {slack_ts}", flush=True)
            logger.info(f"Sent Slack notification, ts: {slack_ts}")

            # 8. Update draft with Slack message timestamp
            draft.slack_message_ts = slack_ts
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
                {
                    "role": "lead" if msg.is_reply else "you",
                    "content": msg.message,
                    "time": msg.creation_time,
                }
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

            created = 0
            deduped = 0

            for msg in payload.all_recent_messages:
                if not msg.message:
                    continue

                # is_reply=True means lead sent it (INBOUND), False means we sent it (OUTBOUND)
                direction = MessageDirection.INBOUND if msg.is_reply else MessageDirection.OUTBOUND

                # Dedup: check for existing message with same content + direction + conversation
                # No time window — replayed webhooks with old messages must still be caught
                existing_result = await session.execute(
                    select(MessageLog).where(
                        and_(
                            MessageLog.conversation_id == conversation.id,
                            MessageLog.direction == direction,
                            MessageLog.content == msg.message,
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
    """Check if prospect should be stopped in follow-up campaign and removed from list.

    If a prospect replies within 24h of being added to the follow-up list,
    they replied before the first follow-up fired. We need to:
    1. Stop them in the campaign (prevents the first follow-up from sending)
    2. Remove them from the follow-up list

    HeyReach natively handles replies that come after a follow-up has been sent,
    so we only need to act within the 24h window.

    Args:
        session: Database session.
        linkedin_url: The prospect's LinkedIn profile URL.

    Returns:
        True if prospect was stopped/removed, False otherwise.
    """
    from datetime import datetime, timedelta, timezone
    from app.services.heyreach import get_heyreach_client, HeyReachError
    from app.routers.slack import HEYREACH_FOLLOW_UP_CAMPAIGN_ID

    normalized_url = normalize_linkedin_url(linkedin_url)
    if not normalized_url:
        return False

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    # Find prospect added to follow-up list within last 24 hours
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

    # Prospect replied before the first follow-up fired
    logger.info(
        f"Prospect {prospect.full_name} ({normalized_url}) replied within 24h of "
        f"being added to follow-up list {prospect.followup_list_id}. "
        f"Stopping campaign {HEYREACH_FOLLOW_UP_CAMPAIGN_ID} and removing from list..."
    )

    try:
        heyreach = get_heyreach_client()

        # Stop the lead in the follow-up campaign
        await heyreach.stop_lead_in_campaign(
            campaign_id=HEYREACH_FOLLOW_UP_CAMPAIGN_ID,
            linkedin_url=normalized_url,
        )
        logger.info(
            f"Stopped {normalized_url} in campaign {HEYREACH_FOLLOW_UP_CAMPAIGN_ID}"
        )

        # Remove from the follow-up list
        await heyreach.remove_lead_from_list(
            list_id=prospect.followup_list_id,
            linkedin_url=normalized_url,
        )
        logger.info(f"Removed {normalized_url} from follow-up list {prospect.followup_list_id}")

        # Clear the follow-up tracking fields
        prospect.followup_list_id = None
        prospect.added_to_followup_at = None
        await session.commit()

        return True

    except HeyReachError as e:
        logger.error(f"Failed to stop/remove prospect from follow-up: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error in follow-up removal: {e}", exc_info=True)
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
                    # Backfill activity fields if currently null
                    if p.get("connection_count") is not None and existing.connection_count is None:
                        existing.connection_count = p["connection_count"]
                    if p.get("follower_count") is not None and existing.follower_count is None:
                        existing.follower_count = p["follower_count"]
                    if p.get("is_creator") is not None and existing.is_creator is None:
                        existing.is_creator = p["is_creator"]
                    if p.get("activity_score") is not None and existing.activity_score is None:
                        existing.activity_score = p["activity_score"]
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
                        connection_count=p.get("connection_count"),
                        follower_count=p.get("follower_count"),
                        is_creator=p.get("is_creator"),
                        activity_score=p.get("activity_score"),
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
                    connection_count=p.get("connection_count"),
                    follower_count=p.get("follower_count"),
                    is_creator=p.get("is_creator"),
                    activity_score=p.get("activity_score"),
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


@app.get("/api/prospects/by-icp")
async def prospects_by_icp(keywords: str, limit: int = 15) -> dict:
    """Search prospect pool by ICP keywords in job_title and headline.

    Args:
        keywords: Comma-separated list of search terms.
        limit: Max results (default 15).

    Returns:
        Dict with pool_size, matches count, and prospects list.
    """
    from sqlalchemy import or_, func

    terms = [t.strip() for t in keywords.split(",") if t.strip()]
    if not terms:
        raise HTTPException(status_code=400, detail="No keywords provided")

    async with async_session_factory() as session:
        # Pool size: total prospects with activity_score
        pool_result = await session.execute(
            select(func.count(Prospect.id)).where(
                Prospect.activity_score.isnot(None)
            )
        )
        pool_size = pool_result.scalar() or 0

        # Build OR conditions across job_title and headline
        conditions = []
        for term in terms:
            conditions.append(Prospect.job_title.ilike(f"%{term}%"))
            conditions.append(Prospect.headline.ilike(f"%{term}%"))

        result = await session.execute(
            select(Prospect)
            .where(
                or_(*conditions),
                Prospect.activity_score.isnot(None),
            )
            .order_by(Prospect.activity_score.desc().nullslast())
            .limit(limit)
        )
        prospects = result.scalars().all()

        return {
            "pool_size": pool_size,
            "matches": len(prospects),
            "prospects": [
                {
                    "linkedin_url": p.linkedin_url,
                    "full_name": p.full_name,
                    "job_title": p.job_title,
                    "company_name": p.company_name,
                    "location": p.location,
                    "headline": p.headline,
                    "activity_score": float(p.activity_score) if p.activity_score else None,
                }
                for p in prospects
            ],
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


@app.post("/api/trend-scout/run")
async def trigger_trend_scout(
    background_tasks: BackgroundTasks,
) -> dict:
    """Manually trigger trend scout discovery.

    Runs the full Perplexity + Claude pipeline in the background.
    Results are saved to contentCreator's DB and reported via Slack.
    """
    from app.services.trend_scout import run_trend_scout_task

    async def _run():
        try:
            result = await run_trend_scout_task()
            logger.info(f"Manual trend scout result: {result}")
        except Exception as e:
            logger.error(f"Manual trend scout failed: {e}", exc_info=True)

    background_tasks.add_task(_run)
    return {"status": "processing", "message": "Trend scout triggered"}


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


# =============================================================================
# PIPELINE RUNS TRACKING
# =============================================================================


@app.post("/api/pipeline-runs")
async def create_pipeline_run(request: Request) -> dict:
    """Record a pipeline run with metrics and costs.

    Called by multichannel-outreach after a pipeline completes (or fails).
    """
    data = await request.json()

    run_type = data.get("run_type")
    if not run_type:
        raise HTTPException(status_code=400, detail="run_type is required")

    async with async_session_factory() as session:
        from decimal import Decimal

        run = PipelineRun(
            run_type=run_type,
            prospect_url=data.get("prospect_url"),
            prospect_name=data.get("prospect_name"),
            icp_description=data.get("icp_description"),
            status=data.get("status", "completed"),
            # Pipeline metrics
            queries_generated=data.get("queries_generated", 0),
            posts_found=data.get("posts_found", 0),
            engagers_found=data.get("engagers_found", 0),
            profiles_scraped=data.get("profiles_scraped", 0),
            location_filtered=data.get("location_filtered", 0),
            icp_qualified=data.get("icp_qualified", 0),
            final_leads=data.get("final_leads", 0),
            # Cost breakdown
            cost_apify_google=Decimal(str(data.get("cost_apify_google", 0))),
            cost_apify_reactions=Decimal(str(data.get("cost_apify_reactions", 0))),
            cost_apify_profiles=Decimal(str(data.get("cost_apify_profiles", 0))),
            cost_deepseek_icp=Decimal(str(data.get("cost_deepseek_icp", 0))),
            cost_deepseek_personalize=Decimal(str(data.get("cost_deepseek_personalize", 0))),
            cost_total=Decimal(str(data.get("cost_total", 0))),
            # API call counts
            count_google_searches=data.get("count_google_searches", 0),
            count_posts_scraped=data.get("count_posts_scraped", 0),
            count_profiles_scraped=data.get("count_profiles_scraped", 0),
            count_icp_checks=data.get("count_icp_checks", 0),
            count_personalizations=data.get("count_personalizations", 0),
            # Timing
            duration_seconds=data.get("duration_seconds"),
            error_message=data.get("error_message"),
        )

        if data.get("status") in ("completed", "failed"):
            run.completed_at = datetime.now(timezone.utc)

        session.add(run)
        await session.commit()

        logger.info(f"Pipeline run recorded: {run.id} ({run_type}, {run.status})")
        return {"id": str(run.id), "status": "created"}


@app.get("/api/pipeline-runs")
async def list_pipeline_runs(
    run_type: str | None = None,
    limit: int = 20,
    days: int | None = None,
) -> dict:
    """List pipeline runs with optional filters.

    Query params:
        run_type: Filter by type (gift_leads, competitor_post, buying_signal)
        limit: Max results (default 20)
        days: Only runs from last N days
    """
    from sqlalchemy import func
    from decimal import Decimal

    async with async_session_factory() as session:
        query = select(PipelineRun).order_by(PipelineRun.created_at.desc())

        if run_type:
            query = query.where(PipelineRun.run_type == run_type)
        if days:
            cutoff = datetime.now(timezone.utc) - __import__('datetime').timedelta(days=days)
            query = query.where(PipelineRun.created_at >= cutoff)

        query = query.limit(limit)
        result = await session.execute(query)
        runs = result.scalars().all()

        # Compute totals
        totals_query = select(
            func.count(PipelineRun.id).label("runs"),
            func.sum(PipelineRun.cost_total).label("cost"),
            func.sum(PipelineRun.final_leads).label("leads"),
        )
        if run_type:
            totals_query = totals_query.where(PipelineRun.run_type == run_type)
        if days:
            totals_query = totals_query.where(PipelineRun.created_at >= cutoff)

        totals_result = await session.execute(totals_query)
        totals_row = totals_result.one()

        return {
            "runs": [
                {
                    "id": str(r.id),
                    "run_type": r.run_type,
                    "prospect_name": r.prospect_name,
                    "status": r.status,
                    "final_leads": r.final_leads,
                    "cost_total": float(r.cost_total) if r.cost_total else 0,
                    "duration_seconds": r.duration_seconds,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "error_message": r.error_message,
                }
                for r in runs
            ],
            "totals": {
                "runs": totals_row.runs or 0,
                "cost": float(totals_row.cost or Decimal("0")),
                "leads": totals_row.leads or 0,
            },
        }


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


@app.get("/admin/message-effectiveness")
async def message_effectiveness(limit: int = 50) -> dict:
    """Get positive-classified drafts with their triggering messages.

    Shows which outbound messages are earning positive replies,
    enabling message effectiveness analysis.

    Query params:
        limit: Max results (default 50).
    """
    from sqlalchemy import func

    async with async_session_factory() as session:
        query = (
            select(Draft, Conversation, Prospect)
            .join(Conversation, Draft.conversation_id == Conversation.id)
            .outerjoin(Prospect, Prospect.conversation_id == Conversation.id)
            .where(Draft.triggering_message.isnot(None))
            .order_by(Draft.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(query)
        rows = result.all()

        items = []
        for draft, conversation, prospect in rows:
            items.append({
                "prospect_name": prospect.full_name if prospect else conversation.lead_name,
                "linkedin_url": prospect.linkedin_url if prospect else conversation.linkedin_profile_url,
                "triggering_message": draft.triggering_message,
                "reply": draft.ai_draft,
                "classification": draft.classification.value if draft.classification else None,
                "classified_at": draft.classified_at.isoformat() if draft.classified_at else None,
                "created_at": draft.created_at.isoformat(),
                "is_first_reply": draft.is_first_reply,
            })

        # Summary stats
        positive_count = sum(1 for i in items if i["classification"] == "positive")

        return {
            "total": len(items),
            "positive_count": positive_count,
            "items": items,
        }


@app.get("/admin/verify-reply-capture")
async def verify_reply_capture(hours: int = 48) -> dict:
    """Verify fresh replies are being captured in message_log.

    Checks for new messages, duplicates, and staleness.
    Designed to be called as a cron job for pipeline health monitoring.

    Query params:
        hours: Check window in hours (default 48).
    """
    from sqlalchemy import func, text

    async with async_session_factory() as session:
        # Total counts
        total_msgs = (await session.execute(
            select(func.count(MessageLog.id))
        )).scalar()
        recent_msgs = (await session.execute(
            select(func.count(MessageLog.id)).where(
                MessageLog.sent_at >= text(f"NOW() - INTERVAL '{hours} hours'")
            )
        )).scalar()
        recent_inbound = (await session.execute(
            select(func.count(MessageLog.id)).where(
                MessageLog.sent_at >= text(f"NOW() - INTERVAL '{hours} hours'"),
                MessageLog.direction == MessageDirection.INBOUND,
            )
        )).scalar()

        # Duplicate check
        dupe_query = text(f"""
            SELECT COUNT(*) FROM (
                SELECT conversation_id, direction, content
                FROM message_log
                WHERE sent_at >= NOW() - INTERVAL '{hours} hours'
                GROUP BY conversation_id, direction, content
                HAVING COUNT(*) > 1
            ) dupes
        """)
        dupe_count = (await session.execute(dupe_query)).scalar()

        # Latest message time
        latest = (await session.execute(
            select(func.max(MessageLog.sent_at))
        )).scalar()
        hours_since_latest = None
        if latest:
            from datetime import datetime, timezone
            hours_since_latest = round(
                (datetime.now(timezone.utc) - latest).total_seconds() / 3600, 1
            )

        status = "PASS"
        warnings = []
        if recent_msgs == 0:
            status = "WARN"
            warnings.append(f"No messages in last {hours}h")
        if dupe_count > 0:
            status = "WARN"
            warnings.append(f"{dupe_count} duplicate groups found")
        if hours_since_latest and hours_since_latest > 12:
            status = "WARN"
            warnings.append(f"Last message was {hours_since_latest}h ago")

        return {
            "status": status,
            "window_hours": hours,
            "total_messages": total_msgs,
            "recent_messages": recent_msgs,
            "recent_inbound": recent_inbound,
            "duplicates": dupe_count,
            "hours_since_latest_message": hours_since_latest,
            "warnings": warnings,
        }


@app.post("/admin/gift-leads/generate-sheet")
async def admin_generate_gift_leads_sheet(
    prospect_name: str,
    background_tasks: BackgroundTasks,
) -> dict:
    """Generate Google Sheet from existing DB leads and post to Slack with Send button.

    Use this to recover gift leads that completed the pipeline but never
    got the Sheet/Slack notification.

    Query params:
        prospect_name: Prospect name (fuzzy matched against pipeline_runs).
    """
    from sqlalchemy import or_

    from app.services.google_sheets import get_google_sheets_service
    from app.services.slack import get_slack_bot

    async with async_session_factory() as session:
        # Find the pipeline run
        run_result = await session.execute(
            select(PipelineRun)
            .where(PipelineRun.prospect_name.ilike(f"%{prospect_name}%"))
            .order_by(PipelineRun.cost_total.desc())  # prefer the real run
            .limit(1)
        )
        pipeline_run = run_result.scalar_one_or_none()

        if not pipeline_run:
            raise HTTPException(404, f"No pipeline run found for '{prospect_name}'")

        # Find the prospect record to get prospect_id
        # Try exact URL match first, then try conversation URL, then name match
        prospect = None
        prospect_result = await session.execute(
            select(Prospect).where(
                Prospect.linkedin_url == pipeline_run.prospect_url
            )
        )
        prospect = prospect_result.scalar_one_or_none()

        # Try conversation match by URL or name
        conv = None
        if not prospect:
            conv_result = await session.execute(
                select(Conversation).where(
                    or_(
                        Conversation.linkedin_profile_url == pipeline_run.prospect_url,
                        Conversation.lead_name.ilike(f"%{prospect_name}%"),
                    )
                ).order_by(Conversation.updated_at.desc())
                .limit(1)
            )
            conv = conv_result.scalar_one_or_none()

            # If we found a conversation, try to find prospect by that URL
            if conv:
                prospect_result = await session.execute(
                    select(Prospect).where(
                        Prospect.linkedin_url == conv.linkedin_profile_url
                    )
                )
                prospect = prospect_result.scalar_one_or_none()

        if not prospect and not conv:
            raise HTTPException(404, f"No prospect or conversation found for '{prospect_name}'")

        prospect_id = prospect.id if prospect else None
        prospect_display_name = (prospect.full_name if prospect else conv.lead_name) if prospect or conv else pipeline_run.prospect_name

        # Find the ICP-matched leads from this pipeline run's time window
        # Use a 2-hour window around the run's started_at
        run_start = pipeline_run.started_at
        window_start = run_start - timedelta(hours=1)
        window_end = run_start + timedelta(hours=1)

        leads_result = await session.execute(
            select(Prospect)
            .where(
                Prospect.source_type == ProspectSource.COMPETITOR_POST,
                Prospect.icp_match == True,
                Prospect.created_at.between(window_start, window_end),
            )
            .order_by(Prospect.activity_score.desc().nullslast())
        )
        leads_list = leads_result.scalars().all()

        if not leads_list:
            raise HTTPException(404, f"No ICP-matched leads found for pipeline run (window: {window_start} to {window_end})")

        leads = [
            {
                "full_name": p.full_name,
                "job_title": p.job_title,
                "company_name": p.company_name,
                "location": p.location,
                "headline": p.headline,
                "activity_score": float(p.activity_score) if p.activity_score else 0,
                "icp_reason": p.icp_reason or "",
                "linkedin_url": p.linkedin_url,
            }
            for p in leads_list
        ]

    # Create Google Sheet
    sheet_url = None
    sheets_svc = get_google_sheets_service()
    if sheets_svc:
        try:
            sheet_url = sheets_svc.create_gift_leads_sheet(
                prospect_name=prospect_display_name,
                leads=leads,
            )
        except Exception as e:
            logger.error(f"Failed to create Google Sheet: {e}", exc_info=True)

    # Compose draft DM
    first_name = prospect_display_name.split()[0] if prospect_display_name else "there"
    if sheet_url:
        draft_dm = (
            f"Hey {first_name}, I pulled together some people in your space "
            f"that might be worth connecting with:\n\n"
            f"{sheet_url}\n\n"
            f"Let me know if any of these are useful!"
        )
    else:
        draft_dm = (
            f"Hey {first_name}, I pulled together {len(leads)} people in your space "
            f"that might be worth connecting with. Let me know if you'd like the list!"
        )

    icp_desc = pipeline_run.icp_description or "(unknown ICP)"

    # Post to Slack with Send/Edit buttons
    slack_bot = get_slack_bot()
    if prospect_id:
        await slack_bot.send_gift_leads_ready(
            prospect_id=prospect_id,
            prospect_name=prospect_display_name,
            lead_count=len(leads),
            icp=icp_desc,
            context="Recovered from pipeline run",
            draft_dm=draft_dm,
            sheet_url=sheet_url,
        )
    else:
        await slack_bot.send_confirmation(
            f"*Gift Leads for {prospect_display_name}* ({len(leads)} leads)\n"
            + (f"<{sheet_url}|Google Sheet>\n" if sheet_url else "")
            + "\n".join(
                f"{i+1}. *{l['full_name']}* - {l['job_title']} @ {l['company_name']}"
                for i, l in enumerate(leads)
            )
        )

    return {
        "prospect": prospect_display_name,
        "pipeline_run_id": str(pipeline_run.id),
        "leads_found": len(leads),
        "sheet_url": sheet_url,
        "posted_to_slack": True,
        "cost_incurred": float(pipeline_run.cost_total) if pipeline_run.cost_total else 0,
    }


@app.post("/admin/gift-leads/trigger")
async def admin_trigger_gift_leads(
    prospect_name: str,
    keywords: str,
    background_tasks: BackgroundTasks,
    icp_label: str | None = None,
    min_leads: int = 10,
) -> dict:
    """Trigger the gift leads flow with 3-tier fallback approach.

    Tier 1: DB pool + strict ICP re-qualification via DeepSeek.
    Tier 2: Full 12-step gift leads pipeline (background, ~5-15 min).
    Tier 3: Lead finder / Sales Nav search (background).

    Query params:
        prospect_name: Prospect name (fuzzy matched against conversations).
        keywords: Comma-separated keywords to search by.
        icp_label: Short ICP description for the draft DM (e.g. "B2B tech founders").
                   Used as "<icp_label> showing high intent signals" in the message.
        min_leads: Minimum qualified leads before triggering fallback (default 10).
    """
    from sqlalchemy import or_

    from app.services.gift_pipeline.cost_tracker import CostTracker
    from app.services.gift_pipeline.deepseek_calls import check_icp_match
    from app.services.google_sheets import get_google_sheets_service
    from app.services.slack import get_slack_bot

    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keyword_list:
        raise HTTPException(400, "No keywords provided")

    async with async_session_factory() as session:
        # Find the conversation
        conv_result = await session.execute(
            select(Conversation).where(
                Conversation.lead_name.ilike(f"%{prospect_name}%")
            ).order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        conv = conv_result.scalar_one_or_none()

        if not conv:
            raise HTTPException(404, f"No conversation found for '{prospect_name}'")

        # Find prospect by linkedin URL
        prospect_result = await session.execute(
            select(Prospect).where(
                Prospect.linkedin_url == conv.linkedin_profile_url
            )
        )
        prospect = prospect_result.scalar_one_or_none()

        # Tier 1: Over-fetch from DB pool, then ICP re-qualify
        conditions = []
        for kw in keyword_list:
            conditions.append(Prospect.job_title.ilike(f"%{kw}%"))
            conditions.append(Prospect.headline.ilike(f"%{kw}%"))

        leads_result = await session.execute(
            select(Prospect)
            .where(
                or_(*conditions),
                Prospect.activity_score.isnot(None),
            )
            .order_by(Prospect.activity_score.desc().nullslast())
            .limit(30)
        )
        leads_list = leads_result.scalars().all()

        pool_result = await session.execute(
            select(func.count(Prospect.id)).where(
                Prospect.activity_score.isnot(None)
            )
        )
        pool_size = pool_result.scalar() or 0

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
        for p in leads_list
    ]

    # ICP re-qualification: filter leads through DeepSeek
    icp_criteria = icp_label or ", ".join(keyword_list)
    if leads:
        cost_tracker = CostTracker()
        qualified_leads = []
        for lead in leads:
            try:
                icp_result = await check_icp_match(lead, cost_tracker, icp_criteria)
                if icp_result.get("match", True):
                    qualified_leads.append(lead)
            except Exception as e:
                logger.warning(f"ICP re-qualification error for {lead.get('full_name')}: {e}")
                qualified_leads.append(lead)  # Keep on error
        before_count = len(leads)
        leads = qualified_leads[:15]
        logger.info(
            f"ICP re-qualification: {before_count} → {len(leads)} leads "
            f"(ICP: {icp_criteria[:60]})"
        )

    # Check if Tier 1 has enough leads
    prospect_id = prospect.id if prospect else None
    linkedin_url = conv.linkedin_profile_url

    if len(leads) >= min_leads:
        # Tier 1 success: create sheet and post to Slack
        sheet_url = None
        sheets_svc = get_google_sheets_service()
        if sheets_svc:
            try:
                sheet_url = sheets_svc.create_gift_leads_sheet(
                    prospect_name=conv.lead_name,
                    leads=leads,
                )
            except Exception as e:
                logger.error(f"Failed to create Google Sheet: {e}", exc_info=True)

        icp_text = icp_label or ", ".join(keyword_list)
        if sheet_url:
            draft_dm = (
                f"{icp_text} showing high intent signals\n\n"
                f"{sheet_url}\n\n"
                f"Will be valuable for you\n\n"
                f"Oh yeah I included the LinkedIn profile links in the spreadsheet. "
                f"Rather than emails etc. Is LI your main way to reach out to "
                f"potential clients? Or more through warm network/word of mouth"
            )
        else:
            draft_dm = (
                f"{icp_text} showing high intent signals\n\n"
                f"Will be valuable for you - let me know if you'd like the list!"
            )

        slack_bot = get_slack_bot()
        await slack_bot.send_gift_leads_ready(
            prospect_id=prospect_id,
            prospect_name=conv.lead_name,
            lead_count=len(leads),
            icp=", ".join(keyword_list),
            context="DB pool keyword search + ICP re-qualification",
            draft_dm=draft_dm,
            sheet_url=sheet_url,
            conversation_id=conv.id if not prospect_id else None,
        )

        return {
            "prospect": conv.lead_name,
            "leads_found": len(leads),
            "pool_size": pool_size,
            "keywords": keyword_list,
            "sheet_url": sheet_url,
            "posted_to_slack": True,
            "tier": 1,
        }

    # Tier 2/3: Not enough qualified leads — run fallback in background
    tier1_leads = leads  # Partial results to combine later

    background_tasks.add_task(
        _admin_gift_leads_fallback,
        conv_lead_name=conv.lead_name,
        conv_id=conv.id,
        prospect_id=prospect_id,
        linkedin_url=linkedin_url,
        keyword_list=keyword_list,
        icp_label=icp_label,
        tier1_leads=tier1_leads,
        min_leads=min_leads,
    )

    tier1_count = len(tier1_leads)
    return {
        "prospect": conv.lead_name,
        "leads_found": tier1_count,
        "pool_size": pool_size,
        "keywords": keyword_list,
        "tier": 2,
        "message": (
            f"Only {tier1_count} qualified leads from DB (need {min_leads}). "
            f"Pipeline fallback running in background (~5-15 min)."
        ),
    }


async def _admin_gift_leads_fallback(
    conv_lead_name: str,
    conv_id: uuid.UUID,
    prospect_id: uuid.UUID | None,
    linkedin_url: str | None,
    keyword_list: list[str],
    icp_label: str | None,
    tier1_leads: list[dict],
    min_leads: int,
) -> None:
    """Background task: Tier 2 (pipeline) / Tier 3 (lead finder) fallback.

    Combines any Tier 1 leads with pipeline/lead-finder leads, deduplicates,
    creates a Google Sheet, and posts to Slack.
    """
    from app.services.google_sheets import get_google_sheets_service
    from app.services.slack import get_slack_bot

    slack_bot = get_slack_bot()
    icp_text = icp_label or ", ".join(keyword_list)

    # Tier 2: Full pipeline (requires LinkedIn URL)
    pipeline_leads: list[dict] = []
    if linkedin_url:
        thread_ts = await slack_bot.send_confirmation(
            f"Only {len(tier1_leads)} ICP-qualified DB leads for *{conv_lead_name}*.\n"
            f"Starting full research pipeline (~5-15 min, ~$1.50)..."
        )

        async def post_progress(text: str) -> None:
            try:
                await slack_bot.send_pipeline_progress(text, thread_ts)
            except Exception as e:
                logger.error(f"Failed to post pipeline progress: {e}")

        try:
            from app.services.gift_pipeline import run_gift_leads_pipeline_async

            pipeline_result = await run_gift_leads_pipeline_async(
                prospect_url=linkedin_url,
                prospect_name=conv_lead_name,
                progress=post_progress,
                user_icp=icp_label,
            )
            pipeline_leads = pipeline_result.get("leads", [])
            if pipeline_leads:
                await post_progress(
                    f"Pipeline found {len(pipeline_leads)} leads."
                )
        except Exception as e:
            logger.error(f"Pipeline fallback error: {e}", exc_info=True)
            await post_progress(f"Pipeline error: {e}")

    # Tier 3: Lead finder (last resort) — if still short
    all_leads = _dedup_leads(tier1_leads + _normalize_pipeline_leads(pipeline_leads))
    if len(all_leads) < min_leads and not pipeline_leads:
        try:
            await slack_bot.send_confirmation(
                f"Pipeline returned 0 leads. Trying Sales Nav search..."
            )
            from app.services.lead_finder_pipeline import find_leads_apify

            # Derive job titles from keywords/icp_label
            job_titles = keyword_list[:3] if keyword_list else [icp_text]
            apify_leads = await find_leads_apify(
                job_titles=job_titles,
                company_keywords=keyword_list[:3],
                location="united kingdom",
                fetch_count=20,
                require_email=False,
            )

            # ICP qualify the Apify results
            if apify_leads:
                from app.services.gift_pipeline.cost_tracker import CostTracker
                from app.services.gift_pipeline.deepseek_calls import check_icp_match

                cost_tracker = CostTracker()
                qualified = []
                for lead in apify_leads:
                    # Normalize to our format
                    normalized = {
                        "full_name": lead.get("fullName", ""),
                        "job_title": lead.get("jobTitle", ""),
                        "company_name": lead.get("companyName", ""),
                        "location": lead.get("addressWithCountry", ""),
                        "headline": lead.get("headline", ""),
                        "activity_score": 0,
                        "linkedin_url": lead.get("linkedinUrl", ""),
                    }
                    try:
                        icp_result = await check_icp_match(
                            normalized, cost_tracker, icp_text
                        )
                        if icp_result.get("match", True):
                            qualified.append(normalized)
                    except Exception:
                        qualified.append(normalized)
                all_leads = _dedup_leads(all_leads + qualified)

        except Exception as e:
            logger.error(f"Lead finder fallback error: {e}", exc_info=True)
            await slack_bot.send_confirmation(f"Lead finder error: {e}")

    all_leads = all_leads[:15]

    if not all_leads:
        await slack_bot.send_confirmation(
            f"All tiers exhausted for *{conv_lead_name}*. "
            f"0 qualified leads found."
        )
        return

    # Create Google Sheet and post to Slack
    sheet_url = None
    sheets_svc = get_google_sheets_service()
    if sheets_svc:
        try:
            sheet_url = sheets_svc.create_gift_leads_sheet(
                prospect_name=conv_lead_name,
                leads=all_leads,
            )
        except Exception as e:
            logger.error(f"Failed to create Google Sheet: {e}", exc_info=True)

    if sheet_url:
        draft_dm = (
            f"{icp_text} showing high intent signals\n\n"
            f"{sheet_url}\n\n"
            f"Will be valuable for you\n\n"
            f"Oh yeah I included the LinkedIn profile links in the spreadsheet. "
            f"Rather than emails etc. Is LI your main way to reach out to "
            f"potential clients? Or more through warm network/word of mouth"
        )
    else:
        draft_dm = (
            f"{icp_text} showing high intent signals\n\n"
            f"Will be valuable for you - let me know if you'd like the list!"
        )

    await slack_bot.send_gift_leads_ready(
        prospect_id=prospect_id,
        prospect_name=conv_lead_name,
        lead_count=len(all_leads),
        icp=", ".join(keyword_list),
        context="Tiered fallback (pipeline + lead finder)",
        draft_dm=draft_dm,
        sheet_url=sheet_url,
        conversation_id=conv_id if not prospect_id else None,
    )


def _normalize_pipeline_leads(pipeline_leads: list[dict]) -> list[dict]:
    """Normalize pipeline lead format to match DB pool format."""
    normalized = []
    for lead in pipeline_leads:
        normalized.append({
            "full_name": lead.get("full_name") or lead.get("fullName", ""),
            "job_title": lead.get("job_title") or lead.get("jobTitle", ""),
            "company_name": lead.get("company_name") or lead.get("companyName", ""),
            "location": lead.get("location") or lead.get("addressWithCountry", ""),
            "headline": lead.get("headline", ""),
            "activity_score": lead.get("activity_score", 0),
            "linkedin_url": lead.get("linkedin_url") or lead.get("linkedinUrl", ""),
        })
    return normalized


def _dedup_leads(leads: list[dict]) -> list[dict]:
    """Deduplicate leads by linkedin_url, keeping first occurrence."""
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for lead in leads:
        url = (lead.get("linkedin_url") or "").rstrip("/").lower()
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        unique.append(lead)
    return unique


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
