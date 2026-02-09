"""Metrics API router for classification, ICP feedback, and reporting data."""

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import DailyMetrics, Draft, ICPFeedback, ReplyClassification
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
