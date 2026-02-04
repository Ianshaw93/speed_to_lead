"""Tests for stage-specific prompts."""

import pytest

from app.prompts.stages import (
    get_stage_prompt,
    STAGE_PROMPTS,
)
from app.models import FunnelStage


class TestStagePrompts:
    """Tests for stage-specific prompt module."""

    def test_all_stages_have_prompts(self):
        """Every funnel stage should have a corresponding prompt."""
        # Skip INITIATED as it doesn't need a draft generation prompt
        for stage in FunnelStage:
            if stage == FunnelStage.INITIATED:
                continue
            assert stage in STAGE_PROMPTS, f"Missing prompt for stage: {stage}"

    def test_get_stage_prompt_returns_correct_module(self):
        """get_stage_prompt should return the correct prompt module for each stage."""
        for stage in FunnelStage:
            if stage == FunnelStage.INITIATED:
                continue
            prompt_module = get_stage_prompt(stage)
            assert hasattr(prompt_module, "SYSTEM_PROMPT")
            assert hasattr(prompt_module, "build_user_prompt")

    def test_positive_reply_prompt(self):
        """positive_reply prompt should focus on building rapport."""
        prompt_module = get_stage_prompt(FunnelStage.POSITIVE_REPLY)
        assert "rapport" in prompt_module.SYSTEM_PROMPT.lower()

        user_prompt = prompt_module.build_user_prompt(
            lead_name="John",
            lead_message="Sounds interesting!",
        )
        assert "John" in user_prompt
        assert "Sounds interesting" in user_prompt

    def test_pitched_prompt(self):
        """pitched prompt should focus on addressing objections."""
        prompt_module = get_stage_prompt(FunnelStage.PITCHED)
        system = prompt_module.SYSTEM_PROMPT.lower()
        assert "objection" in system or "hesitation" in system or "value" in system

    def test_calendar_sent_prompt(self):
        """calendar_sent prompt should focus on confirming meeting."""
        prompt_module = get_stage_prompt(FunnelStage.CALENDAR_SENT)
        system = prompt_module.SYSTEM_PROMPT.lower()
        assert "confirm" in system or "meeting" in system or "book" in system

    def test_booked_prompt(self):
        """booked prompt should focus on meeting prep."""
        prompt_module = get_stage_prompt(FunnelStage.BOOKED)
        system = prompt_module.SYSTEM_PROMPT.lower()
        assert "meeting" in system or "confirm" in system or "prep" in system

    def test_regeneration_prompt(self):
        """regeneration prompt should focus on re-engagement without desperation."""
        prompt_module = get_stage_prompt(FunnelStage.REGENERATION)
        system = prompt_module.SYSTEM_PROMPT.lower()
        assert "value" in system or "re-engage" in system or "nurtur" in system

    def test_build_user_prompt_with_history(self):
        """All stage prompts should support conversation history."""
        history = [
            {"role": "you", "content": "Initial message"},
            {"role": "lead", "content": "Reply"},
        ]

        for stage in FunnelStage:
            if stage == FunnelStage.INITIATED:
                continue
            prompt_module = get_stage_prompt(stage)
            user_prompt = prompt_module.build_user_prompt(
                lead_name="Test",
                lead_message="Latest message",
                conversation_history=history,
            )
            assert "Test" in user_prompt
            assert "Latest message" in user_prompt
            # History should be included
            assert "Initial message" in user_prompt

    def test_build_user_prompt_with_guidance(self):
        """All stage prompts should support guidance parameter."""
        for stage in FunnelStage:
            if stage == FunnelStage.INITIATED:
                continue
            prompt_module = get_stage_prompt(stage)
            user_prompt = prompt_module.build_user_prompt(
                lead_name="Test",
                lead_message="Message",
                guidance="Be more casual",
            )
            assert "Be more casual" in user_prompt
