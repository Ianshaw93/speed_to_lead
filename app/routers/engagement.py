"""Engagement API router for managing watched profiles and engagement posts."""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    EngagementPost,
    EngagementPostStatus,
    WatchedProfile,
    WatchedProfileCategory,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/engagement", tags=["engagement"])


# --- Schemas ---


class WatchlistCreateRequest(BaseModel):
    linkedin_url: str
    name: str
    headline: str | None = None
    category: WatchedProfileCategory = WatchedProfileCategory.PROSPECT
    notes: str | None = None


class WatchlistUpdateRequest(BaseModel):
    name: str | None = None
    headline: str | None = None
    category: WatchedProfileCategory | None = None
    is_active: bool | None = None
    notes: str | None = None


# --- Watchlist CRUD ---


@router.get("/watchlist")
async def list_watchlist(
    category: WatchedProfileCategory | None = None,
    active_only: bool = True,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List watched profiles with optional filtering."""
    query = select(WatchedProfile)

    if category:
        query = query.where(WatchedProfile.category == category)
    if active_only:
        query = query.where(WatchedProfile.is_active == True)

    query = query.order_by(WatchedProfile.name)

    result = await session.execute(query)
    profiles = result.scalars().all()

    return {
        "profiles": [
            {
                "id": str(p.id),
                "linkedin_url": p.linkedin_url,
                "name": p.name,
                "headline": p.headline,
                "category": p.category.value,
                "is_active": p.is_active,
                "last_checked_at": p.last_checked_at.isoformat() if p.last_checked_at else None,
                "notes": p.notes,
                "created_at": p.created_at.isoformat(),
            }
            for p in profiles
        ],
        "count": len(profiles),
    }


@router.post("/watchlist", status_code=201)
async def add_to_watchlist(
    payload: WatchlistCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Add a profile to the watchlist."""
    # Normalize URL
    linkedin_url = payload.linkedin_url.lower().strip()
    if "?" in linkedin_url:
        linkedin_url = linkedin_url.split("?")[0]
    linkedin_url = linkedin_url.rstrip("/")

    # Check for duplicates
    existing = await session.execute(
        select(WatchedProfile).where(WatchedProfile.linkedin_url == linkedin_url)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Profile already in watchlist")

    profile = WatchedProfile(
        linkedin_url=linkedin_url,
        name=payload.name,
        headline=payload.headline,
        category=payload.category,
        notes=payload.notes,
    )
    session.add(profile)
    await session.commit()

    logger.info(f"Added {payload.name} to watchlist ({payload.category.value})")

    return {
        "status": "created",
        "id": str(profile.id),
        "name": profile.name,
        "category": profile.category.value,
    }


@router.patch("/watchlist/{profile_id}")
async def update_watchlist_profile(
    profile_id: uuid.UUID,
    payload: WatchlistUpdateRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update a watched profile."""
    result = await session.execute(
        select(WatchedProfile).where(WatchedProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if payload.name is not None:
        profile.name = payload.name
    if payload.headline is not None:
        profile.headline = payload.headline
    if payload.category is not None:
        profile.category = payload.category
    if payload.is_active is not None:
        profile.is_active = payload.is_active
    if payload.notes is not None:
        profile.notes = payload.notes

    await session.commit()

    return {"status": "updated", "id": str(profile.id)}


@router.delete("/watchlist/{profile_id}")
async def delete_watchlist_profile(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Soft-delete a watched profile (set is_active=false)."""
    result = await session.execute(
        select(WatchedProfile).where(WatchedProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.is_active = False
    await session.commit()

    return {"status": "deactivated", "id": str(profile.id), "name": profile.name}


# --- Manual Trigger ---


@router.post("/check-now")
async def check_now(
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Manually trigger engagement post check."""
    from app.services.engagement import check_engagement_posts

    background_tasks.add_task(check_engagement_posts)

    return {"status": "started", "message": "Engagement check running in background"}


# --- Posts ---


@router.get("/posts")
async def list_engagement_posts(
    status: EngagementPostStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List engagement posts with optional status filter."""
    query = select(EngagementPost)

    if status:
        query = query.where(EngagementPost.status == status)

    query = query.order_by(EngagementPost.created_at.desc()).limit(limit)

    result = await session.execute(query)
    posts = result.scalars().all()

    return {
        "posts": [
            {
                "id": str(p.id),
                "post_url": p.post_url,
                "post_snippet": p.post_snippet,
                "post_summary": p.post_summary,
                "draft_comment": p.draft_comment,
                "status": p.status.value,
                "created_at": p.created_at.isoformat(),
            }
            for p in posts
        ],
        "count": len(posts),
    }
