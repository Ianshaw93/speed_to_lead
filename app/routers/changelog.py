"""Changelog API router for tracking all changes affecting outreach results."""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Changelog, ChangelogCategory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/changelog", tags=["changelog"])


class ChangelogEntry(BaseModel):
    """Schema for creating a changelog entry."""

    timestamp: datetime | None = None  # defaults to now
    category: str
    component: str
    change_type: str  # added, modified, removed
    description: str
    details: dict[str, Any] | None = None
    git_commit: str | None = None


@router.get("")
async def get_changelog(
    category: str | None = Query(None, description="Filter by category"),
    component: str | None = Query(None, description="Filter by component"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get changelog entries, newest first.

    Optional filters: category, component.
    """
    query = select(Changelog).order_by(Changelog.timestamp.desc())

    if category:
        try:
            cat_enum = ChangelogCategory(category)
            query = query.where(Changelog.category == cat_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid category: {category}. Valid: {[c.value for c in ChangelogCategory]}")

    if component:
        query = query.where(Changelog.component.ilike(f"%{component}%"))

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    entries = result.scalars().all()

    return {
        "count": len(entries),
        "entries": [
            {
                "id": str(e.id),
                "timestamp": e.timestamp.isoformat(),
                "category": e.category.value,
                "component": e.component,
                "change_type": e.change_type,
                "description": e.description,
                "details": e.details,
                "git_commit": e.git_commit,
            }
            for e in entries
        ],
    }


@router.get("/categories")
async def get_categories() -> dict[str, Any]:
    """List all valid changelog categories."""
    return {
        "categories": [{"value": c.value, "name": c.name} for c in ChangelogCategory]
    }


@router.post("")
async def create_changelog_entry(
    entry: ChangelogEntry,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new changelog entry."""
    try:
        cat_enum = ChangelogCategory(entry.category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid category: {entry.category}. Valid: {[c.value for c in ChangelogCategory]}")

    ts = entry.timestamp or datetime.now(timezone.utc)

    changelog = Changelog(
        timestamp=ts,
        category=cat_enum,
        component=entry.component,
        change_type=entry.change_type,
        description=entry.description,
        details=entry.details,
        git_commit=entry.git_commit,
    )
    session.add(changelog)
    await session.commit()
    await session.refresh(changelog)

    logger.info(f"Changelog entry created: [{cat_enum.value}] {entry.component} — {entry.change_type}")

    return {
        "id": str(changelog.id),
        "timestamp": changelog.timestamp.isoformat(),
        "category": changelog.category.value,
        "component": changelog.component,
        "change_type": changelog.change_type,
        "description": changelog.description,
    }
