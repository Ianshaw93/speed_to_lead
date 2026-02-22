"""Tests for judge service — weighted scoring, API calls, parse errors."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.prompts.judge import DIMENSION_WEIGHTS, build_judge_prompt
from app.services.judge import (
    SCORE_THRESHOLD,
    JudgeError,
    JudgeResult,
    compute_weighted_score,
    judge_draft,
)


class TestComputeWeightedScore:
    """Tests for weighted score computation."""

    def test_perfect_scores(self):
        """All 5s should return 5.0."""
        scores = {dim: 5.0 for dim in DIMENSION_WEIGHTS}
        assert compute_weighted_score(scores) == 5.0

    def test_all_ones(self):
        """All 1s should return 1.0."""
        scores = {dim: 1.0 for dim in DIMENSION_WEIGHTS}
        assert compute_weighted_score(scores) == 1.0

    def test_weighted_average(self):
        """Verify correct weighted calculation."""
        scores = {
            "contextual_relevance": 5.0,  # 0.30 * 5 = 1.50
            "personalization": 4.0,       # 0.25 * 4 = 1.00
            "tone": 3.0,                  # 0.20 * 3 = 0.60
            "cta_quality": 2.0,           # 0.15 * 2 = 0.30
            "authenticity": 1.0,          # 0.10 * 1 = 0.10
        }
        # Expected: 1.50 + 1.00 + 0.60 + 0.30 + 0.10 = 3.50
        assert compute_weighted_score(scores) == 3.50

    def test_missing_dimension_treated_as_zero(self):
        """Missing dimensions should be treated as 0."""
        scores = {"contextual_relevance": 5.0}
        # 0.30 * 5 = 1.50, everything else 0
        assert compute_weighted_score(scores) == 1.50

    def test_weights_sum_to_one(self):
        """Weights must sum to 1.0 for correct averaging."""
        assert abs(sum(DIMENSION_WEIGHTS.values()) - 1.0) < 0.001

    def test_threshold_is_strict(self):
        """Score threshold should be 4.0."""
        assert SCORE_THRESHOLD == 4.0


class TestBuildJudgePrompt:
    """Tests for judge prompt construction."""

    def test_includes_lead_name(self):
        prompt = build_judge_prompt(
            lead_name="Jane",
            lead_message="Hi there",
            ai_draft="Hey Jane!",
        )
        assert "Jane" in prompt

    def test_includes_draft(self):
        prompt = build_judge_prompt(
            lead_name="Jane",
            lead_message="Hi there",
            ai_draft="This is the draft reply",
        )
        assert "This is the draft reply" in prompt

    def test_includes_lead_context(self):
        prompt = build_judge_prompt(
            lead_name="Jane",
            lead_message="Hi there",
            ai_draft="Hey!",
            lead_context={"company": "Acme Corp", "title": "CEO"},
        )
        assert "Acme Corp" in prompt
        assert "CEO" in prompt

    def test_includes_conversation_history(self):
        history = [
            {"role": "lead", "content": "First message", "time": "10:00"},
            {"role": "you", "content": "Our reply", "time": "10:05"},
        ]
        prompt = build_judge_prompt(
            lead_name="Jane",
            lead_message="Follow up",
            ai_draft="New reply",
            conversation_history=history,
        )
        assert "First message" in prompt
        assert "Our reply" in prompt


class TestJudgeDraft:
    """Tests for the judge_draft async function."""

    @pytest.mark.asyncio
    async def test_successful_judgment(self):
        """Should return JudgeResult with parsed scores and feedback."""
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps({
                    "scores": {
                        "contextual_relevance": 4,
                        "personalization": 3,
                        "tone": 5,
                        "cta_quality": 4,
                        "authenticity": 4,
                    },
                    "feedback": "Good but could reference their product more.",
                })
            )
        ]

        with patch("app.services.judge.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await judge_draft(
                lead_name="John",
                lead_message="We help clinics grow.",
                ai_draft="Thanks for sharing!",
            )

            assert isinstance(result, JudgeResult)
            assert result.scores["tone"] == 5.0
            assert result.feedback == "Good but could reference their product more."
            # Verify weighted score: 4*0.30 + 3*0.25 + 5*0.20 + 4*0.15 + 4*0.10
            # = 1.20 + 0.75 + 1.00 + 0.60 + 0.40 = 3.95
            assert result.weighted_score == 3.95

    @pytest.mark.asyncio
    async def test_api_error_raises_judge_error(self):
        """Should raise JudgeError when Anthropic API fails."""
        with patch("app.services.judge.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create.side_effect = Exception("API down")
            mock_cls.return_value = mock_client

            with pytest.raises(JudgeError, match="Anthropic API error"):
                await judge_draft(
                    lead_name="John",
                    lead_message="Hi",
                    ai_draft="Hey!",
                )

    @pytest.mark.asyncio
    async def test_json_parse_error_raises_judge_error(self):
        """Should raise JudgeError when response isn't valid JSON."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not json at all")]

        with patch("app.services.judge.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create.return_value = mock_response
            mock_cls.return_value = mock_client

            with pytest.raises(JudgeError, match="Failed to parse"):
                await judge_draft(
                    lead_name="John",
                    lead_message="Hi",
                    ai_draft="Hey!",
                )

    @pytest.mark.asyncio
    async def test_missing_dimension_raises_judge_error(self):
        """Should raise JudgeError when a scoring dimension is missing."""
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps({
                    "scores": {
                        "contextual_relevance": 4,
                        # Missing other dimensions
                    },
                    "feedback": "Partial response.",
                })
            )
        ]

        with patch("app.services.judge.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create.return_value = mock_response
            mock_cls.return_value = mock_client

            with pytest.raises(JudgeError, match="Missing dimension"):
                await judge_draft(
                    lead_name="John",
                    lead_message="Hi",
                    ai_draft="Hey!",
                )

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        """Should handle responses wrapped in markdown code fences."""
        scores_json = json.dumps({
            "scores": {
                "contextual_relevance": 4,
                "personalization": 4,
                "tone": 4,
                "cta_quality": 4,
                "authenticity": 4,
            },
            "feedback": "Solid draft.",
        })
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text=f"```json\n{scores_json}\n```")
        ]

        with patch("app.services.judge.anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await judge_draft(
                lead_name="John",
                lead_message="Hi",
                ai_draft="Hey!",
            )

            assert result.weighted_score == 4.0
            assert result.feedback == "Solid draft."
