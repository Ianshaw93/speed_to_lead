"""Tests for Learning Agent service."""

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.learning_agent import (
    SEED_GUIDELINES,
    analyze_edit,
)


class TestAnalyzeEdit:
    """Tests for edit analysis."""

    @pytest.mark.asyncio
    async def test_analyze_tone_change(self):
        """Should detect tone changes in edits."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "learnings": [
                {
                    "type": "tone",
                    "original_snippet": "Dear John, thank you for your message.",
                    "corrected_snippet": "hey John ya sounds good",
                    "explanation": "Changed from formal to casual text-message tone for positive_reply stage.",
                    "confidence": 0.9,
                }
            ]
        }))]
        mock_response.usage = MagicMock(input_tokens=500, output_tokens=150)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("app.services.learning_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            learnings = await analyze_edit(
                original_text="Dear John, thank you for your message. I appreciate your interest.",
                edited_text="hey John ya sounds good let's chat about it",
                stage="positive_reply",
            )

        assert len(learnings) == 1
        assert learnings[0]["type"] == "tone"
        assert learnings[0]["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_analyze_product_addition(self):
        """Should detect when human adds product context."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "learnings": [
                {
                    "type": "product_knowledge",
                    "original_snippet": "That's a great question! Let me tell you more.",
                    "corrected_snippet": "ya so we help businesses get 5-10 new clients per month through LinkedIn",
                    "explanation": "Human added specific product context about LinkedIn client acquisition.",
                    "confidence": 0.95,
                }
            ]
        }))]
        mock_response.usage = MagicMock(input_tokens=400, output_tokens=100)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("app.services.learning_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            learnings = await analyze_edit(
                original_text="That's a great question! Let me tell you more.",
                edited_text="ya so we help businesses get 5-10 new clients per month through LinkedIn",
                stage="positive_reply",
            )

        assert len(learnings) == 1
        assert learnings[0]["type"] == "product_knowledge"

    @pytest.mark.asyncio
    async def test_trivial_edit_returns_empty(self):
        """Should return empty list for trivial edits."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "learnings": []
        }))]
        mock_response.usage = MagicMock(input_tokens=300, output_tokens=30)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("app.services.learning_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            learnings = await analyze_edit(
                original_text="Hey John, sounds great!",
                edited_text="Hey John, sounds great",  # Just removed exclamation
                stage="positive_reply",
            )

        assert len(learnings) == 0

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self):
        """Should return empty list on API failure (graceful degradation)."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))

        with patch("app.services.learning_agent.anthropic.AsyncAnthropic", return_value=mock_client):
            learnings = await analyze_edit(
                original_text="Hello",
                edited_text="Hi",
                stage="positive_reply",
            )

        assert len(learnings) == 0


class TestSeedGuidelines:
    """Tests for seed guideline data."""

    def test_seed_guidelines_valid(self):
        """Seed guidelines should have required fields."""
        for seed in SEED_GUIDELINES:
            assert "stage" in seed
            assert "guideline_type" in seed
            assert "content" in seed
            assert seed["guideline_type"] in ("do", "dont", "example", "tone_rule")
            assert len(seed["content"]) > 10  # Not trivially short

    def test_seed_guidelines_cover_key_issues(self):
        """Seed guidelines should address known P0/P1 issues."""
        all_content = " ".join(g["content"] for g in SEED_GUIDELINES)

        # P0: product context gap
        assert "what do you do" in all_content.lower()
        # P1: tone consistency
        assert "casual" in all_content.lower() or "text-message" in all_content.lower()
        # P1: stop messaging detection
        assert "not interested" in all_content.lower() or "stop messaging" in all_content.lower()
