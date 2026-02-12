"""Engagement orchestration service.

Monitors watched LinkedIn profiles for new posts, generates summaries
and draft comments, and sends them to Slack for approval.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import EngagementPost, WatchedProfile
from app.services.apify import ApifyError, get_apify_service
from app.services.deepseek import DeepSeekError, get_deepseek_client
from app.services.slack import SlackError, get_slack_bot

logger = logging.getLogger(__name__)


async def check_engagement_posts() -> dict:
    """Main orchestration: check all active profiles for new posts.

    Flow:
    1. Fetch active WatchedProfiles from DB
    2. For each, search recent posts via Apify (in thread)
    3. Skip already-seen posts (unique constraint on post_url)
    4. For new posts: call DeepSeek for summary + draft comment
    5. Send Slack notification to engagement channel
    6. Store EngagementPost record with slack_message_ts
    7. Update profile's last_checked_at

    Returns:
        Summary dict with counts.
    """
    logger.info("Starting engagement post check...")

    profiles_checked = 0
    posts_found = 0
    posts_new = 0
    posts_notified = 0
    errors = []

    try:
        async with async_session_factory() as session:
            # 1. Fetch active profiles
            result = await session.execute(
                select(WatchedProfile).where(WatchedProfile.is_active == True)
            )
            profiles = result.scalars().all()

            if not profiles:
                logger.info("No active watched profiles found")
                return {
                    "profiles_checked": 0,
                    "posts_found": 0,
                    "posts_new": 0,
                    "posts_notified": 0,
                    "errors": [],
                }

            logger.info(f"Checking {len(profiles)} active profiles")

            for profile in profiles:
                try:
                    new_count = await _check_profile(session, profile)
                    profiles_checked += 1
                    posts_notified += new_count
                except Exception as e:
                    error_msg = f"Error checking {profile.name}: {e}"
                    logger.error(error_msg, exc_info=True)
                    errors.append(error_msg)

            await session.commit()

    except Exception as e:
        logger.error(f"Fatal error in engagement check: {e}", exc_info=True)
        errors.append(f"Fatal: {e}")

    summary = {
        "profiles_checked": profiles_checked,
        "posts_found": posts_found,
        "posts_new": posts_new,
        "posts_notified": posts_notified,
        "errors": errors[:10],
    }
    logger.info(f"Engagement check complete: {summary}")
    return summary


async def _check_profile(session: AsyncSession, profile: WatchedProfile) -> int:
    """Check a single profile for new posts and send notifications.

    Args:
        session: Database session.
        profile: The watched profile to check.

    Returns:
        Number of new posts notified.
    """
    logger.info(f"Checking profile: {profile.name} ({profile.category.value})")

    # 2. Search for posts via Apify (sync call wrapped in thread)
    apify = get_apify_service()
    try:
        search_results = await asyncio.to_thread(
            apify.search_linkedin_posts,
            author_name=profile.name,
            days_back=3,
            max_results=5,
        )
    except ApifyError as e:
        logger.error(f"Apify search failed for {profile.name}: {e}")
        return 0

    if not search_results:
        logger.info(f"No posts found for {profile.name}")
        # Update last_checked_at even if no posts
        profile.last_checked_at = datetime.now(timezone.utc)
        return 0

    logger.info(f"Found {len(search_results)} posts for {profile.name}")

    notified = 0

    for post_data in search_results:
        post_url = post_data["url"]

        # 3. Skip already-seen posts
        existing = await session.execute(
            select(EngagementPost).where(EngagementPost.post_url == post_url)
        )
        if existing.scalar_one_or_none():
            logger.debug(f"Already seen post: {post_url}")
            continue

        # Build post snippet from search result
        snippet = post_data.get("description", "") or post_data.get("title", "")

        # 4. Call DeepSeek for summary + draft comment
        try:
            deepseek = get_deepseek_client()
            summary, draft_comment = await deepseek.summarize_and_draft_comment(
                author_name=profile.name,
                author_headline=profile.headline,
                author_category=profile.category.value,
                post_snippet=snippet,
            )
        except DeepSeekError as e:
            logger.error(f"DeepSeek error for {post_url}: {e}")
            # Store the post anyway with empty summary/comment
            summary = ""
            draft_comment = ""

        # 5. Send Slack notification
        slack_ts = None
        if summary and draft_comment:
            try:
                slack_bot = get_slack_bot()
                # Pre-generate the post ID for the Slack buttons
                import uuid
                post_id = uuid.uuid4()

                slack_ts = await slack_bot.send_engagement_notification(
                    post_id=post_id,
                    author_name=profile.name,
                    author_headline=profile.headline,
                    author_category=profile.category,
                    post_url=post_url,
                    post_summary=summary,
                    draft_comment=draft_comment,
                )
                notified += 1
            except SlackError as e:
                logger.error(f"Slack error for {post_url}: {e}")
                post_id = uuid.uuid4()
        else:
            import uuid
            post_id = uuid.uuid4()

        # 6. Store EngagementPost record
        engagement_post = EngagementPost(
            id=post_id,
            watched_profile_id=profile.id,
            post_url=post_url,
            post_snippet=snippet,
            post_summary=summary,
            draft_comment=draft_comment,
            slack_message_ts=slack_ts,
        )
        session.add(engagement_post)

    # 7. Update last_checked_at
    profile.last_checked_at = datetime.now(timezone.utc)

    return notified
