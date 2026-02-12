"""Tests for Slack engagement message building."""

import uuid

import pytest

from app.models import WatchedProfileCategory
from app.services.slack import (
    build_engagement_buttons,
    build_engagement_message,
)


class TestBuildEngagementMessage:
    """Tests for build_engagement_message."""

    def test_contains_header(self):
        """Should include header block."""
        blocks = build_engagement_message(
            author_name="John Smith",
            author_headline="CEO at TechCorp",
            author_category=WatchedProfileCategory.PROSPECT,
            post_url="https://linkedin.com/posts/john_test-123",
            post_summary="John discusses AI trends.",
            draft_comment="Great insight on AI.",
        )

        header = blocks[0]
        assert header["type"] == "header"
        assert "Engagement" in header["text"]["text"]

    def test_contains_category_context(self):
        """Should show the profile category."""
        blocks = build_engagement_message(
            author_name="John Smith",
            author_headline=None,
            author_category=WatchedProfileCategory.INFLUENCER,
            post_url="https://linkedin.com/posts/john_test-123",
            post_summary="Summary.",
            draft_comment="Comment.",
        )

        context = blocks[1]
        assert context["type"] == "context"
        assert "Influencer" in context["elements"][0]["text"]

    def test_contains_open_post_button(self):
        """Should include a link button to the post."""
        post_url = "https://linkedin.com/posts/john_test-123"
        blocks = build_engagement_message(
            author_name="John Smith",
            author_headline="CEO",
            author_category=WatchedProfileCategory.PROSPECT,
            post_url=post_url,
            post_summary="Summary.",
            draft_comment="Comment.",
        )

        # Find the section with accessory button
        section_with_button = blocks[2]
        assert section_with_button["accessory"]["url"] == post_url

    def test_contains_draft_comment_in_code_block(self):
        """Should show draft comment in code block for easy copy."""
        draft = "This is a really thoughtful perspective."
        blocks = build_engagement_message(
            author_name="John Smith",
            author_headline=None,
            author_category=WatchedProfileCategory.COMPETITOR,
            post_url="https://linkedin.com/posts/john_test-123",
            post_summary="Summary.",
            draft_comment=draft,
        )

        # Find the draft comment block
        comment_block = blocks[5]
        assert f"```{draft}```" in comment_block["text"]["text"]

    def test_contains_summary(self):
        """Should include the post summary."""
        summary = "John shared his views on AI regulation."
        blocks = build_engagement_message(
            author_name="John Smith",
            author_headline=None,
            author_category=WatchedProfileCategory.ICP_PEER,
            post_url="https://linkedin.com/posts/john_test-123",
            post_summary=summary,
            draft_comment="Comment.",
        )

        # Find the summary block
        summary_block = blocks[3]
        assert summary in summary_block["text"]["text"]

    def test_includes_headline_when_provided(self):
        """Should show headline in author info."""
        blocks = build_engagement_message(
            author_name="Jane Doe",
            author_headline="VP Engineering at Scale",
            author_category=WatchedProfileCategory.INFLUENCER,
            post_url="https://linkedin.com/posts/jane_test-456",
            post_summary="Summary.",
            draft_comment="Comment.",
        )

        author_section = blocks[2]
        assert "VP Engineering at Scale" in author_section["text"]["text"]


class TestBuildEngagementButtons:
    """Tests for build_engagement_buttons."""

    def test_has_three_buttons(self):
        """Should have Done, Edit, and Skip buttons."""
        post_id = uuid.uuid4()
        blocks = build_engagement_buttons(post_id)

        assert len(blocks) == 1
        actions = blocks[0]
        assert actions["type"] == "actions"
        assert len(actions["elements"]) == 3

    def test_button_action_ids(self):
        """Should have correct action IDs."""
        post_id = uuid.uuid4()
        blocks = build_engagement_buttons(post_id)

        elements = blocks[0]["elements"]
        action_ids = [e["action_id"] for e in elements]

        assert "engagement_done" in action_ids
        assert "engagement_edit" in action_ids
        assert "engagement_skip" in action_ids

    def test_button_values_contain_post_id(self):
        """Should include post ID in button values."""
        post_id = uuid.uuid4()
        blocks = build_engagement_buttons(post_id)

        for element in blocks[0]["elements"]:
            assert element["value"] == str(post_id)

    def test_done_is_primary_skip_is_danger(self):
        """Done should be primary style, Skip should be danger."""
        post_id = uuid.uuid4()
        blocks = build_engagement_buttons(post_id)

        elements = blocks[0]["elements"]
        done_btn = next(e for e in elements if e["action_id"] == "engagement_done")
        skip_btn = next(e for e in elements if e["action_id"] == "engagement_skip")

        assert done_btn["style"] == "primary"
        assert skip_btn["style"] == "danger"

    def test_posts_to_engagement_channel(self):
        """SlackBot should use engagement channel ID."""
        from app.services.slack import SlackBot

        bot = SlackBot(
            bot_token="xoxb-test",
            channel_id="C_MAIN",
            engagement_channel_id="C_ENGAGEMENT",
        )

        assert bot._engagement_channel_id == "C_ENGAGEMENT"
