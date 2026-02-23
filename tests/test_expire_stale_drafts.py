"""Tests for stale draft expiry: admin endpoint + scheduled task."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Draft, DraftStatus, MessageDirection, MessageLog, ReplyClassification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conversation(session: AsyncSession, **kwargs) -> Conversation:
    defaults = {
        "heyreach_lead_id": str(uuid.uuid4()),
        "linkedin_profile_url": f"https://linkedin.com/in/{uuid.uuid4().hex[:8]}",
        "lead_name": "Test Lead",
    }
    defaults.update(kwargs)
    conv = Conversation(**defaults)
    session.add(conv)
    return conv


def _make_draft(session: AsyncSession, conv: Conversation, **kwargs) -> Draft:
    defaults = {
        "conversation_id": conv.id,
        "status": DraftStatus.PENDING,
        "ai_draft": "Test draft reply",
        "slack_message_ts": "1234567890.123456",
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    draft = Draft(**defaults)
    session.add(draft)
    return draft


# ===========================================================================
# Admin endpoint tests
# ===========================================================================

class TestExpireStaleDraftsEndpoint:
    @pytest.mark.asyncio
    async def test_requires_auth(self, test_client):
        response = await test_client.post("/admin/expire-stale-drafts")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_expires_old_pending_drafts(self, test_client):
        """Drafts older than 7 days should be expired."""
        response = await test_client.post(
            "/admin/expire-stale-drafts",
            headers={"Authorization": "Bearer test_secret_key_for_testing"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "expired_count" in data
        assert "older_than_days" in data
        assert data["older_than_days"] == 7

    @pytest.mark.asyncio
    async def test_respects_older_than_days_param(self, test_client):
        """Custom older_than_days should be used."""
        response = await test_client.post(
            "/admin/expire-stale-drafts?older_than_days=3",
            headers={"Authorization": "Bearer test_secret_key_for_testing"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["older_than_days"] == 3


# ===========================================================================
# Scheduled task function tests
# ===========================================================================

class TestExpireStaleDraftsTask:
    @pytest.mark.asyncio
    async def test_expires_old_pending(self, test_db_session: AsyncSession):
        """PENDING drafts older than 7 days should be expired."""
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()

        # Old pending draft (10 days old)
        old_draft = _make_draft(
            test_db_session, conv,
            status=DraftStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        await test_db_session.commit()
        old_draft_id = old_draft.id

        from app.services.scheduler import expire_stale_drafts_task
        await expire_stale_drafts_task(test_db_session)

        await test_db_session.refresh(old_draft)
        assert old_draft.status == DraftStatus.REJECTED

    @pytest.mark.asyncio
    async def test_leaves_recent_pending(self, test_db_session: AsyncSession):
        """PENDING drafts less than 7 days old should NOT be expired."""
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()

        recent_draft = _make_draft(
            test_db_session, conv,
            status=DraftStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        await test_db_session.commit()

        from app.services.scheduler import expire_stale_drafts_task
        await expire_stale_drafts_task(test_db_session)

        await test_db_session.refresh(recent_draft)
        assert recent_draft.status == DraftStatus.PENDING

    @pytest.mark.asyncio
    async def test_leaves_approved_and_rejected(self, test_db_session: AsyncSession):
        """APPROVED and REJECTED drafts should not be touched."""
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()

        approved = _make_draft(
            test_db_session, conv,
            status=DraftStatus.APPROVED,
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        rejected = _make_draft(
            test_db_session, conv,
            status=DraftStatus.REJECTED,
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        await test_db_session.commit()

        from app.services.scheduler import expire_stale_drafts_task
        await expire_stale_drafts_task(test_db_session)

        await test_db_session.refresh(approved)
        await test_db_session.refresh(rejected)
        assert approved.status == DraftStatus.APPROVED
        assert rejected.status == DraftStatus.REJECTED

    @pytest.mark.asyncio
    async def test_expires_classified_pending(self, test_db_session: AsyncSession):
        """PENDING drafts with a classification set should be expired."""
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()

        classified_draft = _make_draft(
            test_db_session, conv,
            status=DraftStatus.PENDING,
            classification=ReplyClassification.NOT_INTERESTED,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        await test_db_session.commit()

        from app.services.scheduler import expire_stale_drafts_task
        await expire_stale_drafts_task(test_db_session)

        await test_db_session.refresh(classified_draft)
        assert classified_draft.status == DraftStatus.REJECTED

    @pytest.mark.asyncio
    async def test_expires_superseded_by_outbound(self, test_db_session: AsyncSession):
        """PENDING drafts superseded by a later outbound message should be expired."""
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()

        draft = _make_draft(
            test_db_session, conv,
            status=DraftStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(hours=12),
        )
        # Outbound message sent AFTER the draft was created
        msg = MessageLog(
            conversation_id=conv.id,
            direction=MessageDirection.OUTBOUND,
            content="Sent reply",
            sent_at=datetime.now(timezone.utc) - timedelta(hours=6),
        )
        test_db_session.add(msg)
        await test_db_session.commit()

        from app.services.scheduler import expire_stale_drafts_task
        await expire_stale_drafts_task(test_db_session)

        await test_db_session.refresh(draft)
        assert draft.status == DraftStatus.REJECTED

    @pytest.mark.asyncio
    async def test_keeps_pending_without_outbound(self, test_db_session: AsyncSession):
        """PENDING draft should stay if no outbound was sent after it."""
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()

        draft = _make_draft(
            test_db_session, conv,
            status=DraftStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        await test_db_session.commit()

        from app.services.scheduler import expire_stale_drafts_task
        await expire_stale_drafts_task(test_db_session)

        await test_db_session.refresh(draft)
        assert draft.status == DraftStatus.PENDING
