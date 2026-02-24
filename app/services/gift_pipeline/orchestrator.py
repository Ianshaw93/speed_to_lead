"""Async 12-step gift leads pipeline orchestrator.

Ported from multichannel-outreach/execution/gift_leads_list.py,
adapted for FastAPI async and DB-backed profile caching.
"""

import logging
import time
import uuid
from typing import Any, Callable, Coroutine

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session_factory
from app.models import Prospect, ProspectSource
from app.services.gift_pipeline.apify_actors import (
    scrape_linkedin_profiles,
    scrape_post_engagers,
    search_google,
)
from app.services.gift_pipeline.constants import (
    DEFAULT_COUNTRIES,
    DEFAULT_DAYS_BACK,
    DEFAULT_MAX_LEADS,
    DEFAULT_MIN_LEADS,
    DEFAULT_MIN_REACTIONS,
    PROFILE_BATCH_SIZE,
)
from app.services.gift_pipeline.cost_tracker import CostTracker
from app.services.gift_pipeline.deepseek_calls import (
    generate_search_queries,
    generate_signal_notes,
    qualify_leads_with_deepseek,
    research_prospect_business,
)
from app.services.gift_pipeline.filters import (
    aggregate_profile_urls,
    build_engagement_context,
    compute_activity_score,
    deduplicate_profile_urls,
    enrich_profiles_with_engagement,
    extract_activity_fields,
    filter_by_location,
    filter_complete_profiles,
    filter_posts_by_reactions,
    normalize_linkedin_url,
    prefilter_engagers_by_headline,
)

logger = logging.getLogger(__name__)

# Type alias for Slack progress callback
ProgressCallback = Callable[[str], Coroutine[Any, Any, None]]


async def _get_existing_profile_urls() -> set[str]:
    """Get normalized LinkedIn URLs of prospects already in DB."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Prospect.linkedin_url).where(
                Prospect.activity_score.isnot(None)
            )
        )
        return {normalize_linkedin_url(url) for url in result.scalars().all() if url}


async def _sync_profiles_to_db(
    profiles: list[dict],
    icp_qualified_urls: set[str],
    icp_description: str,
) -> tuple[int, int]:
    """Upsert ALL scraped profiles to DB (enriches pool for future searches).

    Returns (created, updated) counts.
    """
    if not profiles:
        return 0, 0

    async with async_session_factory() as session:
        created = 0
        updated = 0

        for p in profiles:
            li_url = normalize_linkedin_url(
                p.get("linkedinUrl") or p.get("profileUrl") or p.get("url") or ""
            )
            if not li_url:
                continue

            activity = extract_activity_fields(p)
            is_icp = li_url in icp_qualified_urls

            values = {
                "linkedin_url": li_url,
                "full_name": p.get("fullName") or p.get("full_name"),
                "first_name": p.get("firstName") or p.get("first_name"),
                "last_name": p.get("lastName") or p.get("last_name"),
                "job_title": p.get("jobTitle") or p.get("job_title") or p.get("position"),
                "company_name": p.get("companyName") or p.get("company_name") or p.get("company"),
                "company_industry": p.get("companyIndustry") or p.get("company_industry"),
                "location": p.get("addressWithCountry") or p.get("location"),
                "headline": p.get("headline"),
                "email": p.get("email") or p.get("emailAddress"),
                "engagement_type": p.get("engagement_type"),
                "source_post_url": p.get("source_post_url"),
                "source_type": ProspectSource.COMPETITOR_POST,
                "connection_count": activity["connection_count"],
                "follower_count": activity["follower_count"],
                "is_creator": activity["is_creator"],
                "activity_score": activity["activity_score"],
            }

            if is_icp:
                values["icp_match"] = True
                values["icp_reason"] = p.get("icp_reason", "")

            # Upsert: insert or update on linkedin_url conflict
            stmt = pg_insert(Prospect).values(**values)
            update_cols = {
                k: v for k, v in values.items()
                if k != "linkedin_url" and v is not None
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["linkedin_url"],
                set_=update_cols,
            )

            try:
                result = await session.execute(stmt)
                if result.rowcount:
                    # Can't distinguish insert vs update with on_conflict_do_update,
                    # but we count all as processed
                    created += 1
            except Exception as e:
                logger.error(f"Error upserting profile {li_url}: {e}")

        await session.commit()
        logger.info(f"Synced {created} profiles to DB")
        return created, 0


async def run_gift_leads_pipeline_async(
    prospect_url: str,
    prospect_name: str,
    progress: ProgressCallback | None = None,
    user_icp: str | None = None,
    user_pain_points: str | None = None,
    days_back: int = DEFAULT_DAYS_BACK,
    min_reactions: int = DEFAULT_MIN_REACTIONS,
    countries: list[str] | None = None,
    min_leads: int = DEFAULT_MIN_LEADS,
    max_leads: int = DEFAULT_MAX_LEADS,
) -> dict[str, Any]:
    """Main async 12-step gift leads pipeline.

    Args:
        prospect_url: LinkedIn profile URL of the prospect.
        prospect_name: Display name for Slack updates.
        progress: Async callback for Slack thread progress updates.
        user_icp: Optional ICP description override.
        user_pain_points: Optional pain points override.
        days_back: Days to look back for posts.
        min_reactions: Minimum reactions to consider a post.
        countries: Allowed countries for leads.
        min_leads: Target minimum leads (triggers early-stop).
        max_leads: Maximum leads to return.

    Returns:
        Dict with pipeline results, leads, and metadata.
    """
    if countries is None:
        countries = DEFAULT_COUNTRIES

    cost_tracker = CostTracker()
    start_time = time.time()

    async def _progress(msg: str) -> None:
        if progress:
            await progress(msg)
        logger.info(f"[gift-pipeline] {msg}")

    metrics: dict[str, Any] = {
        "prospect_url": prospect_url,
        "prospect_name": prospect_name,
        "icp_description": "",
        "queries_generated": 0,
        "posts_found": 0,
        "posts_filtered": 0,
        "engagers_found": 0,
        "prefilter_kept": 0,
        "profiles_scraped": 0,
        "location_filtered": 0,
        "icp_qualified": 0,
        "final_leads": 0,
    }

    # ── Step 1: Scrape prospect profile ──
    await _progress("Step 1/12: Scraping prospect profile...")
    from app.services.gift_pipeline.apify_actors import scrape_linkedin_profiles as scrape_profiles
    existing_urls = await _get_existing_profile_urls()

    prospect_profiles = await scrape_profiles(
        [prospect_url], existing_urls, cost_tracker,
        wait_seconds=60, poll_interval=15,
    )

    if not prospect_profiles:
        # Try DB fallback for prospect profile
        async with async_session_factory() as session:
            result = await session.execute(
                select(Prospect).where(
                    Prospect.linkedin_url == normalize_linkedin_url(prospect_url)
                )
            )
            db_prospect = result.scalar_one_or_none()
            if db_prospect:
                prospect_profile = {
                    "fullName": db_prospect.full_name,
                    "headline": db_prospect.headline,
                    "jobTitle": db_prospect.job_title,
                    "companyName": db_prospect.company_name,
                    "companyIndustry": db_prospect.company_industry,
                }
            else:
                await _progress("Could not scrape prospect profile. Pipeline stopped.")
                return {"error": "Could not scrape prospect profile", "leads": [], "metrics": metrics}
    else:
        prospect_profile = prospect_profiles[0]

    # ── Step 2: Research prospect's business ──
    await _progress("Step 2/12: Researching prospect's business...")
    research = await research_prospect_business(
        prospect_profile, cost_tracker, user_icp, user_pain_points,
    )

    icp_description = research.get("icp_description", "")
    metrics["icp_description"] = icp_description
    await _progress(f"ICP: {icp_description[:100]}...")

    # ── Step 3: Generate search queries ──
    await _progress("Step 3/12: Generating search queries...")
    queries = await generate_search_queries(
        research, cost_tracker, days_back, prospect_profile,
    )
    metrics["queries_generated"] = len(queries)
    await _progress(f"Generated {len(queries)} search queries")

    if not queries:
        await _progress("No queries generated. Pipeline stopped.")
        return {"error": "No queries generated", "leads": [], "metrics": metrics}

    # ── Step 4: Search Google for LinkedIn posts ──
    await _progress("Step 4/12: Searching Google for LinkedIn posts...")
    all_search_results: list[dict] = []
    for i, query in enumerate(queries, 1):
        try:
            results = await search_google(query, cost_tracker)
            all_search_results.extend(results)
        except Exception as e:
            logger.error(f"Google search error for query {i}: {e}")

    metrics["posts_found"] = len(all_search_results)
    await _progress(f"Found {len(all_search_results)} search results")

    if not all_search_results:
        await _progress("No posts found. Pipeline stopped.")
        return {"error": "No posts found", "leads": [], "metrics": metrics}

    # ── Step 5: Filter posts by reactions ──
    await _progress("Step 5/12: Filtering posts by reactions...")
    posts: list[dict] = []
    for result in all_search_results:
        if "organicResults" in result:
            organic = result["organicResults"]
            if isinstance(organic, list):
                posts.extend(organic)
            else:
                posts.append(organic)
        else:
            posts.append(result)

    filtered_posts = filter_posts_by_reactions(posts, min_reactions)
    metrics["posts_filtered"] = len(filtered_posts)
    await _progress(f"{len(filtered_posts)} posts with {min_reactions}+ reactions")

    if not filtered_posts:
        await _progress("No posts meet reaction threshold. Pipeline stopped.")
        return {"error": "No posts with enough reactions", "leads": [], "metrics": metrics}

    # ── Step 6: Scrape post engagers ──
    await _progress("Step 6/12: Scraping post engagers...")
    post_urls = [
        p.get("url", p.get("link", ""))
        for p in filtered_posts
        if p.get("url") or p.get("link")
    ]
    engagers = await scrape_post_engagers(post_urls, cost_tracker)
    metrics["engagers_found"] = len(engagers)
    await _progress(f"{len(engagers)} engagers found")

    if not engagers:
        await _progress("No engagers found. Pipeline stopped.")
        return {"error": "No engagers found", "leads": [], "metrics": metrics}

    # Build engagement context
    engagement_context = build_engagement_context(engagers)

    # ── Step 7: Pre-filter by headline ──
    await _progress("Step 7/12: Pre-filtering by headline...")
    engagers, kept, rejected, non_english = prefilter_engagers_by_headline(engagers)
    metrics["prefilter_kept"] = kept
    await _progress(f"{kept} passed headline filter ({rejected} rejected, {non_english} non-English)")

    if not engagers:
        await _progress("All engagers filtered out. Pipeline stopped.")
        return {"error": "All engagers filtered by headline", "leads": [], "metrics": metrics}

    # ── Steps 8-10: Batched scrape → filter → qualify (early-stop) ──
    await _progress("Steps 8-10/12: Scraping profiles & qualifying...")
    profile_urls = aggregate_profile_urls(engagers)
    profile_urls = deduplicate_profile_urls(profile_urls)
    await _progress(f"{len(profile_urls)} unique profile URLs")

    existing_urls = await _get_existing_profile_urls()
    qualified: list[dict] = []
    all_scraped: list[dict] = []
    total_scraped = 0
    total_location_filtered = 0

    num_batches = (len(profile_urls) + PROFILE_BATCH_SIZE - 1) // PROFILE_BATCH_SIZE
    for batch_idx in range(num_batches):
        batch_start = batch_idx * PROFILE_BATCH_SIZE
        batch_end = min(batch_start + PROFILE_BATCH_SIZE, len(profile_urls))
        batch_urls = profile_urls[batch_start:batch_end]

        await _progress(f"Batch {batch_idx + 1}/{num_batches}: scraping {len(batch_urls)} profiles...")

        profiles = await scrape_linkedin_profiles(
            batch_urls, existing_urls, cost_tracker,
            wait_seconds=120, poll_interval=30,
        )
        total_scraped += len(profiles)

        profiles = enrich_profiles_with_engagement(profiles, engagement_context)
        all_scraped.extend(profiles)

        location_filtered = filter_by_location(profiles, countries)
        total_location_filtered += len(location_filtered)

        complete = filter_complete_profiles(location_filtered)

        if complete:
            batch_qualified = await qualify_leads_with_deepseek(
                complete, cost_tracker, icp_criteria=icp_description,
            )
            qualified.extend(batch_qualified)
            await _progress(
                f"Batch {batch_idx + 1}: {len(batch_qualified)} qualified "
                f"({len(qualified)} total)"
            )

        # Early-stop check
        if len(qualified) >= min_leads:
            remaining = len(profile_urls) - batch_end
            await _progress(
                f"Early stop: {len(qualified)} leads >= {min_leads} target. "
                f"Skipped {remaining} profiles."
            )
            break

    metrics["profiles_scraped"] = total_scraped
    metrics["location_filtered"] = total_location_filtered
    metrics["icp_qualified"] = len(qualified)

    # ── Sync all scraped profiles to DB ──
    await _progress("Syncing profiles to DB...")
    icp_urls = {
        normalize_linkedin_url(q.get("linkedinUrl") or q.get("linkedin_url", ""))
        for q in qualified
    }
    synced, _ = await _sync_profiles_to_db(all_scraped, icp_urls, icp_description)
    await _progress(f"Synced {synced} profiles to DB")

    if not qualified:
        await _progress("No leads passed ICP qualification. Pipeline stopped.")
        return {"error": "No ICP-qualified leads", "leads": [], "metrics": metrics}

    # Cap at max_leads (sort by confidence)
    if len(qualified) > max_leads:
        confidence_order = {"high": 0, "medium": 1, "low": 2, "local": 3, "error": 4}
        qualified.sort(key=lambda x: confidence_order.get(x.get("icp_confidence", "low"), 3))
        qualified = qualified[:max_leads]

    # ── Step 11: Generate signal notes ──
    await _progress("Step 11/12: Generating signal notes...")
    qualified = await generate_signal_notes(qualified, icp_description, cost_tracker)

    # ── Step 12: Format results ──
    await _progress("Step 12/12: Formatting results...")
    leads = []
    for lead in qualified:
        leads.append({
            "full_name": lead.get("fullName") or lead.get("full_name", "Unknown"),
            "job_title": lead.get("jobTitle") or lead.get("job_title", ""),
            "company_name": lead.get("companyName") or lead.get("company_name", ""),
            "location": lead.get("addressWithCountry") or lead.get("location", ""),
            "headline": lead.get("headline", ""),
            "activity_score": compute_activity_score(lead),
            "linkedin_url": lead.get("linkedinUrl") or lead.get("linkedin_url", ""),
            "signal_note": lead.get("signal_note", ""),
            "engagement_type": lead.get("engagement_type", ""),
            "icp_confidence": lead.get("icp_confidence", ""),
            "icp_reason": lead.get("icp_reason", ""),
        })

    metrics["final_leads"] = len(leads)
    elapsed = time.time() - start_time

    await _progress(
        f"Pipeline complete: {len(leads)} leads in {elapsed:.0f}s "
        f"(cost: ${cost_tracker.get_total():.2f})"
    )

    return {
        "leads": leads,
        "metrics": metrics,
        "cost_summary": cost_tracker.get_summary(),
        "cost_total": cost_tracker.get_total(),
        "duration_seconds": int(elapsed),
    }
