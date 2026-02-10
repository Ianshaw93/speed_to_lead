"""Metrics API router for classification, ICP feedback, and reporting data."""

import csv
import io
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Conversation,
    DailyMetrics,
    Draft,
    ICPFeedback,
    MessageDirection,
    MessageLog,
    Prospect,
    ReplyClassification,
)
from app.services.reports import (
    get_daily_dashboard_metrics,
    get_weekly_dashboard_metrics,
    upsert_content_metrics,
    upsert_multichannel_metrics,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/classifications")
async def get_classifications(
    exclude_followup: bool = Query(False, description="Exclude follow-up drafts"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get classification metrics.

    Returns counts and rates for each classification type.

    Args:
        exclude_followup: If True, exclude drafts that are not first replies.
        session: Database session (injected).

    Returns:
        Dict with total_drafts, classified count, and breakdown by classification.
    """
    # Build base filter condition
    base_filter = []
    if exclude_followup:
        base_filter.append(Draft.is_first_reply == True)

    # Total drafts
    total_query = select(func.count(Draft.id))
    if base_filter:
        total_query = total_query.where(*base_filter)
    total_result = await session.execute(total_query)
    total_drafts = total_result.scalar() or 0

    # Classified drafts
    classified_query = select(func.count(Draft.id)).where(
        Draft.classification.isnot(None),
        *base_filter,
    )
    classified_result = await session.execute(classified_query)
    classified_count = classified_result.scalar() or 0

    # Count by classification
    by_classification = {}
    for classification in ReplyClassification:
        count_query = select(func.count(Draft.id)).where(
            Draft.classification == classification,
            *base_filter,
        )
        result = await session.execute(count_query)
        by_classification[classification.value] = result.scalar() or 0

    # Calculate rates
    classification_rate = (
        f"{(classified_count / total_drafts * 100):.1f}%"
        if total_drafts > 0
        else "0%"
    )

    # First reply specific stats
    first_reply_result = await session.execute(
        select(func.count(Draft.id)).where(Draft.is_first_reply == True)
    )
    first_reply_count = first_reply_result.scalar() or 0

    positive_first_reply_result = await session.execute(
        select(func.count(Draft.id)).where(
            Draft.is_first_reply == True,
            Draft.classification == ReplyClassification.POSITIVE,
        )
    )
    positive_first_reply_count = positive_first_reply_result.scalar() or 0

    positive_rate = (
        f"{(positive_first_reply_count / first_reply_count * 100):.1f}%"
        if first_reply_count > 0
        else "0%"
    )

    return {
        "total_drafts": total_drafts,
        "classified": classified_count,
        "classification_rate": classification_rate,
        "by_classification": by_classification,
        "first_reply_stats": {
            "total_first_replies": first_reply_count,
            "positive_count": positive_first_reply_count,
            "positive_rate": positive_rate,
        },
    }


@router.get("/icp-feedback")
async def get_icp_feedback(
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get ICP feedback records for export to multichannel-outreach.

    Returns a list of prospects marked as Not ICP with their details.

    Args:
        limit: Maximum number of records to return.
        offset: Number of records to skip for pagination.
        session: Database session (injected).

    Returns:
        Dict with feedback list and pagination info.
    """
    # Get total count
    total_result = await session.execute(
        select(func.count(ICPFeedback.id))
    )
    total_count = total_result.scalar() or 0

    # Get feedback records
    result = await session.execute(
        select(ICPFeedback)
        .order_by(ICPFeedback.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    feedback_records = result.scalars().all()

    feedback_list = [
        {
            "id": str(f.id),
            "lead_name": f.lead_name,
            "linkedin_url": f.linkedin_url,
            "job_title": f.job_title,
            "company_name": f.company_name,
            "original_icp_match": f.original_icp_match,
            "original_icp_reason": f.original_icp_reason,
            "notes": f.notes,
            "marked_by_slack_user": f.marked_by_slack_user,
            "draft_id": str(f.draft_id) if f.draft_id else None,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in feedback_records
    ]

    return {
        "feedback": feedback_list,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(feedback_list) < total_count,
    }


@router.get("/summary")
async def get_metrics_summary(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get a summary of all metrics.

    Returns a comprehensive overview of classification and response metrics.

    Args:
        session: Database session (injected).
    """
    # Total drafts
    total_result = await session.execute(select(func.count(Draft.id)))
    total_drafts = total_result.scalar() or 0

    # First replies
    first_reply_result = await session.execute(
        select(func.count(Draft.id)).where(Draft.is_first_reply == True)
    )
    first_reply_count = first_reply_result.scalar() or 0

    # Classification counts
    classification_counts = {}
    for classification in ReplyClassification:
        result = await session.execute(
            select(func.count(Draft.id)).where(
                Draft.classification == classification
            )
        )
        classification_counts[classification.value] = result.scalar() or 0

    # ICP feedback count
    icp_feedback_result = await session.execute(
        select(func.count(ICPFeedback.id))
    )
    icp_feedback_count = icp_feedback_result.scalar() or 0

    return {
        "total_drafts": total_drafts,
        "first_reply_count": first_reply_count,
        "classifications": classification_counts,
        "icp_feedback_records": icp_feedback_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# INGESTION ENDPOINTS - Called by external projects
# =============================================================================


class MultichannelMetricsPayload(BaseModel):
    """Payload for multichannel-outreach metrics ingestion."""

    metric_date: date | None = None  # Defaults to today
    posts_scraped: int = 0
    profiles_scraped: int = 0
    icp_qualified: int = 0
    heyreach_uploaded: int = 0
    apify_cost: float = 0.0
    deepseek_cost: float = 0.0


class ContentMetricsPayload(BaseModel):
    """Payload for contentCreator metrics ingestion."""

    metric_date: date | None = None  # Defaults to today
    drafts_created: int = 0
    drafts_scheduled: int = 0
    drafts_posted: int = 0
    hooks_generated: int = 0
    ideas_added: int = 0


@router.post("/multichannel")
async def ingest_multichannel_metrics(
    payload: MultichannelMetricsPayload,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ingest metrics from multichannel-outreach project.

    Called after pipeline runs to record activity metrics.

    Args:
        payload: The metrics data.
        session: Database session (injected).

    Returns:
        Confirmation with updated totals.
    """
    target_date = payload.metric_date or date.today()

    metrics = await upsert_multichannel_metrics(
        session=session,
        target_date=target_date,
        posts_scraped=payload.posts_scraped,
        profiles_scraped=payload.profiles_scraped,
        icp_qualified=payload.icp_qualified,
        heyreach_uploaded=payload.heyreach_uploaded,
        apify_cost=Decimal(str(payload.apify_cost)),
        deepseek_cost=Decimal(str(payload.deepseek_cost)),
    )
    await session.commit()

    logger.info(
        f"Ingested multichannel metrics for {target_date}: "
        f"profiles={payload.profiles_scraped}, icp={payload.icp_qualified}"
    )

    return {
        "status": "ok",
        "date": target_date.isoformat(),
        "totals": {
            "posts_scraped": metrics.posts_scraped,
            "profiles_scraped": metrics.profiles_scraped,
            "icp_qualified": metrics.icp_qualified,
            "heyreach_uploaded": metrics.heyreach_uploaded,
            "apify_cost": float(metrics.apify_cost),
            "deepseek_cost": float(metrics.deepseek_cost),
        },
    }


@router.post("/content")
async def ingest_content_metrics(
    payload: ContentMetricsPayload,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ingest metrics from contentCreator project.

    Called after content creation activities.

    Args:
        payload: The metrics data.
        session: Database session (injected).

    Returns:
        Confirmation with updated totals.
    """
    target_date = payload.metric_date or date.today()

    metrics = await upsert_content_metrics(
        session=session,
        target_date=target_date,
        drafts_created=payload.drafts_created,
        drafts_scheduled=payload.drafts_scheduled,
        drafts_posted=payload.drafts_posted,
        hooks_generated=payload.hooks_generated,
        ideas_added=payload.ideas_added,
    )
    await session.commit()

    logger.info(
        f"Ingested content metrics for {target_date}: "
        f"drafts={payload.drafts_created}, hooks={payload.hooks_generated}"
    )

    return {
        "status": "ok",
        "date": target_date.isoformat(),
        "totals": {
            "drafts_created": metrics.content_drafts_created,
            "drafts_scheduled": metrics.content_drafts_scheduled,
            "drafts_posted": metrics.content_drafts_posted,
            "hooks_generated": metrics.hooks_generated,
            "ideas_added": metrics.ideas_added,
        },
    }


# =============================================================================
# DASHBOARD ENDPOINT - Unified metrics view
# =============================================================================


@router.get("/dashboard")
async def get_dashboard(
    period: Literal["today", "yesterday", "week", "month"] = Query(
        "today", description="Time period for metrics"
    ),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get unified dashboard metrics.

    Combines data from all three projects for a complete view.

    Args:
        period: Time period - 'today', 'yesterday', 'week', or 'month'.
        session: Database session (injected).

    Returns:
        Complete metrics for the specified period.
    """
    today = date.today()

    if period == "today":
        return await get_daily_dashboard_metrics(session, today)
    elif period == "yesterday":
        return await get_daily_dashboard_metrics(session, today - timedelta(days=1))
    elif period == "week":
        # Monday to Sunday (previous complete week if today is Monday, otherwise current week)
        days_since_monday = today.weekday()
        start_of_week = today - timedelta(days=days_since_monday)
        end_of_week = start_of_week + timedelta(days=6)
        return await get_weekly_dashboard_metrics(session, start_of_week, end_of_week)
    elif period == "month":
        # First day of current month to today
        start_of_month = today.replace(day=1)
        return await get_weekly_dashboard_metrics(session, start_of_month, today)

    # Shouldn't reach here due to Literal type
    return await get_daily_dashboard_metrics(session, today)


# =============================================================================
# PITCHED SEARCH - Find conversations where we pitched
# =============================================================================


@router.get("/pitched")
async def find_pitched_conversations(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Find conversations where we've pitched (sent booking invite).

    Searches OUTBOUND messages in MessageLog for pitch phrases.
    MessageLog reliably tracks direction (outbound = our messages).
    """
    import re

    pitch_patterns = [
        r"i'?d be open to",
        r"sometime",
        r"jump on a call",
        r"schedule a",
        r"book a",
        r"calendly",
        r"let'?s chat",
    ]
    combined_pattern = re.compile("|".join(pitch_patterns), re.IGNORECASE)

    pitched = []
    seen_convos = set()

    # Search MessageLog for OUTBOUND messages only (reliable direction tracking)
    msg_result = await session.execute(
        select(MessageLog, Conversation)
        .join(Conversation, MessageLog.conversation_id == Conversation.id)
        .where(MessageLog.direction == MessageDirection.OUTBOUND)
    )
    message_rows = msg_result.all()

    for msg_log, convo in message_rows:
        if convo.linkedin_profile_url in seen_convos:
            continue
        if combined_pattern.search(msg_log.content):
            pitched.append({
                "name": convo.lead_name,
                "linkedin_url": convo.linkedin_profile_url,
                "pitch_snippet": msg_log.content[:200] + "..." if len(msg_log.content) > 200 else msg_log.content,
            })
            seen_convos.add(convo.linkedin_profile_url)

    return {
        "count": len(pitched),
        "pitched": pitched,
    }


# =============================================================================
# FUNNEL SUMMARY - Full pipeline metrics
# =============================================================================


@router.get("/funnel")
async def get_funnel_summary(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get full funnel metrics from initial outreach to booked meetings.

    Returns counts at each stage of the sales funnel.
    """
    from app.models import FunnelStage

    # Initial messages sent (prospects uploaded to HeyReach)
    initial_sent_result = await session.execute(
        select(func.count(Prospect.id)).where(
            Prospect.heyreach_uploaded_at.isnot(None)
        )
    )
    initial_sent = initial_sent_result.scalar() or 0

    # Positive replies (from positive_reply_at field)
    positive_replies_result = await session.execute(
        select(func.count(Prospect.id)).where(
            Prospect.positive_reply_at.isnot(None)
        )
    )
    positive_replies = positive_replies_result.scalar() or 0

    # Also count from positive_reply_notes (backfilled without timestamp)
    positive_notes_result = await session.execute(
        select(func.count(Prospect.id)).where(
            Prospect.positive_reply_at.is_(None),
            Prospect.positive_reply_notes.isnot(None),
        )
    )
    positive_from_notes = positive_notes_result.scalar() or 0

    # Pitched (from Prospect.pitched_at)
    pitched_result = await session.execute(
        select(func.count(Prospect.id)).where(
            Prospect.pitched_at.isnot(None)
        )
    )
    pitched = pitched_result.scalar() or 0

    # Booked (from Prospect.booked_at)
    booked_result = await session.execute(
        select(func.count(Prospect.id)).where(
            Prospect.booked_at.isnot(None)
        )
    )
    booked = booked_result.scalar() or 0

    # Calendar sent (pitched but not booked yet - for now same as pitched)
    calendar_sent = pitched  # Will refine later if needed

    # Calculate conversion rates
    total_positive = positive_replies + positive_from_notes

    return {
        "funnel": {
            "initial_msgs_sent": initial_sent,
            "positive_replies": total_positive,
            "pitched": pitched,
            "calendar_sent": calendar_sent,
            "booked": booked,
        },
        "conversion_rates": {
            "reply_rate": f"{(total_positive / initial_sent * 100):.1f}%" if initial_sent > 0 else "N/A",
            "pitch_rate": f"{(pitched / total_positive * 100):.1f}%" if total_positive > 0 else "N/A",
            "calendar_rate": f"{(calendar_sent / pitched * 100):.1f}%" if pitched > 0 else "N/A",
            "book_rate": f"{(booked / calendar_sent * 100):.1f}%" if calendar_sent > 0 else "N/A",
            "overall": f"{(booked / initial_sent * 100):.2f}%" if initial_sent > 0 else "N/A",
        },
        "details": {
            "positive_with_timestamp": positive_replies,
            "positive_from_backfill": positive_from_notes,
        }
    }


# =============================================================================
# BACKFILL ENDPOINT - For importing historical positive replies
# =============================================================================


def normalize_linkedin_url(url: str) -> str:
    """Normalize LinkedIn URL for consistent matching."""
    if not url:
        return ""
    url = url.lower().strip().rstrip("/")
    if "?" in url:
        url = url.split("?")[0]
    return url


def parse_icp_match(notes: str) -> bool | None:
    """Parse ICP match from notes field.

    Returns:
        True: "yes" variants
        False: "no", "not icp", "employee", "no traction" variants
        None: unclear/empty
    """
    if not notes:
        return None

    notes_lower = notes.lower().strip()

    # Explicitly not ICP
    not_icp_indicators = [
        "not icp",
        "no - ",
        "no -",
        "employee",
        "not decision maker",
        "no traction",
        "between roles",
        "blank profile",
    ]
    for indicator in not_icp_indicators:
        if indicator in notes_lower:
            return False

    # Explicitly ICP / positive
    if notes_lower.startswith("yes"):
        return True

    # Other indicators of positive
    positive_indicators = [
        "hot prospect",
        "needs reply",
        "just reply",
        "follow up",
    ]
    for indicator in positive_indicators:
        if indicator in notes_lower:
            return True

    # Unclear
    if "unclear" in notes_lower:
        return None

    return None


async def get_first_reply_timestamp(
    session: AsyncSession, conversation_id
) -> datetime | None:
    """Get timestamp of first inbound message in a conversation."""
    if not conversation_id:
        return None

    # Query MessageLog for first inbound message
    result = await session.execute(
        select(MessageLog)
        .where(MessageLog.conversation_id == conversation_id)
        .where(MessageLog.direction == MessageDirection.INBOUND)
        .order_by(MessageLog.sent_at.asc())
        .limit(1)
    )
    message = result.scalar_one_or_none()

    if message:
        return message.sent_at

    # Fallback: check conversation_history JSON if no MessageLog
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    convo = result.scalar_one_or_none()

    if convo and convo.conversation_history:
        for msg in convo.conversation_history:
            # Look for inbound messages in history
            if msg.get("direction") == "inbound" or msg.get("isInbound"):
                timestamp = msg.get("timestamp") or msg.get("sent_at")
                if timestamp:
                    if isinstance(timestamp, str):
                        try:
                            return datetime.fromisoformat(
                                timestamp.replace("Z", "+00:00")
                            )
                        except ValueError:
                            pass
                    elif isinstance(timestamp, datetime):
                        return timestamp

    return None


class PositiveReplyRow(BaseModel):
    """A row from the positive replies CSV."""

    linkedin_url: str
    notes: str | None = None


class BackfillPositiveRepliesPayload(BaseModel):
    """Payload for backfilling positive replies."""

    csv_data: str  # Raw CSV content
    create_missing: bool = False  # Create prospects if not found


@router.post("/backfill/positive-replies")
async def backfill_positive_replies(
    payload: BackfillPositiveRepliesPayload,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Backfill positive reply data from CSV.

    Expects CSV with columns: Name, Last Name, LI Profile, Follow up needed?

    Args:
        payload: Contains CSV data as a string.
        session: Database session (injected).

    Returns:
        Summary of backfill results.
    """
    from app.models import ProspectSource

    # Parse CSV
    csv_file = io.StringIO(payload.csv_data)
    reader = csv.DictReader(csv_file)
    rows = list(reader)

    logger.info(f"Backfill: Processing {len(rows)} rows from CSV")

    updated = 0
    created = 0
    not_found = 0
    already_set = 0
    results = []

    for row in rows:
        linkedin_url = normalize_linkedin_url(row.get("LI Profile", ""))
        if not linkedin_url:
            continue

        notes = row.get("Follow up needed?", "").strip()
        first_name = row.get("Name", "").strip()
        last_name = row.get("Last Name", "").strip()

        # Find prospect
        result = await session.execute(
            select(Prospect).where(Prospect.linkedin_url == linkedin_url)
        )
        prospect = result.scalar_one_or_none()

        if not prospect:
            if payload.create_missing:
                # Create new prospect
                full_name = f"{first_name} {last_name}".strip() or None
                prospect = Prospect(
                    linkedin_url=linkedin_url,
                    full_name=full_name,
                    first_name=first_name or None,
                    last_name=last_name or None,
                    source_type=ProspectSource.MANUAL,
                    icp_match=parse_icp_match(notes),
                    positive_reply_notes=notes if notes else None,
                )
                session.add(prospect)
                created += 1
                results.append({
                    "status": "created",
                    "name": full_name,
                    "url": linkedin_url,
                    "icp_match": prospect.icp_match,
                })
                continue
            else:
                name = f"{first_name} {last_name}".strip()
                results.append({"status": "not_found", "name": name, "url": linkedin_url})
                not_found += 1
                continue

        # Skip if already has positive_reply_at set
        if prospect.positive_reply_at:
            already_set += 1
            continue

        # Get first reply timestamp from conversation if linked
        reply_at = None
        if prospect.conversation_id:
            reply_at = await get_first_reply_timestamp(session, prospect.conversation_id)

        # Update prospect
        prospect.positive_reply_at = reply_at  # Will be None if no timestamp found
        prospect.positive_reply_notes = notes if notes else None

        # Update ICP match if not already set
        if prospect.icp_match is None:
            prospect.icp_match = parse_icp_match(notes)

        updated += 1

        name = (
            prospect.full_name
            or f"{prospect.first_name or ''} {prospect.last_name or ''}".strip()
        )
        results.append({
            "status": "updated",
            "name": name,
            "url": linkedin_url,
            "reply_at": reply_at.isoformat() if reply_at else None,
            "icp_match": prospect.icp_match,
        })

    await session.commit()

    logger.info(
        f"Backfill complete: {updated} updated, {created} created, "
        f"{not_found} not found, {already_set} already set"
    )

    return {
        "status": "ok",
        "summary": {
            "updated": updated,
            "created": created,
            "not_found": not_found,
            "already_set": already_set,
            "total_rows": len(rows),
        },
        "details": results,
    }


@router.post("/run-migration-012")
async def run_migration_012(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Manually run migration 012 to add pitched_at and booked_at columns."""
    from sqlalchemy import text

    # Check if columns already exist
    result = await session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'prospects' AND column_name = 'pitched_at'"
    ))
    if result.fetchone():
        return {"status": "ok", "message": "Columns already exist"}

    # Add columns
    await session.execute(text(
        "ALTER TABLE prospects ADD COLUMN pitched_at TIMESTAMP WITH TIME ZONE"
    ))
    await session.execute(text(
        "ALTER TABLE prospects ADD COLUMN booked_at TIMESTAMP WITH TIME ZONE"
    ))
    await session.commit()

    return {"status": "ok", "message": "Columns added successfully"}


class FunnelStagePayload(BaseModel):
    """Payload for updating funnel stages."""

    pitched: list[str] = []  # LinkedIn URLs of pitched prospects
    booked: list[str] = []  # LinkedIn URLs of booked prospects


@router.post("/backfill/funnel-stages")
async def backfill_funnel_stages(
    payload: FunnelStagePayload,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Backfill funnel stage data (pitched_at, booked_at).

    Args:
        payload: Lists of LinkedIn URLs for each stage.
        session: Database session (injected).

    Returns:
        Summary of updates.
    """
    from datetime import datetime, timezone

    results = {"pitched": [], "booked": [], "not_found": []}

    # Process pitched prospects
    for url in payload.pitched:
        normalized = normalize_linkedin_url(url)
        result = await session.execute(
            select(Prospect).where(Prospect.linkedin_url == normalized)
        )
        prospect = result.scalar_one_or_none()

        if prospect:
            if not prospect.pitched_at:
                prospect.pitched_at = datetime.now(timezone.utc)
            results["pitched"].append(prospect.full_name or normalized)
        else:
            results["not_found"].append(url)

    # Process booked prospects
    for url in payload.booked:
        normalized = normalize_linkedin_url(url)
        result = await session.execute(
            select(Prospect).where(Prospect.linkedin_url == normalized)
        )
        prospect = result.scalar_one_or_none()

        if prospect:
            if not prospect.pitched_at:
                prospect.pitched_at = datetime.now(timezone.utc)
            if not prospect.booked_at:
                prospect.booked_at = datetime.now(timezone.utc)
            results["booked"].append(prospect.full_name or normalized)
        else:
            results["not_found"].append(url)

    await session.commit()

    logger.info(
        f"Funnel backfill: {len(results['pitched'])} pitched, "
        f"{len(results['booked'])} booked, {len(results['not_found'])} not found"
    )

    return {
        "status": "ok",
        "summary": {
            "pitched": len(results["pitched"]),
            "booked": len(results["booked"]),
            "not_found": len(results["not_found"]),
        },
        "details": results,
    }
