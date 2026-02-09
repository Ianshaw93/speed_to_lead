"""Reports service for aggregating metrics across all projects."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Conversation,
    DailyMetrics,
    Draft,
    DraftStatus,
    FunnelStage,
    MessageDirection,
    MessageLog,
    Prospect,
    ReplyClassification,
)


def format_minutes(minutes: int | None) -> str:
    """Format minutes as a human-readable duration string.

    Args:
        minutes: Number of minutes, or None.

    Returns:
        Formatted string like "2h 15m", "45m", "3h", or "N/A" if None.
    """
    if minutes is None:
        return "N/A"

    if minutes == 0:
        return "0m"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    if hours == 0:
        return f"{remaining_minutes}m"
    elif remaining_minutes == 0:
        return f"{hours}h"
    else:
        return f"{hours}h {remaining_minutes}m"


async def calculate_speed_to_lead(
    session: AsyncSession,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, int] | None:
    """Calculate average time from outreach to first reply.

    Speed to Lead = time from Prospect.heyreach_uploaded_at to first INBOUND message.

    Args:
        session: Database session.
        start_date: Start of the period (inclusive).
        end_date: End of the period (exclusive).

    Returns:
        Dict with 'avg_minutes' and 'count', or None if no data.
    """
    # Subquery to find the first inbound message per conversation
    first_inbound_subq = (
        select(
            MessageLog.conversation_id,
            func.min(MessageLog.sent_at).label("first_inbound_at"),
        )
        .where(MessageLog.direction == MessageDirection.INBOUND)
        .group_by(MessageLog.conversation_id)
        .subquery()
    )

    # Query to get first inbound messages in date range with prospect info
    # Join path: first_inbound -> Conversation -> Prospect (via linkedin_profile_url)
    query = (
        select(
            first_inbound_subq.c.first_inbound_at,
            Prospect.heyreach_uploaded_at,
        )
        .select_from(first_inbound_subq)
        .join(
            Conversation,
            Conversation.id == first_inbound_subq.c.conversation_id,
        )
        .join(
            Prospect,
            func.lower(func.trim(func.replace(Prospect.linkedin_url, "www.", "")))
            == func.lower(func.trim(func.replace(Conversation.linkedin_profile_url, "www.", ""))),
        )
        .where(
            first_inbound_subq.c.first_inbound_at >= start_date,
            first_inbound_subq.c.first_inbound_at < end_date,
            Prospect.heyreach_uploaded_at.isnot(None),
        )
    )

    result = await session.execute(query)
    rows = result.all()

    if not rows:
        return None

    # Calculate average time delta in minutes
    total_minutes = 0
    count = 0
    for first_inbound_at, heyreach_uploaded_at in rows:
        if first_inbound_at and heyreach_uploaded_at:
            # Handle timezone-aware and naive datetimes
            if first_inbound_at.tzinfo is None:
                first_inbound_at = first_inbound_at.replace(tzinfo=timezone.utc)
            if heyreach_uploaded_at.tzinfo is None:
                heyreach_uploaded_at = heyreach_uploaded_at.replace(tzinfo=timezone.utc)

            delta = first_inbound_at - heyreach_uploaded_at
            minutes = int(delta.total_seconds() / 60)
            if minutes >= 0:  # Only count positive deltas
                total_minutes += minutes
                count += 1

    if count == 0:
        return None

    return {
        "avg_minutes": total_minutes // count,
        "count": count,
    }


async def calculate_speed_to_reply(
    session: AsyncSession,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, int] | None:
    """Calculate average time from prospect message to our response.

    Speed to Reply = time from INBOUND message to next OUTBOUND message in same conversation.

    Args:
        session: Database session.
        start_date: Start of the period (inclusive).
        end_date: End of the period (exclusive).

    Returns:
        Dict with 'avg_minutes' and 'count', or None if no data.
    """
    # Get all inbound messages in date range
    inbound_query = (
        select(MessageLog)
        .where(
            MessageLog.direction == MessageDirection.INBOUND,
            MessageLog.sent_at >= start_date,
            MessageLog.sent_at < end_date,
        )
        .order_by(MessageLog.conversation_id, MessageLog.sent_at)
    )
    inbound_result = await session.execute(inbound_query)
    inbound_messages = inbound_result.scalars().all()

    if not inbound_messages:
        return None

    # For each inbound message, find the next outbound message in the same conversation
    total_minutes = 0
    count = 0

    for inbound in inbound_messages:
        # Find next outbound message after this inbound in the same conversation
        outbound_query = (
            select(MessageLog)
            .where(
                MessageLog.conversation_id == inbound.conversation_id,
                MessageLog.direction == MessageDirection.OUTBOUND,
                MessageLog.sent_at > inbound.sent_at,
            )
            .order_by(MessageLog.sent_at)
            .limit(1)
        )
        outbound_result = await session.execute(outbound_query)
        outbound = outbound_result.scalar_one_or_none()

        if outbound:
            # Handle timezone-aware and naive datetimes
            inbound_sent = inbound.sent_at
            outbound_sent = outbound.sent_at

            if inbound_sent.tzinfo is None:
                inbound_sent = inbound_sent.replace(tzinfo=timezone.utc)
            if outbound_sent.tzinfo is None:
                outbound_sent = outbound_sent.replace(tzinfo=timezone.utc)

            delta = outbound_sent - inbound_sent
            minutes = int(delta.total_seconds() / 60)
            if minutes >= 0:
                total_minutes += minutes
                count += 1

    if count == 0:
        return None

    return {
        "avg_minutes": total_minutes // count,
        "count": count,
    }


async def get_or_create_daily_metrics(
    session: AsyncSession,
    target_date: date,
) -> DailyMetrics:
    """Get or create a DailyMetrics record for the given date.

    Args:
        session: Database session.
        target_date: The date to get/create metrics for.

    Returns:
        The DailyMetrics record.
    """
    result = await session.execute(
        select(DailyMetrics).where(DailyMetrics.date == target_date)
    )
    metrics = result.scalar_one_or_none()

    if not metrics:
        metrics = DailyMetrics(date=target_date)
        session.add(metrics)
        await session.flush()

    return metrics


async def upsert_multichannel_metrics(
    session: AsyncSession,
    target_date: date,
    posts_scraped: int = 0,
    profiles_scraped: int = 0,
    icp_qualified: int = 0,
    heyreach_uploaded: int = 0,
    apify_cost: Decimal = Decimal("0"),
    deepseek_cost: Decimal = Decimal("0"),
) -> DailyMetrics:
    """Update or insert multichannel-outreach metrics.

    Adds to existing values (incremental updates).

    Args:
        session: Database session.
        target_date: The date to update.
        posts_scraped: Number of posts scraped.
        profiles_scraped: Number of profiles scraped.
        icp_qualified: Number of ICP-qualified prospects.
        heyreach_uploaded: Number uploaded to HeyReach.
        apify_cost: Apify cost incurred.
        deepseek_cost: DeepSeek cost incurred.

    Returns:
        The updated DailyMetrics record.
    """
    metrics = await get_or_create_daily_metrics(session, target_date)

    metrics.posts_scraped += posts_scraped
    metrics.profiles_scraped += profiles_scraped
    metrics.icp_qualified += icp_qualified
    metrics.heyreach_uploaded += heyreach_uploaded
    metrics.apify_cost += apify_cost
    metrics.deepseek_cost += deepseek_cost
    metrics.updated_at = datetime.now(timezone.utc)

    return metrics


async def upsert_content_metrics(
    session: AsyncSession,
    target_date: date,
    drafts_created: int = 0,
    drafts_scheduled: int = 0,
    drafts_posted: int = 0,
    hooks_generated: int = 0,
    ideas_added: int = 0,
) -> DailyMetrics:
    """Update or insert contentCreator metrics.

    Adds to existing values (incremental updates).

    Args:
        session: Database session.
        target_date: The date to update.
        drafts_created: Number of content drafts created.
        drafts_scheduled: Number of drafts scheduled.
        drafts_posted: Number of drafts posted.
        hooks_generated: Number of hooks generated.
        ideas_added: Number of ideas added.

    Returns:
        The updated DailyMetrics record.
    """
    metrics = await get_or_create_daily_metrics(session, target_date)

    metrics.content_drafts_created += drafts_created
    metrics.content_drafts_scheduled += drafts_scheduled
    metrics.content_drafts_posted += drafts_posted
    metrics.hooks_generated += hooks_generated
    metrics.ideas_added += ideas_added
    metrics.updated_at = datetime.now(timezone.utc)

    return metrics


async def get_conversation_metrics(
    session: AsyncSession,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """Get conversation metrics from the database for a date range.

    Args:
        session: Database session.
        start_date: Start of the period (inclusive).
        end_date: End of the period (exclusive).

    Returns:
        Dict with conversation metrics.
    """
    # New conversations
    new_convos = await session.execute(
        select(func.count(Conversation.id)).where(
            Conversation.created_at >= start_date,
            Conversation.created_at < end_date,
        )
    )
    new_count = new_convos.scalar() or 0

    # Drafts by status
    drafts_approved = await session.execute(
        select(func.count(Draft.id)).where(
            Draft.updated_at >= start_date,
            Draft.updated_at < end_date,
            Draft.status == DraftStatus.APPROVED,
        )
    )
    approved_count = drafts_approved.scalar() or 0

    drafts_rejected = await session.execute(
        select(func.count(Draft.id)).where(
            Draft.updated_at >= start_date,
            Draft.updated_at < end_date,
            Draft.status == DraftStatus.REJECTED,
        )
    )
    rejected_count = drafts_rejected.scalar() or 0

    # Classifications
    classifications = {}
    for classification in ReplyClassification:
        result = await session.execute(
            select(func.count(Draft.id)).where(
                Draft.classified_at >= start_date,
                Draft.classified_at < end_date,
                Draft.classification == classification,
            )
        )
        classifications[classification.value] = result.scalar() or 0

    return {
        "new": new_count,
        "drafts_approved": approved_count,
        "drafts_rejected": rejected_count,
        "classifications": classifications,
    }


async def get_funnel_metrics(
    session: AsyncSession,
) -> dict[str, int]:
    """Get current funnel stage counts.

    Returns total conversations at each stage (cumulative, not per-period).

    Args:
        session: Database session.

    Returns:
        Dict with stage names and counts.
    """
    funnel = {}
    for stage in FunnelStage:
        result = await session.execute(
            select(func.count(Conversation.id)).where(
                Conversation.funnel_stage == stage,
            )
        )
        funnel[stage.value] = result.scalar() or 0

    return funnel


async def get_daily_dashboard_metrics(
    session: AsyncSession,
    target_date: date,
) -> dict[str, Any]:
    """Get all metrics for a single day dashboard view.

    Args:
        session: Database session.
        target_date: The date to get metrics for.

    Returns:
        Complete metrics dict for dashboard display.
    """
    # Get stored metrics (outreach + content)
    result = await session.execute(
        select(DailyMetrics).where(DailyMetrics.date == target_date)
    )
    daily = result.scalar_one_or_none()

    # Calculate date range for conversation metrics
    start_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)

    # Get conversation metrics
    conversations = await get_conversation_metrics(session, start_dt, end_dt)

    # Get funnel metrics (cumulative)
    funnel = await get_funnel_metrics(session)

    # Get speed metrics
    speed_to_lead = await calculate_speed_to_lead(session, start_dt, end_dt)
    speed_to_reply = await calculate_speed_to_reply(session, start_dt, end_dt)

    return {
        "period": "day",
        "date_range": {
            "start": target_date.isoformat(),
            "end": target_date.isoformat(),
        },
        "outreach": {
            "posts_scraped": daily.posts_scraped if daily else 0,
            "profiles_scraped": daily.profiles_scraped if daily else 0,
            "icp_qualified": daily.icp_qualified if daily else 0,
            "heyreach_uploaded": daily.heyreach_uploaded if daily else 0,
            "costs": {
                "apify": float(daily.apify_cost) if daily else 0.0,
                "deepseek": float(daily.deepseek_cost) if daily else 0.0,
            },
        },
        "conversations": conversations,
        "funnel": funnel,
        "content": {
            "drafts_created": daily.content_drafts_created if daily else 0,
            "drafts_scheduled": daily.content_drafts_scheduled if daily else 0,
            "drafts_posted": daily.content_drafts_posted if daily else 0,
            "hooks_generated": daily.hooks_generated if daily else 0,
            "ideas_added": daily.ideas_added if daily else 0,
        },
        "speed_metrics": {
            "speed_to_lead": speed_to_lead,
            "speed_to_reply": speed_to_reply,
        },
    }


async def get_weekly_dashboard_metrics(
    session: AsyncSession,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Get aggregated metrics for a week.

    Args:
        session: Database session.
        start_date: Start of week (Monday).
        end_date: End of week (Sunday).

    Returns:
        Complete metrics dict for weekly dashboard display.
    """
    # Aggregate stored metrics
    result = await session.execute(
        select(
            func.sum(DailyMetrics.posts_scraped),
            func.sum(DailyMetrics.profiles_scraped),
            func.sum(DailyMetrics.icp_qualified),
            func.sum(DailyMetrics.heyreach_uploaded),
            func.sum(DailyMetrics.apify_cost),
            func.sum(DailyMetrics.deepseek_cost),
            func.sum(DailyMetrics.content_drafts_created),
            func.sum(DailyMetrics.content_drafts_scheduled),
            func.sum(DailyMetrics.content_drafts_posted),
            func.sum(DailyMetrics.hooks_generated),
            func.sum(DailyMetrics.ideas_added),
        ).where(
            DailyMetrics.date >= start_date,
            DailyMetrics.date <= end_date,
        )
    )
    row = result.one()

    # Calculate date range for conversation metrics
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    # Get conversation metrics for the week
    conversations = await get_conversation_metrics(session, start_dt, end_dt)

    # Get funnel metrics (cumulative)
    funnel = await get_funnel_metrics(session)

    # Get speed metrics for the week
    speed_to_lead = await calculate_speed_to_lead(session, start_dt, end_dt)
    speed_to_reply = await calculate_speed_to_reply(session, start_dt, end_dt)

    # Calculate conversion rates
    positive = conversations["classifications"].get("positive", 0)
    total_classified = sum(conversations["classifications"].values())
    positive_rate = (positive / total_classified * 100) if total_classified > 0 else 0

    return {
        "period": "week",
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "outreach": {
            "posts_scraped": row[0] or 0,
            "profiles_scraped": row[1] or 0,
            "icp_qualified": row[2] or 0,
            "icp_rate": round((row[2] or 0) / (row[1] or 1) * 100),
            "heyreach_uploaded": row[3] or 0,
            "costs": {
                "apify": float(row[4] or 0),
                "deepseek": float(row[5] or 0),
                "total": float((row[4] or 0) + (row[5] or 0)),
            },
        },
        "conversations": {
            **conversations,
            "positive_reply_rate": round(positive_rate, 1),
        },
        "funnel": funnel,
        "content": {
            "drafts_created": row[6] or 0,
            "drafts_scheduled": row[7] or 0,
            "drafts_posted": row[8] or 0,
            "hooks_generated": row[9] or 0,
            "ideas_added": row[10] or 0,
        },
        "speed_metrics": {
            "speed_to_lead": speed_to_lead,
            "speed_to_reply": speed_to_reply,
        },
    }
