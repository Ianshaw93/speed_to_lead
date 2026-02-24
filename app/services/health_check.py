"""Production health check service.

Runs 9 independent checks against the DB to verify the live system
has fresh data flowing through it. Only alerts Slack on failures.
"""

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
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
)

logger = logging.getLogger(__name__)


class CheckStatus(str, enum.Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    details: dict | None = None


@dataclass
class HealthCheckReport:
    status: CheckStatus  # worst status across all checks
    checks: list[CheckResult] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def passing(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.OK)

    @property
    def failing(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status != CheckStatus.OK]


def _is_monday() -> bool:
    """Check if today is Monday (for weekend-aware thresholds)."""
    return datetime.now(timezone.utc).weekday() == 0


# ---------------------------------------------------------------------------
# Check 1: Inbound messages
# ---------------------------------------------------------------------------

async def check_inbound_messages(session: AsyncSession) -> CheckResult:
    """Verify inbound messages are flowing in."""
    now = datetime.now(timezone.utc)
    monday = _is_monday()
    warn_hours = 72 if monday else 48
    crit_hours = 84 if monday else 72

    result = await session.execute(
        select(func.count(MessageLog.id)).where(
            MessageLog.direction == MessageDirection.INBOUND,
            MessageLog.sent_at >= now - timedelta(hours=warn_hours),
        )
    )
    count = result.scalar() or 0

    if count > 0:
        return CheckResult("inbound_messages", CheckStatus.OK, f"{count} inbound in last {warn_hours}h")

    # Check critical window
    result_crit = await session.execute(
        select(func.count(MessageLog.id)).where(
            MessageLog.direction == MessageDirection.INBOUND,
            MessageLog.sent_at >= now - timedelta(hours=crit_hours),
        )
    )
    count_crit = result_crit.scalar() or 0

    if count_crit > 0:
        return CheckResult("inbound_messages", CheckStatus.WARNING, f"Zero inbound in last {warn_hours}h")

    return CheckResult("inbound_messages", CheckStatus.CRITICAL, f"Zero inbound in last {crit_hours}h")


# ---------------------------------------------------------------------------
# Check 2: Outbound messages
# ---------------------------------------------------------------------------

async def check_outbound_messages(session: AsyncSession) -> CheckResult:
    """Verify outbound messages are being sent."""
    now = datetime.now(timezone.utc)
    monday = _is_monday()
    warn_hours = 72 if monday else 36
    crit_hours = 84 if monday else 48

    result = await session.execute(
        select(func.count(MessageLog.id)).where(
            MessageLog.direction == MessageDirection.OUTBOUND,
            MessageLog.sent_at >= now - timedelta(hours=warn_hours),
        )
    )
    count = result.scalar() or 0

    if count > 0:
        return CheckResult("outbound_messages", CheckStatus.OK, f"{count} outbound in last {warn_hours}h")

    result_crit = await session.execute(
        select(func.count(MessageLog.id)).where(
            MessageLog.direction == MessageDirection.OUTBOUND,
            MessageLog.sent_at >= now - timedelta(hours=crit_hours),
        )
    )
    count_crit = result_crit.scalar() or 0

    if count_crit > 0:
        return CheckResult("outbound_messages", CheckStatus.WARNING, f"Zero outbound in last {warn_hours}h")

    return CheckResult("outbound_messages", CheckStatus.CRITICAL, f"Zero outbound in last {crit_hours}h")


# ---------------------------------------------------------------------------
# Check 3: Draft generation
# ---------------------------------------------------------------------------

async def check_draft_generation(session: AsyncSession) -> CheckResult:
    """Verify drafts are being generated for inbound conversations."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=48)

    # Find inbound messages in last 48h
    inbound_result = await session.execute(
        select(MessageLog.conversation_id).where(
            MessageLog.direction == MessageDirection.INBOUND,
            MessageLog.sent_at >= cutoff,
        ).distinct()
    )
    inbound_conv_ids = [row[0] for row in inbound_result.all()]

    if not inbound_conv_ids:
        return CheckResult("draft_generation", CheckStatus.OK, "No recent inbound to check")

    # Check which have drafts
    draft_result = await session.execute(
        select(Draft.conversation_id).where(
            Draft.conversation_id.in_(inbound_conv_ids),
            Draft.created_at >= cutoff,
        ).distinct()
    )
    draft_conv_ids = {row[0] for row in draft_result.all()}

    missing = len(inbound_conv_ids) - len(draft_conv_ids)
    if missing == 0:
        return CheckResult("draft_generation", CheckStatus.OK, f"All {len(inbound_conv_ids)} recent convos have drafts")

    return CheckResult(
        "draft_generation",
        CheckStatus.WARNING,
        f"{missing}/{len(inbound_conv_ids)} inbound convos missing drafts",
        details={"missing": missing, "total": len(inbound_conv_ids)},
    )


# ---------------------------------------------------------------------------
# Check 4: Slack delivery
# ---------------------------------------------------------------------------

async def check_slack_delivery(session: AsyncSession) -> CheckResult:
    """Verify drafts have Slack message timestamps (were delivered to Slack)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=48)

    total_result = await session.execute(
        select(func.count(Draft.id)).where(Draft.created_at >= cutoff)
    )
    total = total_result.scalar() or 0

    if total == 0:
        return CheckResult("slack_delivery", CheckStatus.OK, "No recent drafts to check")

    missing_result = await session.execute(
        select(func.count(Draft.id)).where(
            Draft.created_at >= cutoff,
            Draft.slack_message_ts.is_(None),
        )
    )
    missing = missing_result.scalar() or 0

    if missing == 0:
        return CheckResult("slack_delivery", CheckStatus.OK, f"All {total} recent drafts delivered to Slack")

    if missing == total:
        return CheckResult(
            "slack_delivery",
            CheckStatus.CRITICAL,
            f"ALL {total} recent drafts missing Slack delivery",
        )

    return CheckResult(
        "slack_delivery",
        CheckStatus.WARNING,
        f"{missing}/{total} recent drafts missing Slack delivery",
    )


# ---------------------------------------------------------------------------
# Check 5: Stale pending drafts
# ---------------------------------------------------------------------------

async def check_stale_pending_drafts(session: AsyncSession) -> CheckResult:
    """Check for pending drafts older than 24h (may indicate stuck pipeline)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    result = await session.execute(
        select(func.count(Draft.id)).where(
            Draft.status == DraftStatus.PENDING,
            Draft.created_at < cutoff,
        )
    )
    stale = result.scalar() or 0

    if stale < 10:
        return CheckResult("stale_pending_drafts", CheckStatus.OK, f"{stale} stale pending drafts")

    if stale <= 30:
        return CheckResult("stale_pending_drafts", CheckStatus.WARNING, f"{stale} pending drafts older than 24h")

    return CheckResult("stale_pending_drafts", CheckStatus.CRITICAL, f"{stale} pending drafts older than 24h")


# ---------------------------------------------------------------------------
# Check 6: Prospect freshness
# ---------------------------------------------------------------------------

async def check_prospect_freshness(session: AsyncSession) -> CheckResult:
    """Verify new prospects are being added regularly."""
    now = datetime.now(timezone.utc)

    result_7d = await session.execute(
        select(func.count(Prospect.id)).where(
            Prospect.created_at >= now - timedelta(days=7),
        )
    )
    count_7d = result_7d.scalar() or 0

    if count_7d > 0:
        return CheckResult("prospect_freshness", CheckStatus.OK, f"{count_7d} new prospects in last 7d")

    result_14d = await session.execute(
        select(func.count(Prospect.id)).where(
            Prospect.created_at >= now - timedelta(days=14),
        )
    )
    count_14d = result_14d.scalar() or 0

    if count_14d > 0:
        return CheckResult("prospect_freshness", CheckStatus.WARNING, "No new prospects in last 7d")

    return CheckResult("prospect_freshness", CheckStatus.CRITICAL, "No new prospects in last 14d")


# ---------------------------------------------------------------------------
# Check 7: Daily metrics
# ---------------------------------------------------------------------------

async def check_daily_metrics(session: AsyncSession) -> CheckResult:
    """Verify daily metrics are being recorded."""
    result = await session.execute(
        select(DailyMetrics.date).order_by(DailyMetrics.date.desc()).limit(1)
    )
    row = result.first()

    if not row:
        return CheckResult("daily_metrics", CheckStatus.WARNING, "No daily metrics entries found")

    from datetime import date as date_type
    today = date_type.today()
    latest_date = row[0]
    days_stale = (today - latest_date).days

    if days_stale <= 2:
        return CheckResult("daily_metrics", CheckStatus.OK, f"Latest metrics: {latest_date}")

    if days_stale <= 4:
        return CheckResult("daily_metrics", CheckStatus.WARNING, f"Metrics {days_stale}d stale (latest: {latest_date})")

    return CheckResult("daily_metrics", CheckStatus.CRITICAL, f"Metrics {days_stale}d stale (latest: {latest_date})")


# ---------------------------------------------------------------------------
# Check 8: Pipeline runs
# ---------------------------------------------------------------------------

async def check_pipeline_runs(session: AsyncSession) -> CheckResult:
    """Verify pipeline runs are completing successfully."""
    now = datetime.now(timezone.utc)

    # Latest run
    result = await session.execute(
        select(PipelineRun).order_by(PipelineRun.created_at.desc()).limit(1)
    )
    latest = result.scalar_one_or_none()

    if not latest:
        return CheckResult("pipeline_runs", CheckStatus.WARNING, "No pipeline runs found")

    # Handle timezone-naive datetimes (e.g. SQLite in tests)
    created = latest.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    days_since = (now - created).total_seconds() / 86400

    if latest.status == "failed":
        return CheckResult(
            "pipeline_runs",
            CheckStatus.WARNING,
            f"Latest pipeline run failed: {latest.error_message or 'unknown error'}",
            details={"run_type": latest.run_type, "status": latest.status},
        )

    if days_since > 7:
        return CheckResult("pipeline_runs", CheckStatus.WARNING, f"No pipeline runs in last 7d")

    return CheckResult(
        "pipeline_runs",
        CheckStatus.OK,
        f"Latest run: {latest.run_type} ({latest.status}) {days_since:.1f}d ago",
    )


# ---------------------------------------------------------------------------
# Check 9: Connection tracking
# ---------------------------------------------------------------------------

async def check_connection_tracking(session: AsyncSession) -> CheckResult:
    """Verify connection requests are being sent."""
    now = datetime.now(timezone.utc)

    result = await session.execute(
        select(func.count(Prospect.id)).where(
            Prospect.connection_sent_at >= now - timedelta(days=7),
        )
    )
    count = result.scalar() or 0

    if count > 0:
        return CheckResult("connection_tracking", CheckStatus.OK, f"{count} connection requests sent in last 7d")

    return CheckResult("connection_tracking", CheckStatus.WARNING, "No connection_sent_at in last 7d")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    check_inbound_messages,
    check_outbound_messages,
    check_draft_generation,
    check_slack_delivery,
    check_stale_pending_drafts,
    check_prospect_freshness,
    check_daily_metrics,
    check_pipeline_runs,
    check_connection_tracking,
]


async def run_health_check(session: AsyncSession) -> HealthCheckReport:
    """Run all health checks and return a report."""
    results: list[CheckResult] = []

    for check_fn in ALL_CHECKS:
        try:
            result = await check_fn(session)
            results.append(result)
        except Exception as e:
            logger.error(f"Health check {check_fn.__name__} failed with error: {e}", exc_info=True)
            results.append(CheckResult(
                name=check_fn.__name__.replace("check_", ""),
                status=CheckStatus.WARNING,
                message=f"Check errored: {e}",
            ))

    # Overall status = worst individual status
    if any(r.status == CheckStatus.CRITICAL for r in results):
        overall = CheckStatus.CRITICAL
    elif any(r.status == CheckStatus.WARNING for r in results):
        overall = CheckStatus.WARNING
    else:
        overall = CheckStatus.OK

    return HealthCheckReport(status=overall, checks=results)
