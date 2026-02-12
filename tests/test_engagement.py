"""Tests for engagement orchestration service."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import (
    EngagementPost,
    EngagementPostStatus,
    WatchedProfile,
    WatchedProfileCategory,
)
from app.services.engagement import _check_profile, check_engagement_posts


class TestCheckEngagementPosts:
    """Tests for the main check_engagement_posts orchestration."""

    @pytest.mark.asyncio
    async def test_returns_zero_counts_when_no_profiles(self, test_db_session):
        """Should return zeros when no active profiles exist."""
        with patch("app.services.engagement.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        assert result["profiles_checked"] == 0
        assert result["posts_notified"] == 0

    @pytest.mark.asyncio
    async def test_checks_active_profiles(self, test_db_session):
        """Should only check active profiles."""
        # Add active and inactive profiles
        active = WatchedProfile(
            linkedin_url="https://linkedin.com/in/active",
            name="Active User",
            category=WatchedProfileCategory.PROSPECT,
            is_active=True,
        )
        inactive = WatchedProfile(
            linkedin_url="https://linkedin.com/in/inactive",
            name="Inactive User",
            category=WatchedProfileCategory.PROSPECT,
            is_active=False,
        )
        test_db_session.add_all([active, inactive])
        await test_db_session.commit()

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement._check_profile", new_callable=AsyncMock) as mock_check:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_check.return_value = 0

            result = await check_engagement_posts()

        # Should only check the active profile
        assert result["profiles_checked"] == 1
        assert mock_check.call_count == 1
        checked_profile = mock_check.call_args[0][1]
        assert checked_profile.name == "Active User"


class TestCheckProfile:
    """Tests for _check_profile function."""

    @pytest.mark.asyncio
    async def test_skips_already_seen_posts(self, test_db_session):
        """Should skip posts that already exist in the database."""
        profile = WatchedProfile(
            linkedin_url="https://linkedin.com/in/john",
            name="John Smith",
            category=WatchedProfileCategory.INFLUENCER,
            is_active=True,
        )
        test_db_session.add(profile)
        await test_db_session.flush()

        # Add an existing post
        existing_post = EngagementPost(
            watched_profile_id=profile.id,
            post_url="https://linkedin.com/posts/john_existing-123",
            post_snippet="Old post",
        )
        test_db_session.add(existing_post)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.search_linkedin_posts.return_value = [
            {
                "url": "https://linkedin.com/posts/john_existing-123",
                "title": "Old post",
                "description": "Already seen",
            },
        ]

        with patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            result = await _check_profile(test_db_session, profile)

        # No new notifications
        assert result == 0

    @pytest.mark.asyncio
    async def test_processes_new_post(self, test_db_session):
        """Should create EngagementPost and send Slack notification for new posts."""
        profile = WatchedProfile(
            linkedin_url="https://linkedin.com/in/jane",
            name="Jane Doe",
            headline="CEO at TechCorp",
            category=WatchedProfileCategory.PROSPECT,
            is_active=True,
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.search_linkedin_posts.return_value = [
            {
                "url": "https://linkedin.com/posts/jane_new-post-456",
                "title": "New post",
                "description": "Exciting news about AI...",
            },
        ]

        mock_deepseek = AsyncMock()
        mock_deepseek.summarize_and_draft_comment.return_value = (
            "Jane shared thoughts on AI innovation.",
            "Really interesting perspective on AI. I've seen similar trends...",
        )

        mock_slack = AsyncMock()
        mock_slack.send_engagement_notification.return_value = "1234567890.123456"

        with patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("app.services.engagement.get_deepseek_client", return_value=mock_deepseek), \
             patch("app.services.engagement.get_slack_bot", return_value=mock_slack), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            result = await _check_profile(test_db_session, profile)

        assert result == 1

        # Verify DeepSeek was called
        mock_deepseek.summarize_and_draft_comment.assert_called_once_with(
            author_name="Jane Doe",
            author_headline="CEO at TechCorp",
            author_category="prospect",
            post_snippet="Exciting news about AI...",
        )

        # Verify Slack notification was sent
        mock_slack.send_engagement_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_apify_error_gracefully(self, test_db_session):
        """Should return 0 and not crash on Apify errors."""
        from app.services.apify import ApifyError

        profile = WatchedProfile(
            linkedin_url="https://linkedin.com/in/error",
            name="Error User",
            category=WatchedProfileCategory.PROSPECT,
            is_active=True,
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.search_linkedin_posts.side_effect = ApifyError("API down")

        with patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            result = await _check_profile(test_db_session, profile)

        assert result == 0

    @pytest.mark.asyncio
    async def test_updates_last_checked_at(self, test_db_session):
        """Should update last_checked_at even when no posts found."""
        profile = WatchedProfile(
            linkedin_url="https://linkedin.com/in/check",
            name="Check User",
            category=WatchedProfileCategory.ICP_PEER,
            is_active=True,
            last_checked_at=None,
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.search_linkedin_posts.return_value = []

        with patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            await _check_profile(test_db_session, profile)

        assert profile.last_checked_at is not None
