"""Tests for the morning standup report script."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Base,
    Conversation,
    Draft,
    DraftLearning,
    DraftStatus,
    FunnelStage,
    LearningType,
    MessageDirection,
    MessageLog,
    Prospect,
)


def _utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


YESTERDAY = _utc(2026, 2, 23)
TODAY = _utc(2026, 2, 24)
TWO_DAYS_AGO = _utc(2026, 2, 22)


def _make_conversation(lead_name="Test Lead", history=None):
    return Conversation(
        id=uuid.uuid4(),
        heyreach_lead_id=str(uuid.uuid4()),
        linkedin_profile_url=f"https://linkedin.com/in/{lead_name.lower().replace(' ', '-')}",
        lead_name=lead_name,
        conversation_history=history or [{"role": "lead", "content": "Hey there!"}],
        funnel_stage=FunnelStage.POSITIVE_REPLY,
        created_at=YESTERDAY,
        updated_at=YESTERDAY,
    )


def _make_draft(conversation_id, status=DraftStatus.PENDING, created_at=YESTERDAY,
                ai_draft="AI generated reply", human_edited_draft=None,
                actual_sent_text=None, qa_score=None, qa_verdict=None):
    return Draft(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        status=status,
        ai_draft=ai_draft,
        human_edited_draft=human_edited_draft,
        actual_sent_text=actual_sent_text,
        qa_score=qa_score,
        qa_verdict=qa_verdict,
        created_at=created_at,
        updated_at=created_at,
    )


def _make_prospect(full_name="Test Prospect", company_name="Test Co", **kwargs):
    return Prospect(
        id=uuid.uuid4(),
        linkedin_url=f"https://linkedin.com/in/{uuid.uuid4().hex[:8]}",
        full_name=full_name,
        company_name=company_name,
        created_at=TWO_DAYS_AGO,
        updated_at=YESTERDAY,
        **kwargs,
    )


# Import the standup module functions after conftest sets env vars
@pytest.fixture
def standup_module():
    """Import standup module lazily to ensure env vars are set."""
    import importlib
    import scripts.standup as mod
    importlib.reload(mod)
    return mod


@pytest.mark.asyncio
async def test_empty_report(test_db_session: AsyncSession, standup_module):
    """With no data, all sections should show zeros / empty."""
    from datetime import date
    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "# Morning Standup" in report
    assert "Total messages: **0**" in report
    assert "No funnel progression" in report


@pytest.mark.asyncio
async def test_draft_status_counts(test_db_session: AsyncSession, standup_module):
    """Draft activity section should count drafts by status."""
    from datetime import date

    conv = _make_conversation("Alice")
    test_db_session.add(conv)
    await test_db_session.flush()

    # Create drafts with various statuses, all created yesterday
    drafts = [
        _make_draft(conv.id, DraftStatus.APPROVED, YESTERDAY),
        _make_draft(conv.id, DraftStatus.APPROVED, YESTERDAY),
        _make_draft(conv.id, DraftStatus.REJECTED, YESTERDAY),
        _make_draft(conv.id, DraftStatus.SNOOZED, YESTERDAY),
        _make_draft(conv.id, DraftStatus.PENDING, YESTERDAY),
    ]
    test_db_session.add_all(drafts)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "Total messages: **5**" in report
    assert "Approved (via Slack): 2" in report
    assert "Rejected (via Slack): 1" in report
    assert "Snoozed (via Slack): 1" in report
    assert "Pending (via Slack): 1" in report


@pytest.mark.asyncio
async def test_human_edit_detection(test_db_session: AsyncSession, standup_module):
    """Should distinguish human-edited drafts from AI sent as-is."""
    from datetime import date

    conv = _make_conversation("Bob")
    test_db_session.add(conv)
    await test_db_session.flush()

    # 2 approved: 1 edited by human, 1 sent as-is
    drafts = [
        _make_draft(conv.id, DraftStatus.APPROVED, YESTERDAY,
                    human_edited_draft="Human tweaked this"),
        _make_draft(conv.id, DraftStatus.APPROVED, YESTERDAY,
                    human_edited_draft=None),
    ]
    test_db_session.add_all(drafts)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "Sent as-is: **1**" in report
    assert "Human edited: **1**" in report
    assert "AI accuracy: **50.0%**" in report


@pytest.mark.asyncio
async def test_qa_performance(test_db_session: AsyncSession, standup_module):
    """QA section should show average score and verdict counts."""
    from datetime import date

    conv = _make_conversation("Charlie")
    test_db_session.add(conv)
    await test_db_session.flush()

    drafts = [
        _make_draft(conv.id, DraftStatus.APPROVED, YESTERDAY,
                    qa_score=Decimal("8.5"), qa_verdict="pass"),
        _make_draft(conv.id, DraftStatus.APPROVED, YESTERDAY,
                    qa_score=Decimal("6.0"), qa_verdict="flag"),
        _make_draft(conv.id, DraftStatus.REJECTED, YESTERDAY,
                    qa_score=Decimal("3.0"), qa_verdict="block"),
    ]
    test_db_session.add_all(drafts)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "Average QA score: **5.8**" in report
    assert "pass: 1" in report
    assert "flag: 1" in report
    assert "block: 1" in report


@pytest.mark.asyncio
async def test_funnel_progression(test_db_session: AsyncSession, standup_module):
    """Should detect prospects who moved through funnel stages yesterday."""
    from datetime import date

    prospects = [
        _make_prospect("Diana Prince", "Themyscira Inc", pitched_at=YESTERDAY),
        _make_prospect("Bruce Wayne", "Wayne Enterprises", calendar_sent_at=YESTERDAY),
        _make_prospect("Clark Kent", "Daily Planet", booked_at=YESTERDAY),
        # Should NOT appear — pitched two days ago
        _make_prospect("Barry Allen", "Star Labs", pitched_at=TWO_DAYS_AGO),
    ]
    test_db_session.add_all(prospects)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "Diana Prince" in report
    assert "Themyscira Inc" in report
    assert "Bruce Wayne" in report
    assert "Clark Kent" in report
    assert "Barry Allen" not in report


@pytest.mark.asyncio
async def test_date_filtering(test_db_session: AsyncSession, standup_module):
    """Drafts from other days should not appear in the report."""
    from datetime import date

    conv = _make_conversation("Eve")
    test_db_session.add(conv)
    await test_db_session.flush()

    # Draft from two days ago — should not appear in yesterday's report
    old_draft = _make_draft(conv.id, DraftStatus.APPROVED, TWO_DAYS_AGO)
    # Draft from yesterday — should appear
    new_draft = _make_draft(conv.id, DraftStatus.APPROVED, YESTERDAY)
    test_db_session.add_all([old_draft, new_draft])
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))
    assert "Total messages: **1**" in report


@pytest.mark.asyncio
async def test_notable_conversations(test_db_session: AsyncSession, standup_module):
    """Notable conversations section should show approved draft details."""
    from datetime import date

    conv = _make_conversation(
        "Frank Castle",
        history=[{"role": "lead", "content": "I'd love to learn more about your product"}],
    )
    test_db_session.add(conv)
    await test_db_session.flush()

    draft = _make_draft(
        conv.id, DraftStatus.APPROVED, YESTERDAY,
        ai_draft="Great to hear!",
        actual_sent_text="Awesome, let me tell you more",
    )
    test_db_session.add(draft)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "Frank Castle" in report
    assert "love to learn more" in report
    assert "Awesome, let me tell you more" in report


@pytest.mark.asyncio
async def test_learnings_section(test_db_session: AsyncSession, standup_module):
    """Should include recent DraftLearning entries."""
    from datetime import date

    conv = _make_conversation("Greta")
    test_db_session.add(conv)
    await test_db_session.flush()

    draft = _make_draft(conv.id, DraftStatus.APPROVED, YESTERDAY)
    test_db_session.add(draft)
    await test_db_session.flush()

    learning = DraftLearning(
        id=uuid.uuid4(),
        draft_id=draft.id,
        learning_type=LearningType.TONE,
        original_text="Too formal greeting",
        corrected_text="Casual hey",
        diff_summary="Changed formal greeting to casual tone",
        confidence=Decimal("0.8"),
        created_at=YESTERDAY,
    )
    test_db_session.add(learning)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "Changed formal greeting to casual tone" in report


@pytest.mark.asyncio
async def test_direct_heyreach_sends(test_db_session: AsyncSession, standup_module):
    """Messages sent via HeyReach directly (no draft) should appear in report."""
    from datetime import date

    # Conversation with no draft — message sent directly via HeyReach
    conv = _make_conversation(
        "Hal Jordan",
        history=[{"role": "lead", "content": "Sounds interesting, tell me more"}],
    )
    test_db_session.add(conv)
    await test_db_session.flush()

    # Outbound reply sent manually via HeyReach (no campaign, no draft)
    msg = MessageLog(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        direction=MessageDirection.OUTBOUND,
        content="Hey Hal, happy to share more details!",
        sent_at=YESTERDAY,
    )
    test_db_session.add(msg)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    # Section 1: should count as a message
    assert "Total messages: **1**" in report
    assert "Sent via HeyReach directly: 1" in report

    # Section 2: should show in messages sent
    assert "Sent via HeyReach directly**: 1" in report

    # Section 5: should appear in notable conversations
    assert "Hal Jordan" in report
    assert "happy to share more details" in report


@pytest.mark.asyncio
async def test_mixed_draft_and_heyreach(test_db_session: AsyncSession, standup_module):
    """Report should combine Slack-approved drafts and direct HeyReach sends."""
    from datetime import date

    # Conv 1: has a draft (Slack flow)
    conv1 = _make_conversation("Peter Parker")
    test_db_session.add(conv1)
    await test_db_session.flush()

    draft = _make_draft(conv1.id, DraftStatus.APPROVED, YESTERDAY)
    test_db_session.add(draft)

    # Conv 2: direct HeyReach send (no draft)
    conv2 = _make_conversation("Tony Stark")
    test_db_session.add(conv2)
    await test_db_session.flush()

    msg = MessageLog(
        id=uuid.uuid4(),
        conversation_id=conv2.id,
        direction=MessageDirection.OUTBOUND,
        content="Let's connect!",
        sent_at=YESTERDAY,
    )
    test_db_session.add(msg)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "Total messages: **2**" in report
    assert "Approved (via Slack): 1" in report
    assert "Sent via HeyReach directly: 1" in report
    assert "Total replies sent: **2**" in report


@pytest.mark.asyncio
async def test_campaign_messages_excluded(test_db_session: AsyncSession, standup_module):
    """Campaign automation (initial outreach, follow-ups) should NOT appear."""
    from datetime import date

    conv = _make_conversation("Campaign Target")
    test_db_session.add(conv)
    await test_db_session.flush()

    # Campaign automation message — should be excluded
    campaign_msg = MessageLog(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        direction=MessageDirection.OUTBOUND,
        content="Hey, saw you liked a post...",
        sent_at=YESTERDAY,
        campaign_id=300178,
        campaign_name="Smiths Competition",
    )
    test_db_session.add(campaign_msg)
    await test_db_session.commit()

    report = await standup_module.generate_report(test_db_session, date(2026, 2, 23))

    assert "Total messages: **0**" in report
    assert "Campaign Target" not in report
