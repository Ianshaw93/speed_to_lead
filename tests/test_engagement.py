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
from app.services.engagement import check_engagement_posts


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
    async def test_only_fetches_active_profiles(self, test_db_session):
        """Should only fetch active profiles for scraping."""
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

        mock_apify = MagicMock()
        mock_apify.scrape_profile_posts.return_value = []

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        # Should only send the active profile URL to Apify
        call_args = mock_apify.scrape_profile_posts.call_args
        urls = call_args[1]["linkedin_urls"]
        assert "https://linkedin.com/in/active" in urls
        assert "https://linkedin.com/in/inactive" not in urls
        assert result["profiles_checked"] == 1

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

        existing_post = EngagementPost(
            watched_profile_id=profile.id,
            post_url="https://linkedin.com/posts/john_existing-123",
            post_snippet="Old post",
        )
        test_db_session.add(existing_post)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.scrape_profile_posts.return_value = [
            {"postUrl": "https://linkedin.com/posts/john_existing-123", "text": "Old"},
        ]
        mock_apify.extract_post_url.return_value = "https://linkedin.com/posts/john_existing-123"
        mock_apify.extract_post_text.return_value = "Old"

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        assert result["posts_new"] == 0
        assert result["posts_notified"] == 0

    @pytest.mark.asyncio
    async def test_processes_new_post_end_to_end(self, test_db_session):
        """Should create EngagementPost and send Slack for new posts."""
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
        mock_apify.scrape_profile_posts.return_value = [
            {
                "postUrl": "https://linkedin.com/posts/jane_new-456",
                "text": "Exciting news about AI...",
                "authorProfileUrl": "https://linkedin.com/in/jane",
            },
        ]
        mock_apify.extract_post_url.return_value = "https://linkedin.com/posts/jane_new-456"
        mock_apify.extract_post_text.return_value = "Exciting news about AI..."

        mock_deepseek = AsyncMock()
        mock_deepseek.summarize_and_draft_comment.return_value = (
            "Jane shared thoughts on AI.",
            "Really interesting perspective...",
        )

        mock_slack = AsyncMock()
        mock_slack.send_engagement_notification.return_value = "ts-123"

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("app.services.engagement.get_deepseek_client", return_value=mock_deepseek), \
             patch("app.services.engagement.get_slack_bot", return_value=mock_slack), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        assert result["posts_new"] == 1
        assert result["posts_notified"] == 1
        mock_deepseek.summarize_and_draft_comment.assert_called_once()
        mock_slack.send_engagement_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_apify_error_gracefully(self, test_db_session):
        """Should return error summary when Apify fails."""
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
        mock_apify.scrape_profile_posts.side_effect = ApifyError("API down")

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        assert result["profiles_checked"] == 0
        assert len(result["errors"]) > 0

    @pytest.mark.asyncio
    async def test_updates_last_checked_at(self, test_db_session):
        """Should update last_checked_at for all checked profiles."""
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
        mock_apify.scrape_profile_posts.return_value = []

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        assert profile.last_checked_at is not None
