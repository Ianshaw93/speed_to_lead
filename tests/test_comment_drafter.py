"""Tests for comment drafter prompt."""

import pytest

from app.prompts.comment_drafter import SYSTEM_PROMPT, build_comment_drafter_prompt


class TestCommentDrafterPrompt:
    """Tests for comment drafter prompt building."""

    def test_builds_prompt_with_all_fields(self):
        """Should include all fields in the prompt."""
        result = build_comment_drafter_prompt(
            author_name="Jane Doe",
            author_headline="CEO at TechCorp",
            author_category="influencer",
            post_snippet="AI is transforming how we do business...",
        )

        assert "Jane Doe" in result
        assert "CEO at TechCorp" in result
        assert "influencer" in result
        assert "AI is transforming" in result

    def test_handles_missing_headline(self):
        """Should use fallback when headline is None."""
        result = build_comment_drafter_prompt(
            author_name="Jane Doe",
            author_headline=None,
            author_category="prospect",
            post_snippet="Some post content",
        )

        assert "Not available" in result
        assert "Jane Doe" in result

    def test_handles_empty_snippet(self):
        """Should use fallback when snippet is empty."""
        result = build_comment_drafter_prompt(
            author_name="Jane Doe",
            author_headline="CTO",
            author_category="competitor",
            post_snippet="",
        )

        assert "No content available" in result

    def test_system_prompt_contains_guidelines(self):
        """System prompt should contain key engagement guidelines."""
        assert "JSON" in SYSTEM_PROMPT
        assert "summary" in SYSTEM_PROMPT
        assert "comment" in SYSTEM_PROMPT
        assert "Great post" in SYSTEM_PROMPT  # Anti-pattern mentioned
        assert "2-4 sentences" in SYSTEM_PROMPT
