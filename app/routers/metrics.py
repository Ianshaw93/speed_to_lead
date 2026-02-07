"""Metrics API router for classification and ICP feedback data."""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Draft, ICPFeedback, ReplyClassification

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
