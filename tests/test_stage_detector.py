"""Tests for stage detection prompt."""

import pytest

from app.prompts.stage_detector import (
    STAGE_DETECTION_SYSTEM_PROMPT,
    build_stage_detection_prompt,
)


class TestStageDetectionPrompt:
    """Tests for the stage detection prompt module."""

    def test_system_prompt_exists(self):
        """STAGE_DETECTION_SYSTEM_PROMPT should exist and be non-empty."""
        assert STAGE_DETECTION_SYSTEM_PROMPT
        assert len(STAGE_DETECTION_SYSTEM_PROMPT) > 100
        assert "stage" in STAGE_DETECTION_SYSTEM_PROMPT.lower()

    def test_system_prompt_includes_all_stages(self):
        """System prompt should mention all funnel stages."""
        stages = [
            "initiated",
            "positive_reply",
            "pitched",
            "calendar_sent",
            "booked",
            "regeneration",
        ]
        for stage in stages:
            assert stage in STAGE_DETECTION_SYSTEM_PROMPT.lower(), f"Missing stage: {stage}"

    def test_system_prompt_requests_json(self):
        """System prompt should instruct JSON output."""
        assert "json" in STAGE_DETECTION_SYSTEM_PROMPT.lower()

    def test_build_prompt_basic(self):
        """Should build a basic prompt with lead name and message."""
        prompt = build_stage_detection_prompt(
            lead_name="John Doe",
            lead_message="Thanks for reaching out!",
        )

        assert "John Doe" in prompt
        assert "Thanks for reaching out!" in prompt

    def test_build_prompt_with_history(self):
        """Should include conversation history when provided."""
        history = [
            {"role": "you", "content": "Hi, saw your work at Acme..."},
            {"role": "lead", "content": "Thanks! Tell me more."},
        ]

        prompt = build_stage_detection_prompt(
            lead_name="Jane Smith",
            lead_message="Tell me more.",
            conversation_history=history,
        )

        assert "Jane Smith" in prompt
        assert "Hi, saw your work at Acme" in prompt
        assert "Thanks! Tell me more" in prompt

    def test_build_prompt_empty_history(self):
        """Should handle empty conversation history gracefully."""
        prompt = build_stage_detection_prompt(
            lead_name="Test User",
            lead_message="Hello!",
            conversation_history=[],
        )

        assert "Test User" in prompt
        assert "Hello!" in prompt

    def test_build_prompt_history_with_timestamps(self):
        """Should include timestamps when present in history."""
        history = [
            {"role": "you", "content": "Initial outreach", "time": "Jan 15"},
            {"role": "lead", "content": "Sounds interesting", "time": "Jan 16"},
        ]

        prompt = build_stage_detection_prompt(
            lead_name="Test",
            lead_message="Sounds interesting",
            conversation_history=history,
        )

        assert "Jan 15" in prompt
        assert "Jan 16" in prompt
