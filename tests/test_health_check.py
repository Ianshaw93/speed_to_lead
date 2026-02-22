"""Tests for the production health check system."""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Conversation,
    DailyMetrics,
    Draft,
    DraftStatus,
    MessageDirection,
    MessageLog,
    PipelineRun,
    Prospect,
    ProspectSource,
)
from app.services.health_check import (
    ALL_CHECKS,
    CheckResult,
    CheckStatus,
    HealthCheckReport,
    check_connection_tracking,
    check_daily_metrics,
    check_draft_generation,
    check_inbound_messages,
    check_outbound_messages,
    check_pipeline_runs,
    check_prospect_freshness,
    check_slack_delivery,
    check_stale_pending_drafts,
    run_health_check,
)


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


def _make_message(session: AsyncSession, conv: Conversation, direction: MessageDirection, sent_at: datetime | None = None) -> MessageLog:
    msg = MessageLog(
        conversation_id=conv.id,
        direction=direction,
        content=f"Test message {uuid.uuid4().hex[:6]}",
        sent_at=sent_at or datetime.now(timezone.utc),
    )
    session.add(msg)
    return msg


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


def _make_prospect(session: AsyncSession, **kwargs) -> Prospect:
    defaults = {
        "linkedin_url": f"https://linkedin.com/in/{uuid.uuid4().hex[:8]}",
        "full_name": "Test Prospect",
        "source_type": ProspectSource.OTHER,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    prospect = Prospect(**defaults)
    session.add(prospect)
    return prospect


# ===========================================================================
# Check 1: Inbound messages
# ===========================================================================

class TestCheckInboundMessages:
    @pytest.mark.asyncio
    async def test_ok_recent_messages(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        _make_message(test_db_session, conv, MessageDirection.INBOUND)
        await test_db_session.commit()

        result = await check_inbound_messages(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_no_recent(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        # Message 50h ago (past 48h warning but within 72h critical)
        _make_message(test_db_session, conv, MessageDirection.INBOUND,
                      sent_at=datetime.now(timezone.utc) - timedelta(hours=50))
        await test_db_session.commit()

        result = await check_inbound_messages(test_db_session)
        assert result.status == CheckStatus.WARNING

    @pytest.mark.asyncio
    async def test_critical_none_at_all(self, test_db_session: AsyncSession):
        # No messages at all
        result = await check_inbound_messages(test_db_session)
        assert result.status == CheckStatus.CRITICAL


# ===========================================================================
# Check 2: Outbound messages
# ===========================================================================

class TestCheckOutboundMessages:
    @pytest.mark.asyncio
    async def test_ok_recent_messages(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        _make_message(test_db_session, conv, MessageDirection.OUTBOUND)
        await test_db_session.commit()

        result = await check_outbound_messages(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_no_recent(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        _make_message(test_db_session, conv, MessageDirection.OUTBOUND,
                      sent_at=datetime.now(timezone.utc) - timedelta(hours=40))
        await test_db_session.commit()

        result = await check_outbound_messages(test_db_session)
        assert result.status == CheckStatus.WARNING

    @pytest.mark.asyncio
    async def test_critical_none(self, test_db_session: AsyncSession):
        result = await check_outbound_messages(test_db_session)
        assert result.status == CheckStatus.CRITICAL


# ===========================================================================
# Check 3: Draft generation
# ===========================================================================

class TestCheckDraftGeneration:
    @pytest.mark.asyncio
    async def test_ok_all_have_drafts(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        _make_message(test_db_session, conv, MessageDirection.INBOUND)
        _make_draft(test_db_session, conv)
        await test_db_session.commit()

        result = await check_draft_generation(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_ok_no_recent_inbound(self, test_db_session: AsyncSession):
        # No inbound = nothing to check
        result = await check_draft_generation(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_missing_draft(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        _make_message(test_db_session, conv, MessageDirection.INBOUND)
        # No draft created
        await test_db_session.commit()

        result = await check_draft_generation(test_db_session)
        assert result.status == CheckStatus.WARNING
        assert "1/1" in result.message


# ===========================================================================
# Check 4: Slack delivery
# ===========================================================================

class TestCheckSlackDelivery:
    @pytest.mark.asyncio
    async def test_ok_all_delivered(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        _make_draft(test_db_session, conv, slack_message_ts="12345.67890")
        await test_db_session.commit()

        result = await check_slack_delivery(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_some_missing(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        _make_draft(test_db_session, conv, slack_message_ts="12345.67890")
        _make_draft(test_db_session, conv, slack_message_ts=None)
        await test_db_session.commit()

        result = await check_slack_delivery(test_db_session)
        assert result.status == CheckStatus.WARNING

    @pytest.mark.asyncio
    async def test_critical_all_missing(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        _make_draft(test_db_session, conv, slack_message_ts=None)
        _make_draft(test_db_session, conv, slack_message_ts=None)
        await test_db_session.commit()

        result = await check_slack_delivery(test_db_session)
        assert result.status == CheckStatus.CRITICAL


# ===========================================================================
# Check 5: Stale pending drafts
# ===========================================================================

class TestCheckStalePendingDrafts:
    @pytest.mark.asyncio
    async def test_ok_few_stale(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        # 2 stale drafts (under threshold of 4)
        for _ in range(2):
            _make_draft(test_db_session, conv,
                        status=DraftStatus.PENDING,
                        created_at=datetime.now(timezone.utc) - timedelta(hours=30))
        await test_db_session.commit()

        result = await check_stale_pending_drafts(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_moderate_stale(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        for _ in range(6):
            _make_draft(test_db_session, conv,
                        status=DraftStatus.PENDING,
                        created_at=datetime.now(timezone.utc) - timedelta(hours=30))
        await test_db_session.commit()

        result = await check_stale_pending_drafts(test_db_session)
        assert result.status == CheckStatus.WARNING

    @pytest.mark.asyncio
    async def test_critical_many_stale(self, test_db_session: AsyncSession):
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()
        for _ in range(12):
            _make_draft(test_db_session, conv,
                        status=DraftStatus.PENDING,
                        created_at=datetime.now(timezone.utc) - timedelta(hours=30))
        await test_db_session.commit()

        result = await check_stale_pending_drafts(test_db_session)
        assert result.status == CheckStatus.CRITICAL


# ===========================================================================
# Check 6: Prospect freshness
# ===========================================================================

class TestCheckProspectFreshness:
    @pytest.mark.asyncio
    async def test_ok_recent_prospect(self, test_db_session: AsyncSession):
        _make_prospect(test_db_session, created_at=datetime.now(timezone.utc))
        await test_db_session.commit()

        result = await check_prospect_freshness(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_no_recent(self, test_db_session: AsyncSession):
        _make_prospect(test_db_session, created_at=datetime.now(timezone.utc) - timedelta(days=10))
        await test_db_session.commit()

        result = await check_prospect_freshness(test_db_session)
        assert result.status == CheckStatus.WARNING

    @pytest.mark.asyncio
    async def test_critical_very_stale(self, test_db_session: AsyncSession):
        result = await check_prospect_freshness(test_db_session)
        assert result.status == CheckStatus.CRITICAL


# ===========================================================================
# Check 7: Daily metrics
# ===========================================================================

class TestCheckDailyMetrics:
    @pytest.mark.asyncio
    async def test_ok_recent_entry(self, test_db_session: AsyncSession):
        yesterday = date.today() - timedelta(days=1)
        metrics = DailyMetrics(date=yesterday)
        test_db_session.add(metrics)
        await test_db_session.commit()

        result = await check_daily_metrics(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_stale(self, test_db_session: AsyncSession):
        old_date = date.today() - timedelta(days=3)
        metrics = DailyMetrics(date=old_date)
        test_db_session.add(metrics)
        await test_db_session.commit()

        result = await check_daily_metrics(test_db_session)
        assert result.status == CheckStatus.WARNING

    @pytest.mark.asyncio
    async def test_critical_very_stale(self, test_db_session: AsyncSession):
        old_date = date.today() - timedelta(days=6)
        metrics = DailyMetrics(date=old_date)
        test_db_session.add(metrics)
        await test_db_session.commit()

        result = await check_daily_metrics(test_db_session)
        assert result.status == CheckStatus.CRITICAL

    @pytest.mark.asyncio
    async def test_warning_no_entries(self, test_db_session: AsyncSession):
        result = await check_daily_metrics(test_db_session)
        assert result.status == CheckStatus.WARNING


# ===========================================================================
# Check 8: Pipeline runs
# ===========================================================================

class TestCheckPipelineRuns:
    @pytest.mark.asyncio
    async def test_ok_recent_successful(self, test_db_session: AsyncSession):
        run = PipelineRun(
            run_type="competitor_post",
            status="completed",
            created_at=datetime.now(timezone.utc),
        )
        test_db_session.add(run)
        await test_db_session.commit()

        result = await check_pipeline_runs(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_latest_failed(self, test_db_session: AsyncSession):
        run = PipelineRun(
            run_type="competitor_post",
            status="failed",
            error_message="API timeout",
            created_at=datetime.now(timezone.utc),
        )
        test_db_session.add(run)
        await test_db_session.commit()

        result = await check_pipeline_runs(test_db_session)
        assert result.status == CheckStatus.WARNING
        assert "failed" in result.message

    @pytest.mark.asyncio
    async def test_warning_no_runs(self, test_db_session: AsyncSession):
        result = await check_pipeline_runs(test_db_session)
        assert result.status == CheckStatus.WARNING


# ===========================================================================
# Check 9: Connection tracking
# ===========================================================================

class TestCheckConnectionTracking:
    @pytest.mark.asyncio
    async def test_ok_recent_connections(self, test_db_session: AsyncSession):
        _make_prospect(test_db_session, connection_sent_at=datetime.now(timezone.utc))
        await test_db_session.commit()

        result = await check_connection_tracking(test_db_session)
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warning_no_recent(self, test_db_session: AsyncSession):
        result = await check_connection_tracking(test_db_session)
        assert result.status == CheckStatus.WARNING


# ===========================================================================
# Orchestrator
# ===========================================================================

class TestRunHealthCheck:
    @pytest.mark.asyncio
    async def test_all_ok(self, test_db_session: AsyncSession):
        """When all data is fresh, overall status should be OK."""
        # Seed enough data to make all checks pass
        conv = _make_conversation(test_db_session)
        await test_db_session.flush()

        _make_message(test_db_session, conv, MessageDirection.INBOUND)
        _make_message(test_db_session, conv, MessageDirection.OUTBOUND)
        _make_draft(test_db_session, conv)
        _make_prospect(test_db_session, connection_sent_at=datetime.now(timezone.utc))

        yesterday = date.today() - timedelta(days=1)
        test_db_session.add(DailyMetrics(date=yesterday))
        test_db_session.add(PipelineRun(run_type="test", status="completed"))

        await test_db_session.commit()

        report = await run_health_check(test_db_session)
        assert report.status == CheckStatus.OK
        assert report.passing == len(ALL_CHECKS)
        assert len(report.failing) == 0

    @pytest.mark.asyncio
    async def test_mixed_failures(self, test_db_session: AsyncSession):
        """Empty DB should produce multiple failures."""
        report = await run_health_check(test_db_session)
        assert report.status in (CheckStatus.WARNING, CheckStatus.CRITICAL)
        assert len(report.failing) > 0
        assert report.passing + len(report.failing) == len(ALL_CHECKS)


# ===========================================================================
# Slack block builder
# ===========================================================================

class TestHealthCheckSlackBlocks:
    def test_build_alert_blocks(self):
        from app.services.slack import build_health_check_alert_blocks

        report = HealthCheckReport(
            status=CheckStatus.WARNING,
            checks=[
                CheckResult("inbound_messages", CheckStatus.OK, "5 inbound in last 48h"),
                CheckResult("outbound_messages", CheckStatus.WARNING, "Zero outbound in last 36h"),
                CheckResult("pipeline_runs", CheckStatus.CRITICAL, "Latest run failed"),
            ],
        )

        blocks = build_health_check_alert_blocks(report)

        # Header block
        assert blocks[0]["type"] == "header"
        assert "WARNING" in blocks[0]["text"]["text"]

        # Should have failing check sections (2 failing: outbound + pipeline)
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) == 2

        # Context block with passing count
        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert len(context_blocks) == 1
        assert "1/3 checks passing" in context_blocks[0]["elements"][0]["text"]


# ===========================================================================
# API endpoints
# ===========================================================================

class TestHealthCheckEndpoints:
    @pytest.mark.asyncio
    async def test_admin_health_check_requires_auth(self, test_client):
        response = await test_client.post("/admin/health-check")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_admin_health_check_with_auth(self, test_client):
        response = await test_client.post(
            "/admin/health-check",
            headers={"Authorization": "Bearer test_secret_key_for_testing"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "checks" in data
        assert "passing" in data
        assert "total" in data
        assert data["total"] == len(ALL_CHECKS)

    @pytest.mark.asyncio
    async def test_status_endpoint_no_auth(self, test_client):
        response = await test_client.get("/admin/health-check/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "passing" in data
        assert "total" in data
        assert "failing" in data
        assert isinstance(data["failing"], list)
