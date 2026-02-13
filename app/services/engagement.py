"""Engagement orchestration service.

Monitors watched LinkedIn profiles for new posts, generates summaries
and draft comments, and sends them to Slack for approval.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import EngagementPost, WatchedProfile
from app.services.apify import ApifyError, ApifyService, get_apify_service
from app.services.deepseek import DeepSeekError, get_deepseek_client
from app.services.slack import SlackError, get_slack_bot

logger = logging.getLogger(__name__)


async def check_engagement_posts() -> dict:
    """Main orchestration: check all active profiles for new posts.

    Flow:
    1. Fetch active WatchedProfiles from DB
    2. Scrape recent posts via Apify LinkedIn profile posts scraper
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

            # 2. Scrape posts for all profiles in one Apify call
            profile_urls = [p.linkedin_url for p in profiles]
            profile_map = {p.linkedin_url: p for p in profiles}

            apify = get_apify_service()
            try:
                all_posts = await asyncio.to_thread(
                    apify.scrape_profile_posts,
                    linkedin_urls=profile_urls,
                    max_posts=5,
                )
            except ApifyError as e:
                logger.error(f"Apify scrape failed: {e}", exc_info=True)
                errors.append(f"Apify: {e}")
                return {
                    "profiles_checked": 0,
                    "posts_found": 0,
                    "posts_new": 0,
                    "posts_notified": 0,
                    "errors": errors[:10],
                }

            posts_found = len(all_posts)
            logger.info(f"Apify returned {posts_found} total posts")

            # Log first item's structure for debugging
            if all_posts:
                first = all_posts[0]
                logger.info(f"Sample post keys: {list(first.keys())}")
                logger.info(f"Sample authorName type: {type(first.get('authorName'))}")
                logger.info(f"Sample authorName value: {first.get('authorName')}")
                logger.info(f"Sample postUrl: {first.get('postUrl')}")
                logger.info(f"Sample authorProfileUrl: {first.get('authorProfileUrl')}")

            # 3-7. Process each post
            for item in all_posts:
                try:
                    notified = await _process_post(session, item, profile_map, apify)
                    if notified:
                        posts_notified += 1
                    posts_new += 1
                except _AlreadySeen:
                    continue
                except Exception as e:
                    error_msg = f"Error processing post: {e}"
                    logger.error(error_msg, exc_info=True)
                    errors.append(error_msg)

            # Update last_checked_at for all profiles
            for profile in profiles:
                profile.last_checked_at = datetime.now(timezone.utc)
                profiles_checked += 1

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


class _AlreadySeen(Exception):
    pass


async def _process_post(
    session: AsyncSession,
    item: dict,
    profile_map: dict[str, WatchedProfile],
    apify: ApifyService,
) -> bool:
    """Process a single post from Apify results.

    Returns True if a Slack notification was sent.
    Raises _AlreadySeen if the post URL was already in the DB.
    """
    post_url = apify.extract_post_url(item)
    if not post_url:
        return False

    # 3. Skip already-seen posts
    existing = await session.execute(
        select(EngagementPost).where(EngagementPost.post_url == post_url)
    )
    if existing.scalar_one_or_none():
        raise _AlreadySeen()

    # Match post to a watched profile
    author_url = item.get("authorProfileUrl") or item.get("profileUrl") or ""
    if "?" in author_url:
        author_url = author_url.split("?")[0]
    author_url = author_url.rstrip("/").lower()

    profile = None
    for url, p in profile_map.items():
        if url.rstrip("/").lower() == author_url:
            profile = p
            break

    # Fallback: try author name matching
    if not profile:
        raw_name = item.get("authorName") or item.get("author") or ""
        # Handle dict-shaped author names (e.g. {"first": "...", "last": "..."})
        if isinstance(raw_name, dict):
            parts = [raw_name.get("first", ""), raw_name.get("last", "")]
            author_name = " ".join(p for p in parts if p).strip()
        else:
            author_name = str(raw_name)
        if author_name:
            for p in profile_map.values():
                if p.name.lower() in author_name.lower() or author_name.lower() in p.name.lower():
                    profile = p
                    break

    if not profile:
        # Post doesn't match any watched profile - use first profile as fallback
        profile = list(profile_map.values())[0]
        logger.warning(f"Could not match post to profile, using fallback: {profile.name}")

    snippet = apify.extract_post_text(item)

    # 4. Call DeepSeek for summary + draft comment
    summary = ""
    draft_comment = ""
    try:
        deepseek = get_deepseek_client()
        summary, draft_comment = await deepseek.summarize_and_draft_comment(
            author_name=profile.name,
            author_headline=profile.headline,
            author_category=profile.category.value,
            post_snippet=snippet[:2000],  # Truncate very long posts
        )
    except DeepSeekError as e:
        logger.error(f"DeepSeek error for {post_url}: {e}")

    # 5. Send Slack notification
    post_id = uuid.uuid4()
    slack_ts = None
    if summary and draft_comment:
        try:
            slack_bot = get_slack_bot()
            slack_ts = await slack_bot.send_engagement_notification(
                post_id=post_id,
                author_name=profile.name,
                author_headline=profile.headline,
                author_category=profile.category,
                post_url=post_url,
                post_summary=summary,
                draft_comment=draft_comment,
            )
        except SlackError as e:
            logger.error(f"Slack error for {post_url}: {e}")

    # 6. Store EngagementPost record
    engagement_post = EngagementPost(
        id=post_id,
        watched_profile_id=profile.id,
        post_url=post_url,
        post_snippet=snippet[:2000] if snippet else None,
        post_summary=summary,
        draft_comment=draft_comment,
        slack_message_ts=slack_ts,
    )
    session.add(engagement_post)

    return slack_ts is not None
