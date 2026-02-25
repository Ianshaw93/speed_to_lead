"""Clients API router — ex-client info store for re-engagement outreach."""

import logging
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Client, ClientStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/clients", tags=["clients"])


# --- Schemas ---


class ClientCreate(BaseModel):
    name: str
    email: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    status: ClientStatus = ClientStatus.ACTIVE
    case_study_data: dict[str, Any] | None = None
    notes: str | None = None
    prospect_id: str | None = None
    started_at: date | None = None
    ended_at: date | None = None


class ClientUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    status: ClientStatus | None = None
    case_study_data: dict[str, Any] | None = None
    notes: str | None = None
    prospect_id: str | None = None
    started_at: date | None = None
    ended_at: date | None = None


class ClientResponse(BaseModel):
    id: str
    name: str
    email: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    status: str
    case_study_data: dict[str, Any] | None = None
    notes: str | None = None
    prospect_id: str | None = None
    started_at: date | None = None
    ended_at: date | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def _to_response(client: Client) -> dict:
    return {
        "id": str(client.id),
        "name": client.name,
        "email": client.email,
        "linkedin_url": client.linkedin_url,
        "company": client.company,
        "status": client.status.value,
        "case_study_data": client.case_study_data,
        "notes": client.notes,
        "prospect_id": str(client.prospect_id) if client.prospect_id else None,
        "started_at": client.started_at.isoformat() if client.started_at else None,
        "ended_at": client.ended_at.isoformat() if client.ended_at else None,
        "created_at": client.created_at.isoformat(),
        "updated_at": client.updated_at.isoformat(),
    }


# --- Endpoints ---


@router.post("")
async def create_client(body: ClientCreate, db: AsyncSession = Depends(get_db)) -> dict:
    """Add a new client."""
    client = Client(
        id=uuid.uuid4(),
        name=body.name,
        email=body.email,
        linkedin_url=body.linkedin_url,
        company=body.company,
        status=body.status,
        case_study_data=body.case_study_data,
        notes=body.notes,
        prospect_id=uuid.UUID(body.prospect_id) if body.prospect_id else None,
        started_at=body.started_at,
        ended_at=body.ended_at,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return _to_response(client)


@router.get("")
async def list_clients(
    status: ClientStatus | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all clients, optionally filtered by status."""
    stmt = select(Client).order_by(Client.name)
    if status is not None:
        stmt = stmt.where(Client.status == status)
    result = await db.execute(stmt)
    clients = result.scalars().all()
    return [_to_response(c) for c in clients]


@router.get("/{client_id}")
async def get_client(client_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Get a single client's full info."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return _to_response(client)


@router.patch("/{client_id}")
async def update_client(
    client_id: uuid.UUID,
    body: ClientUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update a client (partial update)."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "prospect_id" and value is not None:
            value = uuid.UUID(value)
        setattr(client, field, value)

    await db.commit()
    await db.refresh(client)
    return _to_response(client)
