"""Engagement orchestration service.

Monitors watched LinkedIn profiles for new posts, generates summaries
and draft comments, and sends them to Slack for approval.
"""

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import DailyMetrics, EngagementPost, WatchedProfile
from app.services.apify import ApifyError, ApifyService, get_apify_service
from app.services.deepseek import DeepSeekError, get_deepseek_client
from app.services.slack import SlackError, get_slack_bot

# DeepSeek pricing (deepseek-chat): $0.27/M input, $1.10/M output
DEEPSEEK_INPUT_COST_PER_TOKEN = Decimal("0.00000027")
DEEPSEEK_OUTPUT_COST_PER_TOKEN = Decimal("0.0000011")

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
    apify_cost_usd = Decimal("0")
    deepseek_cost_usd = Decimal("0")

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
                    "apify_cost_usd": 0,
                    "deepseek_cost_usd": 0,
                }

            logger.info(f"Checking {len(profiles)} active profiles")

            # 2. Scrape posts for all profiles in one Apify call
            profile_urls = [p.linkedin_url for p in profiles]
            profile_map = {p.linkedin_url: p for p in profiles}

            apify = get_apify_service()
            try:
                all_posts, apify_run_cost = await asyncio.to_thread(
                    apify.scrape_profile_posts,
                    linkedin_urls=profile_urls,
                    max_posts=1,
                )
                apify_cost_usd = Decimal(str(apify_run_cost))
            except ApifyError as e:
                logger.error(f"Apify scrape failed: {e}", exc_info=True)
                errors.append(f"Apify: {e}")
                return {
                    "profiles_checked": 0,
                    "posts_found": 0,
                    "posts_new": 0,
                    "posts_notified": 0,
                    "errors": errors[:10],
                    "apify_cost_usd": 0,
                    "deepseek_cost_usd": 0,
                }

            posts_found = len(all_posts)
            logger.info(f"Apify returned {posts_found} total posts")

            # Keep only the newest post per profile (first result per input URL)
            seen_profiles: set[str] = set()
            filtered_posts = []
            for item in all_posts:
                input_url = item.get("input", "")
                if isinstance(input_url, dict):
                    input_url = input_url.get("url", "")
                input_key = input_url.rstrip("/").lower()
                if input_key and input_key in seen_profiles:
                    continue
                if input_key:
                    seen_profiles.add(input_key)
                filtered_posts.append(item)

            logger.info(f"Filtered to {len(filtered_posts)} posts (1 per profile)")

            # 3-7. Process each post
            for item in filtered_posts:
                try:
                    notified, post_deepseek_cost = await _process_post(
                        session, item, profile_map, apify
                    )
                    deepseek_cost_usd += post_deepseek_cost
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

            # Persist costs to DailyMetrics
            await _update_daily_metrics(
                session,
                apify_cost=apify_cost_usd,
                deepseek_cost=deepseek_cost_usd,
                posts_found=posts_new,
            )

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
        "apify_cost_usd": float(apify_cost_usd),
        "deepseek_cost_usd": float(deepseek_cost_usd),
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
) -> tuple[bool, Decimal]:
    """Process a single post from Apify results.

    Returns (notified, deepseek_cost_usd).
    Raises _AlreadySeen if the post URL was already in the DB.
    """
    post_url = apify.extract_post_url(item)
    if not post_url:
        return False, Decimal("0")

    # 3. Skip already-seen posts
    existing = await session.execute(
        select(EngagementPost).where(EngagementPost.post_url == post_url)
    )
    if existing.scalar_one_or_none():
        raise _AlreadySeen()

    # Match post to a watched profile via input URL (the URL we asked Apify to scrape)
    input_url = ""
    raw_input = item.get("input")
    if isinstance(raw_input, str):
        input_url = raw_input.rstrip("/").lower()
    elif isinstance(raw_input, dict):
        input_url = (raw_input.get("url") or raw_input.get("profileUrl") or "").rstrip("/").lower()

    profile = None
    if input_url:
        for url, p in profile_map.items():
            if url.rstrip("/").lower() == input_url:
                profile = p
                break

    # Fallback: try author profile URL from the author dict
    if not profile:
        author = item.get("author") or {}
        author_url = ""
        if isinstance(author, dict):
            author_url = (author.get("profileUrl") or author.get("url") or "").rstrip("/").lower()
        elif isinstance(author, str):
            author_url = author.rstrip("/").lower()
        if author_url:
            for url, p in profile_map.items():
                if url.rstrip("/").lower() in author_url or author_url in url.rstrip("/").lower():
                    profile = p
                    break

    # Fallback: try author name matching
    if not profile:
        author = item.get("author") or {}
        if isinstance(author, dict):
            author_name = author.get("name") or author.get("fullName") or ""
            if not author_name:
                parts = [author.get("first", ""), author.get("last", "")]
                author_name = " ".join(p for p in parts if p).strip()
        elif isinstance(author, str):
            author_name = author
        else:
            author_name = ""
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
    post_deepseek_cost = Decimal("0")
    try:
        deepseek = get_deepseek_client()
        summary, draft_comment, prompt_tokens, completion_tokens = (
            await deepseek.summarize_and_draft_comment(
                author_name=profile.name,
                author_headline=profile.headline,
                author_category=profile.category.value,
                post_snippet=snippet[:2000],  # Truncate very long posts
            )
        )
        post_deepseek_cost = (
            Decimal(prompt_tokens) * DEEPSEEK_INPUT_COST_PER_TOKEN
            + Decimal(completion_tokens) * DEEPSEEK_OUTPUT_COST_PER_TOKEN
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

    return slack_ts is not None, post_deepseek_cost


async def _update_daily_metrics(
    session: AsyncSession,
    apify_cost: Decimal,
    deepseek_cost: Decimal,
    posts_found: int,
) -> None:
    """Upsert engagement costs into today's DailyMetrics row."""
    today = date.today()
    result = await session.execute(
        select(DailyMetrics).where(DailyMetrics.date == today)
    )
    metrics = result.scalar_one_or_none()

    if metrics is None:
        metrics = DailyMetrics(
            date=today,
            engagement_apify_cost=Decimal("0"),
            engagement_deepseek_cost=Decimal("0"),
            engagement_checks=0,
            engagement_posts_found=0,
        )
        session.add(metrics)

    metrics.engagement_apify_cost = (metrics.engagement_apify_cost or Decimal("0")) + apify_cost
    metrics.engagement_deepseek_cost = (metrics.engagement_deepseek_cost or Decimal("0")) + deepseek_cost
    metrics.engagement_checks = (metrics.engagement_checks or 0) + 1
    metrics.engagement_posts_found = (metrics.engagement_posts_found or 0) + posts_found
