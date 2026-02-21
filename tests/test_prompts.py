"""Tests for prompt utilities and stage prompts."""

import pytest

from app.prompts.utils import build_history_section, build_lead_context_section
from app.prompts.stages.positive_reply import (
    SYSTEM_PROMPT as POSITIVE_REPLY_SYSTEM_PROMPT,
    build_user_prompt as positive_reply_prompt,
)
from app.prompts.stages.pitched import build_user_prompt as pitched_prompt
from app.prompts.stages.calendar_sent import build_user_prompt as calendar_sent_prompt
from app.prompts.stages.booked import build_user_prompt as booked_prompt
from app.prompts.stages.regeneration import build_user_prompt as regeneration_prompt
from app.prompts.stage_detector import build_stage_detection_prompt


class TestBuildHistorySection:
    """Tests for build_history_section utility."""

    def test_empty_history_returns_no_messages(self):
        """Should return 'No previous messages.' for empty/None history."""
        assert build_history_section(None) == "No previous messages."
        assert build_history_section([]) == "No previous messages."

    def test_lead_role_gets_lead_prefix(self):
        """Messages with role='lead' should get **Lead:** prefix."""
        history = [{"role": "lead", "content": "Hello there"}]
        result = build_history_section(history)
        assert "**Lead:**" in result
        assert "Hello there" in result

    def test_you_role_gets_you_prefix(self):
        """Messages with role='you' should get **You:** prefix."""
        history = [{"role": "you", "content": "Hi, thanks for connecting"}]
        result = build_history_section(history)
        assert "**You:**" in result
        assert "Hi, thanks for connecting" in result

    def test_mixed_roles_correctly_attributed(self):
        """Conversation with both roles should have correct attribution."""
        history = [
            {"role": "you", "content": "Hey, saw your post about AI"},
            {"role": "lead", "content": "Thanks! Yeah we're big on AI"},
            {"role": "you", "content": "That's awesome"},
        ]
        result = build_history_section(history)
        lines = result.strip().split("\n")
        assert lines[0].startswith("**You:**")
        assert lines[1].startswith("**Lead:**")
        assert lines[2].startswith("**You:**")

    def test_time_included_when_present(self):
        """Should include timestamp in brackets when provided."""
        history = [
            {"role": "lead", "content": "Hello", "time": "2026-02-20T10:00:00"}
        ]
        result = build_history_section(history)
        assert "[2026-02-20T10:00:00]" in result

    def test_time_omitted_when_absent(self):
        """Should not include brackets when time is not present."""
        history = [{"role": "lead", "content": "Hello"}]
        result = build_history_section(history)
        assert "[" not in result


class TestBuildLeadContextSection:
    """Tests for build_lead_context_section utility."""

    def test_none_returns_empty(self):
        """Should return empty string for None context."""
        assert build_lead_context_section(None) == ""

    def test_empty_dict_returns_empty(self):
        """Should return empty string for empty dict."""
        assert build_lead_context_section({}) == ""

    def test_company_rendered(self):
        """Should render company name."""
        result = build_lead_context_section({"company": "Acme Corp"})
        assert "**Company:** Acme Corp" in result

    def test_title_rendered(self):
        """Should render job title."""
        result = build_lead_context_section({"title": "CEO"})
        assert "**Title:** CEO" in result

    def test_triggering_message_rendered(self):
        """Should render triggering message with section header."""
        result = build_lead_context_section(
            {"triggering_message": "Hey, saw your post!"}
        )
        assert "## Our Last Message To Them" in result
        assert "Hey, saw your post!" in result

    def test_personalized_message_rendered(self):
        """Should render personalized message with section header."""
        result = build_lead_context_section(
            {"personalized_message": "Custom outreach for you"}
        )
        assert "## Original Outreach Message" in result
        assert "Custom outreach for you" in result

    def test_full_context(self):
        """Should render all fields when provided."""
        result = build_lead_context_section({
            "company": "TechCo",
            "title": "VP Engineering",
            "triggering_message": "Loved your take on microservices",
            "personalized_message": "Hey, noticed your team is growing",
        })
        assert "**Company:** TechCo" in result
        assert "**Title:** VP Engineering" in result
        assert "Loved your take on microservices" in result
        assert "Hey, noticed your team is growing" in result

    def test_none_values_skipped(self):
        """Should skip fields with None values."""
        result = build_lead_context_section({
            "company": None,
            "title": "CTO",
        })
        assert "**Company:**" not in result
        assert "**Title:** CTO" in result


class TestPositiveReplyPrompt:
    """Tests for the positive_reply stage prompt."""

    def test_system_prompt_has_qualifying_questions(self):
        """System prompt should contain qualifying questions."""
        assert "LinkedIn a big client acq channel" in POSITIVE_REPLY_SYSTEM_PROMPT
        assert "ICP" in POSITIVE_REPLY_SYSTEM_PROMPT

    def test_system_prompt_has_examples(self):
        """System prompt should contain few-shot examples."""
        assert "Example 1" in POSITIVE_REPLY_SYSTEM_PROMPT
        assert "Example 2" in POSITIVE_REPLY_SYSTEM_PROMPT
        assert "SOC as a service" in POSITIVE_REPLY_SYSTEM_PROMPT

    def test_system_prompt_text_message_style(self):
        """System prompt should instruct text-message style."""
        assert "Text-message style" in POSITIVE_REPLY_SYSTEM_PROMPT
        assert "2-3 SHORT separate messages" in POSITIVE_REPLY_SYSTEM_PROMPT

    def test_user_prompt_includes_lead_context(self):
        """User prompt should include lead context when provided."""
        result = positive_reply_prompt(
            lead_name="John",
            lead_message="Sounds interesting",
            lead_context={
                "company": "BigCorp",
                "title": "CEO",
                "triggering_message": "Hey John, saw your post",
            },
        )
        assert "**Company:** BigCorp" in result
        assert "**Title:** CEO" in result
        assert "Hey John, saw your post" in result

    def test_user_prompt_works_without_context(self):
        """User prompt should work fine without lead_context."""
        result = positive_reply_prompt(
            lead_name="Jane",
            lead_message="Tell me more",
        )
        assert "**Name:** Jane" in result
        assert "Tell me more" in result

    def test_user_prompt_includes_history_with_roles(self):
        """User prompt should show correct roles in history."""
        history = [
            {"role": "you", "content": "Hey, thanks for connecting!"},
            {"role": "lead", "content": "Sure thing, what do you do?"},
        ]
        result = positive_reply_prompt(
            lead_name="Bob",
            lead_message="Tell me more",
            conversation_history=history,
        )
        assert "**You:**" in result
        assert "**Lead:**" in result
        assert "Hey, thanks for connecting!" in result


class TestAllStagePromptsAcceptLeadContext:
    """All stage modules should accept and render lead_context."""

    @pytest.mark.parametrize("build_fn", [
        positive_reply_prompt,
        pitched_prompt,
        calendar_sent_prompt,
        booked_prompt,
        regeneration_prompt,
    ])
    def test_stage_prompt_renders_lead_context(self, build_fn):
        """Each stage prompt should include lead context in output."""
        result = build_fn(
            lead_name="Test Lead",
            lead_message="Hello",
            conversation_history=[
                {"role": "you", "content": "Hi there"},
                {"role": "lead", "content": "Hello"},
            ],
            lead_context={
                "company": "TestCorp",
                "title": "Founder",
                "triggering_message": "Saw your post",
            },
        )
        assert "**Company:** TestCorp" in result
        assert "**Title:** Founder" in result
        assert "Saw your post" in result

    @pytest.mark.parametrize("build_fn", [
        positive_reply_prompt,
        pitched_prompt,
        calendar_sent_prompt,
        booked_prompt,
        regeneration_prompt,
    ])
    def test_stage_prompt_works_without_lead_context(self, build_fn):
        """Each stage prompt should work without lead_context."""
        result = build_fn(
            lead_name="Test Lead",
            lead_message="Hello",
        )
        assert "**Name:** Test Lead" in result
        assert "Hello" in result


class TestStageDetectorPrompt:
    """Tests for the stage detection prompt."""

    def test_includes_lead_context(self):
        """Stage detection prompt should include lead context."""
        result = build_stage_detection_prompt(
            lead_name="John",
            lead_message="Sounds good",
            lead_context={"company": "ACME", "title": "CTO"},
        )
        assert "**Company:** ACME" in result
        assert "**Title:** CTO" in result

    def test_correct_role_attribution_in_history(self):
        """Stage detection should show correct roles in history."""
        history = [
            {"role": "you", "content": "Want to hop on a call?"},
            {"role": "lead", "content": "Sure, when?"},
        ]
        result = build_stage_detection_prompt(
            lead_name="Jane",
            lead_message="Sure, when?",
            conversation_history=history,
        )
        assert "**You:**" in result
        assert "Want to hop on a call?" in result
        assert "**Lead:**" in result

    def test_works_without_context(self):
        """Should work with no lead_context."""
        result = build_stage_detection_prompt(
            lead_name="Bob",
            lead_message="Hello",
        )
        assert "**Name:** Bob" in result
