#!/usr/bin/env python3
"""Morning standup report — daily snapshot of draft activity, QA, and funnel.

Queries the production database and outputs a markdown report covering:
1. Draft Activity (created yesterday, by status)
2. Human Edits vs AI Sent As-Is
3. QA Performance (scores, verdicts)
4. Funnel Progression (pitched/calendar/booked)
5. Notable Conversations (approved drafts with context)
6. Learnings from Edits (DraftLearning entries)

Usage:
    python scripts/standup.py                          # Yesterday's report
    python scripts/standup.py --date 2026-02-20        # Specific date
    python scripts/standup.py --output .tmp/standup.md  # Save to file
"""

import argparse
import asyncio
import io
import os
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# Fix Windows console encoding
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set minimal env defaults for config loading
os.environ.setdefault("HEYREACH_API_KEY", "")
os.environ.setdefault("SLACK_BOT_TOKEN", "")
os.environ.setdefault("SLACK_CHANNEL_ID", "")
os.environ.setdefault("SLACK_SIGNING_SECRET", "")
os.environ.setdefault("APIFY_API_TOKEN", "")
os.environ.setdefault("SLACK_ENGAGEMENT_CHANNEL_ID", "")
os.environ.setdefault("SECRET_KEY", "standup")
os.environ.setdefault("ENVIRONMENT", "script")
os.environ.setdefault("CONTENT_DB_URL", "")
os.environ.setdefault("PERPLEXITY_API_KEY", "")
os.environ.setdefault("ANTHROPIC_API_KEY", "")

from sqlalchemy import func, select, and_, case, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Conversation,
    Draft,
    DraftLearning,
    DraftStatus,
    MessageDirection,
    MessageLog,
    Prospect,
)


def _day_range(target_date: date) -> tuple[datetime, datetime]:
    """Return UTC start/end datetimes for a given date."""
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end


async def generate_report(session: AsyncSession, target_date: date) -> str:
    """Generate the full standup report for the given date."""
    day_start, day_end = _day_range(target_date)
    lines: list[str] = []

    lines.append(f"# Morning Standup — {target_date.strftime('%A %d %B %Y')}")
    lines.append("")

    # ── Section 1: Draft Activity ──
    await _section_draft_activity(session, day_start, day_end, lines)

    # ── Section 2: Human Edits vs AI As-Is ──
    await _section_human_edits(session, day_start, day_end, lines)

    # ── Section 3: QA Performance ──
    await _section_qa_performance(session, day_start, day_end, lines)

    # ── Section 4: Funnel Progression ──
    await _section_funnel_progression(session, day_start, day_end, lines)

    # ── Section 5: Notable Conversations ──
    await _section_notable_conversations(session, day_start, day_end, lines)

    # ── Section 6: Learnings from Edits ──
    await _section_learnings(session, day_start, day_end, lines)

    return "\n".join(lines)


async def _get_direct_heyreach_replies(
    session: AsyncSession, day_start: datetime, day_end: datetime,
) -> list[tuple[MessageLog, Conversation | None]]:
    """Get replies sent manually via HeyReach (no Draft record, no campaign).

    Excludes campaign automation (initial outreach + follow-ups) which have
    campaign_id set. Only returns manual replies — outbound messages without
    a campaign_id and without a matching Draft.
    """
    draft_conv_ids = (
        select(Draft.conversation_id)
        .where(
            Draft.created_at >= day_start,
            Draft.created_at < day_end,
        )
        .correlate()
        .scalar_subquery()
    )

    result = await session.execute(
        select(MessageLog, Conversation)
        .outerjoin(Conversation, MessageLog.conversation_id == Conversation.id)
        .where(
            MessageLog.sent_at >= day_start,
            MessageLog.sent_at < day_end,
            MessageLog.direction == MessageDirection.OUTBOUND,
            MessageLog.campaign_id.is_(None),  # Exclude campaign automation
            MessageLog.conversation_id.notin_(draft_conv_ids),
        )
        .order_by(MessageLog.sent_at)
    )
    return result.all()


async def _section_draft_activity(
    session: AsyncSession, day_start: datetime, day_end: datetime, lines: list[str]
):
    """Section 1: Draft counts by status + direct HeyReach sends."""
    lines.append("## 1. Draft Activity")
    lines.append("")

    # Count all drafts created in the date range
    result = await session.execute(
        select(Draft.status, func.count(Draft.id))
        .where(Draft.created_at >= day_start, Draft.created_at < day_end)
        .group_by(Draft.status)
    )
    counts = {row[0]: row[1] for row in result.all()}
    draft_total = sum(counts.values())

    # Count direct HeyReach sends (no draft)
    direct_sends = await _get_direct_heyreach_replies(session, day_start, day_end)
    direct_count = len(direct_sends)

    total = draft_total + direct_count
    lines.append(f"Total messages: **{total}**")
    lines.append("")
    if draft_total > 0:
        for status in [DraftStatus.APPROVED, DraftStatus.REJECTED, DraftStatus.SNOOZED, DraftStatus.PENDING]:
            count = counts.get(status, 0)
            if count > 0:
                lines.append(f"- {status.value.capitalize()} (via Slack): {count}")
    if direct_count > 0:
        lines.append(f"- Sent via HeyReach directly: {direct_count}")
    lines.append("")


async def _section_human_edits(
    session: AsyncSession, day_start: datetime, day_end: datetime, lines: list[str]
):
    """Section 2: Of approved drafts, how many were edited vs sent as-is. Includes direct HeyReach sends."""
    lines.append("## 2. Messages Sent")
    lines.append("")

    # Approved drafts via Slack
    result = await session.execute(
        select(Draft.human_edited_draft)
        .where(
            Draft.created_at >= day_start,
            Draft.created_at < day_end,
            Draft.status == DraftStatus.APPROVED,
        )
    )
    rows = result.all()
    total_approved = len(rows)

    # Direct HeyReach sends
    direct_sends = await _get_direct_heyreach_replies(session, day_start, day_end)
    direct_count = len(direct_sends)

    total_sent = total_approved + direct_count

    if total_sent == 0:
        lines.append("No messages sent.")
        lines.append("")
        return

    if total_approved > 0:
        edited = sum(1 for (hed,) in rows if hed is not None)
        as_is = total_approved - edited
        accuracy = (as_is / total_approved) * 100
        lines.append(f"**Slack approval flow** ({total_approved}):")
        lines.append(f"- Sent as-is: **{as_is}** | Human edited: **{edited}**")
        lines.append(f"- AI accuracy: **{accuracy:.1f}%**")
        lines.append("")

    if direct_count > 0:
        lines.append(f"**Sent via HeyReach directly**: {direct_count}")
        lines.append("")

    lines.append(f"Total replies sent: **{total_sent}**")
    lines.append("")


async def _section_qa_performance(
    session: AsyncSession, day_start: datetime, day_end: datetime, lines: list[str]
):
    """Section 3: QA scores and verdict breakdown."""
    lines.append("## 3. QA Performance")
    lines.append("")

    # Average score
    result = await session.execute(
        select(func.avg(Draft.qa_score), func.count(Draft.qa_score))
        .where(
            Draft.created_at >= day_start,
            Draft.created_at < day_end,
            Draft.qa_score.isnot(None),
        )
    )
    avg_score, scored_count = result.one()

    if not scored_count:
        lines.append("No QA scores recorded.")
        lines.append("")
        return

    # Round to 1 decimal
    avg_display = round(float(avg_score), 1) if avg_score else 0.0
    lines.append(f"Average QA score: **{avg_display}** ({scored_count} drafts scored)")
    lines.append("")

    # Verdict breakdown
    result = await session.execute(
        select(Draft.qa_verdict, func.count(Draft.id))
        .where(
            Draft.created_at >= day_start,
            Draft.created_at < day_end,
            Draft.qa_verdict.isnot(None),
        )
        .group_by(Draft.qa_verdict)
    )
    verdicts = {row[0]: row[1] for row in result.all()}
    if verdicts:
        lines.append("Verdicts:")
        for verdict in ["pass", "flag", "block"]:
            count = verdicts.get(verdict, 0)
            if count > 0:
                lines.append(f"- {verdict}: {count}")
    lines.append("")


async def _section_funnel_progression(
    session: AsyncSession, day_start: datetime, day_end: datetime, lines: list[str]
):
    """Section 4: Prospects who moved through funnel stages."""
    lines.append("## 4. Funnel Progression")
    lines.append("")

    found_any = False

    for label, field in [
        ("Pitched", Prospect.pitched_at),
        ("Calendar Sent", Prospect.calendar_sent_at),
        ("Booked", Prospect.booked_at),
    ]:
        result = await session.execute(
            select(Prospect.full_name, Prospect.company_name)
            .where(field >= day_start, field < day_end)
            .order_by(field)
        )
        rows = result.all()
        if rows:
            found_any = True
            lines.append(f"**{label}** ({len(rows)}):")
            for name, company in rows:
                company_str = f" @ {company}" if company else ""
                lines.append(f"- {name or 'Unknown'}{company_str}")
            lines.append("")

    if not found_any:
        lines.append("No funnel progression yesterday.")
    lines.append("")


async def _section_notable_conversations(
    session: AsyncSession, day_start: datetime, day_end: datetime, lines: list[str]
):
    """Section 5: Approved drafts + direct HeyReach sends with context."""
    lines.append("## 5. Notable Conversations")
    lines.append("")

    # Approved drafts via Slack
    result = await session.execute(
        select(Draft, Conversation)
        .join(Conversation, Draft.conversation_id == Conversation.id)
        .where(
            Draft.created_at >= day_start,
            Draft.created_at < day_end,
            Draft.status == DraftStatus.APPROVED,
        )
        .order_by(Draft.created_at)
        .limit(10)
    )
    draft_rows = result.all()

    # Direct HeyReach sends
    direct_sends = await _get_direct_heyreach_replies(session, day_start, day_end)

    if not draft_rows and not direct_sends:
        lines.append("No conversations to show.")
        lines.append("")
        return

    for draft, conv in draft_rows:
        lead_msg = _extract_last_lead_message(conv.conversation_history)
        sent_text = draft.actual_sent_text or draft.ai_draft

        lines.append(f"**{conv.lead_name}**")
        if lead_msg:
            snippet = lead_msg[:150] + ("..." if len(lead_msg) > 150 else "")
            lines.append(f"> {snippet}")
        lines.append(f"")
        lines.append(f"Sent: _{sent_text[:200]}_")
        lines.append("")

    if direct_sends:
        if draft_rows:
            lines.append("---")
            lines.append("")
        lines.append("**Sent via HeyReach directly:**")
        lines.append("")
        for msg, conv in direct_sends[:10]:
            name = conv.lead_name if conv else "Unknown"
            lead_msg = _extract_last_lead_message(conv.conversation_history) if conv else None

            lines.append(f"**{name}**")
            if lead_msg:
                snippet = lead_msg[:150] + ("..." if len(lead_msg) > 150 else "")
                lines.append(f"> {snippet}")
            lines.append(f"")
            lines.append(f"Sent: _{msg.content[:200]}_")
            lines.append("")

    lines.append("")


async def _section_learnings(
    session: AsyncSession, day_start: datetime, day_end: datetime, lines: list[str]
):
    """Section 6: Recent DraftLearning entries."""
    lines.append("## 6. Learnings from Edits")
    lines.append("")

    result = await session.execute(
        select(DraftLearning)
        .where(
            DraftLearning.created_at >= day_start,
            DraftLearning.created_at < day_end,
        )
        .order_by(DraftLearning.created_at)
        .limit(10)
    )
    learnings = result.scalars().all()

    if not learnings:
        lines.append("No new learnings recorded.")
        lines.append("")
        return

    for learning in learnings:
        lines.append(f"- **{learning.learning_type.value}**: {learning.diff_summary}")

    lines.append("")


def _extract_last_lead_message(history: list[dict] | None) -> str | None:
    """Get the last lead message from conversation history."""
    if not history:
        return None
    for msg in reversed(history):
        if msg.get("role") == "lead" and msg.get("content"):
            return msg["content"]
    return None


async def run_standalone(target_date: date, output_file: str | None):
    """Run the standup report as a standalone script against prod DB."""
    from app.config import settings

    db_url = settings.async_database_url
    if "sqlite" in db_url and "memory" in db_url:
        print("Error: DATABASE_URL points to in-memory SQLite. Set it to production DB.", file=sys.stderr)
        sys.exit(1)

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    connect_args = {}
    if "postgresql" in db_url:
        connect_args = {"ssl": ssl_context}

    engine = create_async_engine(db_url, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        report = await generate_report(session, target_date)

    await engine.dispose()

    if output_file:
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_text(report, encoding="utf-8")
        print(f"Report saved to: {output_file}")
    else:
        print(report)


def main():
    parser = argparse.ArgumentParser(description="Morning standup report")
    parser.add_argument(
        "--date",
        type=str,
        help="Report date as YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Write report to file instead of stdout",
    )
    args = parser.parse_args()

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        target_date = date.today() - timedelta(days=1)

    asyncio.run(run_standalone(target_date, args.output))


if __name__ == "__main__":
    main()
