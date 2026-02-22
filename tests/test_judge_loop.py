"""Tests for the judge loop integration in deepseek.py."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import FunnelStage
from app.services.deepseek import DraftResult, generate_reply_draft_with_judgment
from app.services.judge import JudgeError, JudgeResult


def _make_judge_result(score: float, feedback: str = "test feedback") -> JudgeResult:
    """Helper to create a JudgeResult with uniform scores."""
    scores = {
        "contextual_relevance": score,
        "personalization": score,
        "tone": score,
        "cta_quality": score,
        "authenticity": score,
    }
    return JudgeResult(
        scores=scores,
        weighted_score=score,
        feedback=feedback,
    )


def _make_draft_result(reply: str = "Initial draft") -> DraftResult:
    """Helper to create a DraftResult."""
    return DraftResult(
        detected_stage=FunnelStage.POSITIVE_REPLY,
        stage_reasoning="Lead showed interest",
        reply=reply,
    )


class TestJudgeLoop:
    """Tests for generate_reply_draft_with_judgment."""

    @pytest.mark.asyncio
    async def test_high_score_no_revision(self):
        """Draft scoring >= 4.0 should not trigger revision."""
        draft = _make_draft_result("Great initial draft")
        judge = _make_judge_result(4.5, "Excellent draft, no changes needed.")

        with (
            patch("app.services.deepseek.get_deepseek_client") as mock_get_client,
            patch("app.services.judge.judge_draft", new_callable=AsyncMock) as mock_judge,
        ):
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = draft
            mock_get_client.return_value = mock_client

            mock_judge.return_value = judge

            result = await generate_reply_draft_with_judgment(
                lead_name="Jane",
                lead_message="I'm interested",
            )

            assert result.reply == "Great initial draft"
            assert result.judge_score == 4.5
            assert result.revision_count == 0
            # generate_with_stage should NOT be called (no revision)
            mock_client.generate_with_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_score_triggers_revision(self):
        """Draft scoring < 4.0 should trigger one revision."""
        draft = _make_draft_result("Generic draft")
        low_judge = _make_judge_result(2.5, "Too generic. Reference their product.")
        revised_judge = _make_judge_result(4.2, "Much better after revision.")

        with (
            patch("app.services.deepseek.get_deepseek_client") as mock_get_client,
            patch("app.services.judge.judge_draft", new_callable=AsyncMock) as mock_judge,
        ):
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = draft
            mock_client.generate_with_stage.return_value = "Revised draft mentioning their product"
            mock_get_client.return_value = mock_client

            mock_judge.side_effect = [low_judge, revised_judge]

            result = await generate_reply_draft_with_judgment(
                lead_name="Jane",
                lead_message="We help clinics grow",
            )

            assert result.reply == "Revised draft mentioning their product"
            assert result.judge_score == 4.2
            assert result.revision_count == 1
            # Verify revision was called with judge feedback as guidance
            mock_client.generate_with_stage.assert_called_once()
            call_kwargs = mock_client.generate_with_stage.call_args
            assert call_kwargs.kwargs.get("guidance") == "Too generic. Reference their product."

    @pytest.mark.asyncio
    async def test_revision_keeps_better_version(self):
        """If revised draft scores lower than original, keep original."""
        draft = _make_draft_result("Decent draft")
        first_judge = _make_judge_result(3.5, "Could improve personalization.")
        worse_judge = _make_judge_result(3.0, "Revision made it worse.")

        with (
            patch("app.services.deepseek.get_deepseek_client") as mock_get_client,
            patch("app.services.judge.judge_draft", new_callable=AsyncMock) as mock_judge,
        ):
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = draft
            mock_client.generate_with_stage.return_value = "Worse revised draft"
            mock_get_client.return_value = mock_client

            mock_judge.side_effect = [first_judge, worse_judge]

            result = await generate_reply_draft_with_judgment(
                lead_name="Jane",
                lead_message="Hi",
            )

            # Should keep original since revision scored lower
            assert result.reply == "Decent draft"
            assert result.judge_score == 3.5
            assert result.revision_count == 1

    @pytest.mark.asyncio
    async def test_judge_failure_returns_unjudged_draft(self):
        """If judge fails entirely, draft should flow through without score."""
        draft = _make_draft_result("Unjudged draft")

        with (
            patch("app.services.deepseek.get_deepseek_client") as mock_get_client,
            patch("app.services.judge.judge_draft", new_callable=AsyncMock) as mock_judge,
        ):
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = draft
            mock_get_client.return_value = mock_client

            mock_judge.side_effect = JudgeError("API unavailable")

            result = await generate_reply_draft_with_judgment(
                lead_name="Jane",
                lead_message="Hi",
            )

            assert result.reply == "Unjudged draft"
            assert result.judge_score is None
            assert result.judge_feedback is None
            assert result.revision_count == 0

    @pytest.mark.asyncio
    async def test_rejudge_failure_keeps_revised_draft(self):
        """If re-judge fails after revision, keep the revised draft with original score."""
        draft = _make_draft_result("Original draft")
        low_judge = _make_judge_result(2.0, "Needs work.")

        with (
            patch("app.services.deepseek.get_deepseek_client") as mock_get_client,
            patch("app.services.judge.judge_draft", new_callable=AsyncMock) as mock_judge,
        ):
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = draft
            mock_client.generate_with_stage.return_value = "Revised but unjudged"
            mock_get_client.return_value = mock_client

            # First judge succeeds, re-judge fails
            mock_judge.side_effect = [low_judge, JudgeError("API flaky")]

            result = await generate_reply_draft_with_judgment(
                lead_name="Jane",
                lead_message="Hi",
            )

            assert result.reply == "Revised but unjudged"
            assert result.judge_score == 2.0  # Original score retained
            assert result.revision_count == 1

    @pytest.mark.asyncio
    async def test_max_one_revision(self):
        """Should never revise more than once even if score stays low."""
        draft = _make_draft_result("Bad draft")
        low_judge = _make_judge_result(2.0, "Bad.")
        still_low = _make_judge_result(2.5, "Still bad.")

        with (
            patch("app.services.deepseek.get_deepseek_client") as mock_get_client,
            patch("app.services.judge.judge_draft", new_callable=AsyncMock) as mock_judge,
        ):
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = draft
            mock_client.generate_with_stage.return_value = "Slightly better"
            mock_get_client.return_value = mock_client

            mock_judge.side_effect = [low_judge, still_low]

            result = await generate_reply_draft_with_judgment(
                lead_name="Jane",
                lead_message="Hi",
            )

            # Only one revision attempt
            assert result.revision_count == 1
            assert mock_client.generate_with_stage.call_count == 1

    @pytest.mark.asyncio
    async def test_draft_result_fields_populated(self):
        """DraftResult should have all judge fields set."""
        draft = _make_draft_result()
        judge = _make_judge_result(4.3, "Good draft.")

        with (
            patch("app.services.deepseek.get_deepseek_client") as mock_get_client,
            patch("app.services.judge.judge_draft", new_callable=AsyncMock) as mock_judge,
        ):
            mock_client = AsyncMock()
            mock_client.generate_draft.return_value = draft
            mock_get_client.return_value = mock_client

            mock_judge.return_value = judge

            result = await generate_reply_draft_with_judgment(
                lead_name="Jane",
                lead_message="Hi",
            )

            assert result.detected_stage == FunnelStage.POSITIVE_REPLY
            assert result.stage_reasoning == "Lead showed interest"
            assert result.judge_score == 4.3
            assert result.judge_feedback == "Good draft."
            assert result.revision_count == 0
