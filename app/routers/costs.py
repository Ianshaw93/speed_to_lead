"""Costs API router — unified cost ledger for all 3 repos."""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import CostLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/costs", tags=["costs"])


# --- Schemas ---


class CostEntry(BaseModel):
    incurred_at: datetime | None = None
    project: str
    provider: str
    operation: str
    cost_usd: float
    units: int | None = None
    unit_type: str | None = None
    pipeline_run_id: str | None = None
    daily_metrics_id: str | None = None
    note: str | None = None


class CostBatchRequest(BaseModel):
    costs: list[CostEntry]


# --- Endpoints ---


@router.post("")
async def log_costs(body: CostBatchRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Batch-log cost entries. Called by all 3 repos after paid API calls."""
    created = 0
    for entry in body.costs:
        row = CostLog(
            id=uuid.uuid4(),
            incurred_at=entry.incurred_at or datetime.now(timezone.utc),
            project=entry.project,
            provider=entry.provider,
            operation=entry.operation,
            cost_usd=Decimal(str(entry.cost_usd)),
            units=entry.units,
            unit_type=entry.unit_type,
            pipeline_run_id=uuid.UUID(entry.pipeline_run_id) if entry.pipeline_run_id else None,
            daily_metrics_id=uuid.UUID(entry.daily_metrics_id) if entry.daily_metrics_id else None,
            note=entry.note,
        )
        db.add(row)
        created += 1

    await db.commit()
    logger.info(f"Logged {created} cost entries")
    return {"status": "ok", "created": created}


@router.get("")
async def get_costs(
    project: str | None = None,
    provider: str | None = None,
    operation: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Query cost entries with optional filters."""
    query = select(CostLog).order_by(CostLog.incurred_at.desc())

    if project:
        query = query.where(CostLog.project == project)
    if provider:
        query = query.where(CostLog.provider == provider)
    if operation:
        query = query.where(CostLog.operation == operation)
    if since:
        query = query.where(CostLog.incurred_at >= since)
    if until:
        query = query.where(CostLog.incurred_at <= until)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    rows = result.scalars().all()

    return {
        "costs": [
            {
                "id": str(r.id),
                "incurred_at": r.incurred_at.isoformat(),
                "project": r.project,
                "provider": r.provider,
                "operation": r.operation,
                "cost_usd": float(r.cost_usd),
                "units": r.units,
                "unit_type": r.unit_type,
                "pipeline_run_id": str(r.pipeline_run_id) if r.pipeline_run_id else None,
                "note": r.note,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/summary")
async def cost_summary(
    group_by: str = Query(default="provider", pattern="^(project|provider|operation|day)$"),
    project: str | None = None,
    provider: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Aggregated cost totals grouped by project, provider, operation, or day."""
    if group_by == "project":
        group_col = CostLog.project
    elif group_by == "provider":
        group_col = CostLog.provider
    elif group_by == "operation":
        group_col = CostLog.operation
    else:  # day
        group_col = func.date(CostLog.incurred_at)

    query = (
        select(
            group_col.label("group"),
            func.sum(CostLog.cost_usd).label("total_usd"),
            func.count(CostLog.id).label("entries"),
        )
        .group_by(group_col)
        .order_by(func.sum(CostLog.cost_usd).desc())
    )

    if project:
        query = query.where(CostLog.project == project)
    if provider:
        query = query.where(CostLog.provider == provider)
    if since:
        query = query.where(CostLog.incurred_at >= since)
    if until:
        query = query.where(CostLog.incurred_at <= until)

    result = await db.execute(query)
    rows = result.all()

    grand_total = sum(float(r.total_usd) for r in rows)

    return {
        "group_by": group_by,
        "grand_total_usd": grand_total,
        "groups": [
            {
                "name": str(r.group),
                "total_usd": float(r.total_usd),
                "entries": r.entries,
            }
            for r in rows
        ],
    }
