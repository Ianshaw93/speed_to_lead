"""Tests for QA Agent service."""

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.qa_agent import (
    QAAgentError,
    QAResult,
    _build_qa_prompt,
    _estimate_cost,
    qa_check_draft,
)


class TestBuildQAPrompt:
    """Tests for QA prompt construction."""

    def test_basic_prompt(self):
        """Should build a prompt with all required fields."""
        prompt = _build_qa_prompt(
            lead_name="John Doe",
            lead_message="Sounds interesting, what do you do?",
            ai_draft="Hey John! We help businesses...",
            detected_stage="positive_reply",
        )
        assert "John Doe" in prompt
        assert "positive_reply" in prompt
        assert "what do you do?" in prompt
        assert "We help businesses" in prompt

    def test_prompt_with_history(self):
        """Should include conversation history when provided."""
        history = [
            {"role": "you", "content": "Hi John, saw your post!"},
            {"role": "lead", "content": "Thanks! What's this about?"},
        ]
        prompt = _build_qa_prompt(
            lead_name="John Doe",
            lead_message="Thanks! What's this about?",
            ai_draft="We help with LinkedIn...",
            detected_stage="positive_reply",
            conversation_history=history,
        )
        assert "Conversation History" in prompt
        assert "saw your post!" in prompt

    def test_prompt_with_guidelines(self):
        """Should include QA guidelines when provided."""
        guidelines = [
            {"guideline_type": "do", "content": "Always mention LinkedIn client acquisition"},
            {"guideline_type": "dont", "content": "Never use formal greetings"},
        ]
        prompt = _build_qa_prompt(
            lead_name="John",
            lead_message="Hi",
            ai_draft="Hello!",
            detected_stage="positive_reply",
            guidelines=guidelines,
        )
        assert "QA Guidelines" in prompt
        assert "LinkedIn client acquisition" in prompt
        assert "formal greetings" in prompt

    def test_prompt_limits_guidelines(self):
        """Should cap guidelines at MAX_GUIDELINES_PER_STAGE."""
        guidelines = [
            {"guideline_type": "do", "content": f"Guideline {i}"}
            for i in range(20)
        ]
        prompt = _build_qa_prompt(
            lead_name="John",
            lead_message="Hi",
            ai_draft="Hello!",
            detected_stage="positive_reply",
            guidelines=guidelines,
        )
        # Should only include up to 15
        assert "Guideline 14" in prompt
        assert "Guideline 15" not in prompt


class TestEstimateCost:
    """Tests for cost estimation."""

    def test_cost_calculation(self):
        """Should calculate cost based on Sonnet pricing."""
        cost = _estimate_cost(input_tokens=1000, output_tokens=200)
        # 1000 * $0.000003 + 200 * $0.000015 = $0.003 + $0.003 = $0.006
        assert cost == Decimal("0.006000")

    def test_zero_tokens(self):
        """Should return zero for zero tokens."""
        cost = _estimate_cost(input_tokens=0, output_tokens=0)
        assert cost == Decimal("0.000000")


class TestQACheckDraft:
    """Tests for the main QA check function."""

    @pytest.mark.asyncio
    async def test_passing_draft(self):
        """Should return pass verdict for good drafts."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "score": 4.5,
            "verdict": "pass",
            "issues": [],
            "should_not_reply": False,
            "reasoning": "Good casual tone, answers the question."
        }))]
        mock_response.usage = MagicMock(input_tokens=500, output_tokens=100)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("app.services.qa_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await qa_check_draft(
                lead_name="John Doe",
                lead_message="Sounds interesting!",
                ai_draft="hey John ya we help businesses get clients on LinkedIn",
                detected_stage="positive_reply",
            )

        assert result.score == 4.5
        assert result.verdict == "pass"
        assert result.should_not_reply is False
        assert len(result.issues) == 0
        assert result.cost_usd > Decimal("0")

    @pytest.mark.asyncio
    async def test_flagged_draft(self):
        """Should return flag verdict for score 3.0-3.9."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "score": 3.5,
            "verdict": "flag",
            "issues": [
                {"type": "tone", "detail": "Slightly too formal", "severity": "low"}
            ],
            "should_not_reply": False,
            "reasoning": "Acceptable but tone could be more casual."
        }))]
        mock_response.usage = MagicMock(input_tokens=500, output_tokens=150)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("app.services.qa_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await qa_check_draft(
                lead_name="Jane Smith",
                lead_message="Tell me more",
                ai_draft="Dear Jane, thank you for your interest...",
                detected_stage="positive_reply",
            )

        assert result.score == 3.5
        assert result.verdict == "flag"
        assert len(result.issues) == 1
        assert result.issues[0].type == "tone"

    @pytest.mark.asyncio
    async def test_blocked_draft(self):
        """Should return block verdict for score < 3.0."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "score": 2.0,
            "verdict": "block",
            "issues": [
                {"type": "product", "detail": "Doesn't answer the question", "severity": "high"},
                {"type": "tone", "detail": "Too formal for this stage", "severity": "medium"},
            ],
            "should_not_reply": False,
            "reasoning": "Major issues: doesn't answer what the company does."
        }))]
        mock_response.usage = MagicMock(input_tokens=600, output_tokens=200)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("app.services.qa_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await qa_check_draft(
                lead_name="Bob",
                lead_message="What do you do?",
                ai_draft="That's a great question! Let's connect.",
                detected_stage="positive_reply",
            )

        assert result.score == 2.0
        assert result.verdict == "block"
        assert len(result.issues) == 2

    @pytest.mark.asyncio
    async def test_should_not_reply_forces_block(self):
        """Should force score=1.0 and block when should_not_reply is true."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "score": 3.5,  # Even if score is OK
            "verdict": "flag",
            "issues": [
                {"type": "stop_detection", "detail": "Lead said stop messaging", "severity": "high"}
            ],
            "should_not_reply": True,
            "reasoning": "Lead explicitly asked to stop messaging."
        }))]
        mock_response.usage = MagicMock(input_tokens=400, output_tokens=120)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("app.services.qa_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await qa_check_draft(
                lead_name="Mike",
                lead_message="Please stop messaging me",
                ai_draft="Sorry to hear that! Let me share one more thing...",
                detected_stage="positive_reply",
            )

        # should_not_reply overrides score to 1.0 and verdict to block
        assert result.score == 1.0
        assert result.verdict == "block"
        assert result.should_not_reply is True

    @pytest.mark.asyncio
    async def test_api_error_raises(self):
        """Should raise QAAgentError on API failure."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))

        with patch("app.services.qa_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            with pytest.raises(QAAgentError, match="API down"):
                await qa_check_draft(
                    lead_name="Test",
                    lead_message="Hi",
                    ai_draft="Hello!",
                    detected_stage="positive_reply",
                )

    @pytest.mark.asyncio
    async def test_handles_markdown_fenced_json(self):
        """Should parse JSON even when wrapped in markdown fences."""
        json_str = json.dumps({
            "score": 4.0,
            "verdict": "pass",
            "issues": [],
            "should_not_reply": False,
            "reasoning": "Looks good."
        })
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=f"```json\n{json_str}\n```")]
        mock_response.usage = MagicMock(input_tokens=300, output_tokens=80)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("app.services.qa_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await qa_check_draft(
                lead_name="Test",
                lead_message="Hi",
                ai_draft="Hey!",
                detected_stage="positive_reply",
            )

        assert result.score == 4.0
        assert result.verdict == "pass"


class TestQAAnnotation:
    """Tests for Slack QA annotation blocks."""

    def test_pass_annotation(self):
        """Should show green circle for passing drafts."""
        from app.services.slack import build_qa_annotation

        blocks = build_qa_annotation(qa_score=4.5, qa_verdict="pass")
        block_text = str(blocks)
        assert "large_green_circle" in block_text
        assert "4.5" in block_text

    def test_flag_annotation_with_issues(self):
        """Should show yellow circle and issue details for flagged drafts."""
        from app.services.slack import build_qa_annotation

        issues = [
            {"type": "tone", "detail": "Too formal", "severity": "medium"},
        ]
        blocks = build_qa_annotation(qa_score=3.5, qa_verdict="flag", qa_issues=issues)
        block_text = str(blocks)
        assert "large_yellow_circle" in block_text
        assert "Too formal" in block_text

    def test_no_issues_for_passing_draft(self):
        """Should not show issue details for clean pass."""
        from app.services.slack import build_qa_annotation

        blocks = build_qa_annotation(qa_score=4.5, qa_verdict="pass", qa_issues=[])
        # Should only be divider + score badge (2 blocks), no issue detail block
        assert len(blocks) == 2
