"""Tests for AI context awareness improvements.

Covers:
- Phase 1: is_first_reply rendering in prompts
- Phase 2: Core principles prepended to all stage prompts
- Phase 3: Dynamic example retrieval and formatting
- Phase 4: actual_sent_text learning loop
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Draft, DraftStatus, FunnelStage
from app.prompts.principles import CORE_PRINCIPLES
from app.prompts.utils import build_lead_context_section
from app.services.example_retriever import (
    RetrievedExample,
    _extract_last_lead_message,
    _rank_examples,
    format_examples_for_prompt,
    get_similar_examples,
)


# ============================================================
# Phase 1: is_first_reply in prompts
# ============================================================


class TestIsFirstReplyInPrompt:
    """Tests that is_first_reply is rendered into the prompt context."""

    def test_first_reply_true_renders_signal(self):
        """When is_first_reply=True, prompt should contain first reply signal."""
        lead_context = {
            "company": "Acme Corp",
            "title": "CEO",
            "is_first_reply": True,
        }
        result = build_lead_context_section(lead_context)
        assert "FIRST EVER reply" in result
        assert "Acme Corp" in result

    def test_first_reply_false_no_signal(self):
        """When is_first_reply=False, no first reply signal should appear."""
        lead_context = {
            "company": "Acme Corp",
            "title": "CEO",
            "is_first_reply": False,
        }
        result = build_lead_context_section(lead_context)
        assert "FIRST EVER reply" not in result
        assert "Acme Corp" in result

    def test_first_reply_missing_no_signal(self):
        """When is_first_reply is not in context, no first reply signal."""
        lead_context = {
            "company": "Acme Corp",
        }
        result = build_lead_context_section(lead_context)
        assert "FIRST EVER reply" not in result

    def test_first_reply_appears_before_company(self):
        """First reply signal should appear before company/title info."""
        lead_context = {
            "company": "Acme Corp",
            "title": "CEO",
            "is_first_reply": True,
        }
        result = build_lead_context_section(lead_context)
        first_idx = result.index("FIRST EVER reply")
        company_idx = result.index("Acme Corp")
        assert first_idx < company_idx


# ============================================================
# Phase 2: Core principles in all stage prompts
# ============================================================


class TestCorePrinciples:
    """Tests that core principles are prepended to all stage prompts."""

    def test_principles_content(self):
        """Principles should contain key frame concepts."""
        assert "Peer-to-peer" in CORE_PRINCIPLES
        assert "Qualify before pitching" in CORE_PRINCIPLES
        assert "Genuine curiosity" in CORE_PRINCIPLES
        assert "Match their energy" in CORE_PRINCIPLES
        assert "Every conversation is different" in CORE_PRINCIPLES
        assert "back off" in CORE_PRINCIPLES

    def test_positive_reply_has_principles(self):
        """positive_reply stage prompt should start with principles."""
        from app.prompts.stages.positive_reply import SYSTEM_PROMPT

        assert CORE_PRINCIPLES in SYSTEM_PROMPT
        assert "RAPPORT BUILDING" in SYSTEM_PROMPT

    def test_pitched_has_principles(self):
        """pitched stage prompt should start with principles."""
        from app.prompts.stages.pitched import SYSTEM_PROMPT

        assert CORE_PRINCIPLES in SYSTEM_PROMPT

    def test_calendar_sent_has_principles(self):
        """calendar_sent stage prompt should start with principles."""
        from app.prompts.stages.calendar_sent import SYSTEM_PROMPT

        assert CORE_PRINCIPLES in SYSTEM_PROMPT

    def test_booked_has_principles(self):
        """booked stage prompt should start with principles."""
        from app.prompts.stages.booked import SYSTEM_PROMPT

        assert CORE_PRINCIPLES in SYSTEM_PROMPT

    def test_regeneration_has_principles(self):
        """regeneration stage prompt should start with principles."""
        from app.prompts.stages.regeneration import SYSTEM_PROMPT

        assert CORE_PRINCIPLES in SYSTEM_PROMPT


# ============================================================
# Phase 3: Dynamic example retrieval
# ============================================================


class TestExtractLastLeadMessage:
    """Tests for _extract_last_lead_message utility."""

    def test_extracts_last_lead_message(self):
        history = [
            {"role": "you", "content": "Hey there"},
            {"role": "lead", "content": "First reply"},
            {"role": "you", "content": "Thanks!"},
            {"role": "lead", "content": "Second reply"},
        ]
        assert _extract_last_lead_message(history) == "Second reply"

    def test_returns_none_for_empty(self):
        assert _extract_last_lead_message(None) is None
        assert _extract_last_lead_message([]) is None

    def test_returns_none_when_no_lead_messages(self):
        history = [
            {"role": "you", "content": "Hey there"},
        ]
        assert _extract_last_lead_message(history) is None


class TestRankExamples:
    """Tests for example ranking logic."""

    def _make_example(self, message: str, is_first: bool = False, was_edited: bool = False) -> RetrievedExample:
        return RetrievedExample(
            lead_name="Test Lead",
            lead_message=message,
            draft_reply="Test reply",
            company=None,
            title=None,
            is_first_reply=is_first,
            funnel_stage=FunnelStage.POSITIVE_REPLY,
            was_edited=was_edited,
        )

    def test_first_reply_match_ranks_higher(self):
        """Examples matching is_first_reply should rank higher."""
        examples = [
            self._make_example("Some reply", is_first=False),
            self._make_example("Another reply", is_first=True),
        ]
        lead_context = {"is_first_reply": True}
        ranked = _rank_examples(examples, lead_context, "Test message")
        assert ranked[0].is_first_reply is True

    def test_unedited_ranks_higher(self):
        """Unedited examples (AI got it right) should rank higher."""
        examples = [
            self._make_example("Same message", was_edited=True),
            self._make_example("Same message", was_edited=False),
        ]
        lead_context = {"is_first_reply": False}
        ranked = _rank_examples(examples, lead_context, "Same message")
        assert ranked[0].was_edited is False

    def test_keyword_overlap_ranks_higher(self):
        """Examples with keyword overlap should rank higher."""
        examples = [
            self._make_example("We do marketing consulting"),
            self._make_example("We handle cybersecurity operations"),
        ]
        lead_context = {"is_first_reply": False}
        ranked = _rank_examples(examples, lead_context, "We offer marketing services")
        assert "marketing" in ranked[0].lead_message.lower()


class TestFormatExamplesForPrompt:
    """Tests for formatting examples into prompt text."""

    def test_empty_examples_returns_empty(self):
        assert format_examples_for_prompt([]) == ""

    def test_formats_single_example(self):
        examples = [
            RetrievedExample(
                lead_name="John",
                lead_message="We do cybersecurity",
                draft_reply="That's a growing space\nHow are you finding LinkedIn for leads?",
                company="SecureCo",
                title=None,
                is_first_reply=True,
                funnel_stage=FunnelStage.POSITIVE_REPLY,
            ),
        ]
        result = format_examples_for_prompt(examples)
        assert "Similar Past Conversations" in result
        assert "adapt, don't copy" in result
        assert "We do cybersecurity" in result
        assert "That's a growing space" in result
        assert "approved and sent" in result
        assert "first reply" in result

    def test_formats_multiple_examples(self):
        examples = [
            RetrievedExample(
                lead_name="John",
                lead_message="First message",
                draft_reply="First reply",
                company=None,
                title=None,
                is_first_reply=False,
                funnel_stage=FunnelStage.POSITIVE_REPLY,
            ),
            RetrievedExample(
                lead_name="Jane",
                lead_message="Second message",
                draft_reply="Second reply",
                company=None,
                title=None,
                is_first_reply=True,
                funnel_stage=FunnelStage.POSITIVE_REPLY,
            ),
        ]
        result = format_examples_for_prompt(examples)
        assert "Example 1" in result
        assert "Example 2" in result


class TestDynamicExamplesInPrompt:
    """Tests that dynamic examples are included in stage prompt output."""

    def test_positive_reply_includes_dynamic_examples(self):
        from app.prompts.stages.positive_reply import build_user_prompt

        examples_text = "## Similar Past Conversations\nExample 1: Test"
        result = build_user_prompt(
            lead_name="Test Lead",
            lead_message="Hello",
            dynamic_examples=examples_text,
        )
        assert "Similar Past Conversations" in result

    def test_positive_reply_empty_examples_ok(self):
        from app.prompts.stages.positive_reply import build_user_prompt

        result = build_user_prompt(
            lead_name="Test Lead",
            lead_message="Hello",
            dynamic_examples="",
        )
        assert "Similar Past Conversations" not in result

    def test_pitched_includes_dynamic_examples(self):
        from app.prompts.stages.pitched import build_user_prompt

        result = build_user_prompt(
            lead_name="Test",
            lead_message="Sounds good",
            dynamic_examples="## Similar Past Conversations\nExample 1: Test",
        )
        assert "Similar Past Conversations" in result


# ============================================================
# Phase 3: Database-level retrieval tests
# ============================================================


@pytest.mark.asyncio
class TestGetSimilarExamples:
    """Tests for get_similar_examples with a real (test) database."""

    async def test_returns_empty_when_no_approved_drafts(self, test_db_session: AsyncSession):
        """Should return empty list when no approved drafts exist."""
        result = await get_similar_examples(
            stage=FunnelStage.POSITIVE_REPLY,
            lead_context={"is_first_reply": True},
            current_lead_message="Hello",
            db=test_db_session,
        )
        assert result == []

    async def test_returns_examples_from_same_stage(self, test_db_session: AsyncSession):
        """Should return approved drafts from the same funnel stage."""
        # Create a conversation with history
        conv = Conversation(
            id=uuid.uuid4(),
            heyreach_lead_id="test_lead_1",
            linkedin_profile_url="https://linkedin.com/in/test1",
            lead_name="Test Lead",
            funnel_stage=FunnelStage.POSITIVE_REPLY,
            conversation_history=[
                {"role": "you", "content": "Hey, saw your profile"},
                {"role": "lead", "content": "Thanks for reaching out!"},
            ],
        )
        test_db_session.add(conv)
        await test_db_session.flush()

        draft = Draft(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            ai_draft="Of course\nIs LinkedIn a big channel for you?",
            status=DraftStatus.APPROVED,
            is_first_reply=True,
        )
        test_db_session.add(draft)
        await test_db_session.commit()

        result = await get_similar_examples(
            stage=FunnelStage.POSITIVE_REPLY,
            lead_context={"is_first_reply": True},
            current_lead_message="Thanks for connecting!",
            db=test_db_session,
        )
        assert len(result) == 1
        assert result[0].lead_message == "Thanks for reaching out!"
        assert "LinkedIn" in result[0].draft_reply

    async def test_does_not_return_wrong_stage(self, test_db_session: AsyncSession):
        """Should not return drafts from a different stage."""
        conv = Conversation(
            id=uuid.uuid4(),
            heyreach_lead_id="test_lead_2",
            linkedin_profile_url="https://linkedin.com/in/test2",
            lead_name="Test Lead 2",
            funnel_stage=FunnelStage.PITCHED,  # Different stage
            conversation_history=[
                {"role": "lead", "content": "Sounds interesting"},
            ],
        )
        test_db_session.add(conv)
        await test_db_session.flush()

        draft = Draft(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            ai_draft="Great, let's chat",
            status=DraftStatus.APPROVED,
            is_first_reply=False,
        )
        test_db_session.add(draft)
        await test_db_session.commit()

        result = await get_similar_examples(
            stage=FunnelStage.POSITIVE_REPLY,  # Querying different stage
            lead_context={},
            current_lead_message="Hello",
            db=test_db_session,
        )
        assert len(result) == 0

    async def test_uses_actual_sent_text_when_available(self, test_db_session: AsyncSession):
        """Should prefer actual_sent_text over ai_draft for examples."""
        conv = Conversation(
            id=uuid.uuid4(),
            heyreach_lead_id="test_lead_3",
            linkedin_profile_url="https://linkedin.com/in/test3",
            lead_name="Test Lead 3",
            funnel_stage=FunnelStage.POSITIVE_REPLY,
            conversation_history=[
                {"role": "lead", "content": "We do marketing"},
            ],
        )
        test_db_session.add(conv)
        await test_db_session.flush()

        draft = Draft(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            ai_draft="Original AI text",
            actual_sent_text="Edited human text",
            status=DraftStatus.APPROVED,
            is_first_reply=True,
        )
        test_db_session.add(draft)
        await test_db_session.commit()

        result = await get_similar_examples(
            stage=FunnelStage.POSITIVE_REPLY,
            lead_context={"is_first_reply": True},
            current_lead_message="We do marketing too",
            db=test_db_session,
        )
        assert len(result) == 1
        assert result[0].draft_reply == "Edited human text"
        assert result[0].was_edited is True


# ============================================================
# Phase 4: Learning loop
# ============================================================


class TestActualSentText:
    """Tests for actual_sent_text on Draft model."""

    def test_draft_has_actual_sent_text_field(self):
        """Draft model should have actual_sent_text field."""
        draft = Draft(
            id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            ai_draft="AI generated text",
            actual_sent_text="Human edited text",
            status=DraftStatus.APPROVED,
        )
        assert draft.actual_sent_text == "Human edited text"
        assert draft.ai_draft == "AI generated text"

    def test_actual_sent_text_nullable(self):
        """actual_sent_text should be nullable for older drafts."""
        draft = Draft(
            id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            ai_draft="AI generated text",
            status=DraftStatus.PENDING,
        )
        assert draft.actual_sent_text is None
