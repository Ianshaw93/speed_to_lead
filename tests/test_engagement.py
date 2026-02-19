"""Tests for engagement orchestration service."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import (
    DailyMetrics,
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
        mock_apify.scrape_profile_posts.return_value = ([], 0.0)

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
        mock_apify.scrape_profile_posts.return_value = (
            [{"postUrl": "https://linkedin.com/posts/john_existing-123", "text": "Old"}],
            0.005,
        )
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
        mock_apify.scrape_profile_posts.return_value = (
            [
                {
                    "postUrl": "https://linkedin.com/posts/jane_new-456",
                    "text": "Exciting news about AI...",
                    "authorProfileUrl": "https://linkedin.com/in/jane",
                },
            ],
            0.012,
        )
        mock_apify.extract_post_url.return_value = "https://linkedin.com/posts/jane_new-456"
        mock_apify.extract_post_text.return_value = "Exciting news about AI..."

        mock_deepseek = AsyncMock()
        mock_deepseek.summarize_and_draft_comment.return_value = (
            "Jane shared thoughts on AI.",
            "Really interesting perspective...",
            150,  # prompt_tokens
            80,   # completion_tokens
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
        mock_apify.scrape_profile_posts.return_value = ([], 0.0)

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        assert profile.last_checked_at is not None


class TestEngagementCostTracking:
    """Tests for engagement cost logging and accumulation."""

    @pytest.mark.asyncio
    async def test_returns_cost_fields_in_summary(self, test_db_session):
        """Should include apify_cost_usd and deepseek_cost_usd in result."""
        with patch("app.services.engagement.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        assert "apify_cost_usd" in result
        assert "deepseek_cost_usd" in result

    @pytest.mark.asyncio
    async def test_tracks_apify_cost(self, test_db_session):
        """Should capture Apify cost from scrape_profile_posts."""
        profile = WatchedProfile(
            linkedin_url="https://linkedin.com/in/cost-test",
            name="Cost Test",
            category=WatchedProfileCategory.PROSPECT,
            is_active=True,
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.scrape_profile_posts.return_value = ([], 0.0234)

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        assert result["apify_cost_usd"] == pytest.approx(0.0234, abs=1e-6)

    @pytest.mark.asyncio
    async def test_tracks_deepseek_cost(self, test_db_session):
        """Should calculate and return DeepSeek cost from token usage."""
        profile = WatchedProfile(
            linkedin_url="https://linkedin.com/in/ds-cost",
            name="DS Cost",
            headline="Test",
            category=WatchedProfileCategory.PROSPECT,
            is_active=True,
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.scrape_profile_posts.return_value = (
            [
                {
                    "postUrl": "https://linkedin.com/posts/ds-cost_post-1",
                    "text": "Test post",
                },
            ],
            0.01,
        )
        mock_apify.extract_post_url.return_value = "https://linkedin.com/posts/ds-cost_post-1"
        mock_apify.extract_post_text.return_value = "Test post"

        mock_deepseek = AsyncMock()
        # 1000 input tokens * $0.27/M = $0.00027
        # 500 output tokens * $1.10/M = $0.00055
        # Total = $0.00082
        mock_deepseek.summarize_and_draft_comment.return_value = (
            "Summary", "Comment", 1000, 500
        )

        mock_slack = AsyncMock()
        mock_slack.send_engagement_notification.return_value = "ts-456"

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("app.services.engagement.get_deepseek_client", return_value=mock_deepseek), \
             patch("app.services.engagement.get_slack_bot", return_value=mock_slack), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await check_engagement_posts()

        expected_cost = 1000 * 0.00000027 + 500 * 0.0000011
        assert result["deepseek_cost_usd"] == pytest.approx(expected_cost, abs=1e-8)

    @pytest.mark.asyncio
    async def test_persists_costs_to_daily_metrics(self, test_db_session):
        """Should upsert engagement costs into DailyMetrics."""
        profile = WatchedProfile(
            linkedin_url="https://linkedin.com/in/metrics-test",
            name="Metrics Test",
            headline="CEO",
            category=WatchedProfileCategory.PROSPECT,
            is_active=True,
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.scrape_profile_posts.return_value = (
            [
                {
                    "postUrl": "https://linkedin.com/posts/metrics_post-1",
                    "text": "Post content",
                },
            ],
            0.015,
        )
        mock_apify.extract_post_url.return_value = "https://linkedin.com/posts/metrics_post-1"
        mock_apify.extract_post_text.return_value = "Post content"

        mock_deepseek = AsyncMock()
        mock_deepseek.summarize_and_draft_comment.return_value = (
            "Summary", "Comment", 500, 200
        )

        mock_slack = AsyncMock()
        mock_slack.send_engagement_notification.return_value = "ts-789"

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("app.services.engagement.get_deepseek_client", return_value=mock_deepseek), \
             patch("app.services.engagement.get_slack_bot", return_value=mock_slack), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            await check_engagement_posts()

        # Verify DailyMetrics row was created
        result = await test_db_session.execute(
            select(DailyMetrics).where(DailyMetrics.date == date.today())
        )
        metrics = result.scalar_one_or_none()

        assert metrics is not None
        assert metrics.engagement_apify_cost == Decimal("0.015")
        assert metrics.engagement_deepseek_cost > 0
        assert metrics.engagement_checks == 1
        assert metrics.engagement_posts_found == 1

    @pytest.mark.asyncio
    async def test_increments_existing_daily_metrics(self, test_db_session):
        """Should increment existing DailyMetrics rather than overwrite."""
        # Pre-create a DailyMetrics row
        existing_metrics = DailyMetrics(
            date=date.today(),
            engagement_apify_cost=Decimal("0.010"),
            engagement_deepseek_cost=Decimal("0.001"),
            engagement_checks=2,
            engagement_posts_found=3,
        )
        test_db_session.add(existing_metrics)
        await test_db_session.commit()

        profile = WatchedProfile(
            linkedin_url="https://linkedin.com/in/incr-test",
            name="Incr Test",
            headline="Test",
            category=WatchedProfileCategory.PROSPECT,
            is_active=True,
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        mock_apify = MagicMock()
        mock_apify.scrape_profile_posts.return_value = (
            [
                {
                    "postUrl": "https://linkedin.com/posts/incr_post-1",
                    "text": "New post",
                },
            ],
            0.020,
        )
        mock_apify.extract_post_url.return_value = "https://linkedin.com/posts/incr_post-1"
        mock_apify.extract_post_text.return_value = "New post"

        mock_deepseek = AsyncMock()
        mock_deepseek.summarize_and_draft_comment.return_value = (
            "Summary", "Comment", 200, 100
        )

        mock_slack = AsyncMock()
        mock_slack.send_engagement_notification.return_value = "ts-incr"

        with patch("app.services.engagement.async_session_factory") as mock_factory, \
             patch("app.services.engagement.get_apify_service", return_value=mock_apify), \
             patch("app.services.engagement.get_deepseek_client", return_value=mock_deepseek), \
             patch("app.services.engagement.get_slack_bot", return_value=mock_slack), \
             patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=test_db_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            await check_engagement_posts()

        # Verify incremented values
        result = await test_db_session.execute(
            select(DailyMetrics).where(DailyMetrics.date == date.today())
        )
        metrics = result.scalar_one()

        assert metrics.engagement_apify_cost == Decimal("0.010") + Decimal("0.020")
        assert metrics.engagement_checks == 3  # 2 + 1
        assert metrics.engagement_posts_found == 4  # 3 + 1
