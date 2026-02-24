"""Async wrappers for Apify actors used in the gift leads pipeline.

All Apify calls use asyncio.to_thread() to wrap the sync ApifyClient,
following the same pattern as app/services/apify.py.
"""

import asyncio
import logging

import httpx
from apify_client import ApifyClient

from app.config import settings
from app.services.gift_pipeline.constants import (
    GOOGLE_SEARCH_ACTOR,
    POST_REACTIONS_ACTOR,
    PROFILE_SCRAPER_ACTOR,
)
from app.services.gift_pipeline.cost_tracker import CostTracker
from app.services.gift_pipeline.filters import normalize_supreme_coder_profile

logger = logging.getLogger(__name__)


def _get_client() -> ApifyClient:
    return ApifyClient(settings.apify_api_token)


# ---------------------------------------------------------------------------
# Google Search
# ---------------------------------------------------------------------------

def _search_google_sync(query: str) -> list[dict]:
    """Sync: search Google with a pre-formed query string."""
    client = _get_client()
    run = client.actor(GOOGLE_SEARCH_ACTOR).call(run_input={
        "queries": query,
        "maxPagesPerQuery": 1,
        "resultsPerPage": 10,
        "mobileResults": False,
    })
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


async def search_google(query: str, cost_tracker: CostTracker) -> list[dict]:
    """Async: search Google for LinkedIn posts."""
    results = await asyncio.to_thread(_search_google_sync, query)
    cost_tracker.add_google_search(len(results))
    return results


# ---------------------------------------------------------------------------
# Post Engagers (reactions)
# ---------------------------------------------------------------------------

def _scrape_engagers_sync(post_url: str) -> list[dict]:
    """Sync: scrape engagers from a single LinkedIn post."""
    client = _get_client()
    run = client.actor(POST_REACTIONS_ACTOR).call(run_input={
        "post_urls": [post_url],
    })
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


async def scrape_post_engagers(
    post_urls: list[str], cost_tracker: CostTracker,
) -> list[dict]:
    """Async: scrape engagers from multiple posts sequentially."""
    all_engagers: list[dict] = []
    for url in post_urls:
        try:
            engagers = await asyncio.to_thread(_scrape_engagers_sync, url)
            all_engagers.extend(engagers)
            cost_tracker.add_post_reactions(1)
        except Exception as e:
            logger.error(f"Error scraping engagers from {url}: {e}")
    logger.info(f"Found {len(all_engagers)} total engagers from {len(post_urls)} posts")
    return all_engagers


# ---------------------------------------------------------------------------
# Profile Scraper (with DB cache instead of file cache)
# ---------------------------------------------------------------------------

async def _start_and_poll_profile_scraper(
    urls: list[str],
    wait_seconds: int = 120,
    poll_interval: int = 30,
) -> list[dict]:
    """Start profile scraper run, poll for completion, return normalized profiles.

    Uses httpx.AsyncClient + asyncio.sleep for non-blocking polling.
    """
    token = settings.apify_api_token
    start_url = f"https://api.apify.com/v2/acts/{PROFILE_SCRAPER_ACTOR}/runs?token={token}"
    payload = {"urls": [{"url": u} for u in urls]}

    async with httpx.AsyncClient(timeout=60) as http:
        # Start the run
        resp = await http.post(start_url, json=payload)
        resp.raise_for_status()
        run_data = resp.json()["data"]
        run_id = run_data["id"]
        dataset_id = run_data["defaultDatasetId"]
        logger.info(f"Profile scraper run started: {run_id} ({len(urls)} URLs)")

        # Wait initial period
        await asyncio.sleep(wait_seconds)

        # Poll for completion
        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}"
        while True:
            status_resp = await http.get(status_url)
            status_resp.raise_for_status()
            status = status_resp.json()["data"]["status"]

            if status in ("SUCCEEDED", "ABORTED"):
                logger.info(f"Profile scraper finished: {status}")
                break

            logger.info(f"Profile scraper status: {status}, waiting {poll_interval}s...")
            await asyncio.sleep(poll_interval)

        # Fetch results
        data_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}"
        data_resp = await http.get(data_url, headers={"Accept": "application/json"})
        data_resp.raise_for_status()
        raw_profiles = data_resp.json()

    return [normalize_supreme_coder_profile(r) for r in raw_profiles]


async def scrape_linkedin_profiles(
    profile_urls: list[str],
    existing_urls: set[str],
    cost_tracker: CostTracker,
    wait_seconds: int = 120,
    poll_interval: int = 30,
) -> list[dict]:
    """Async: scrape LinkedIn profiles, skipping those already in DB.

    Args:
        profile_urls: URLs to scrape.
        existing_urls: Set of normalized LinkedIn URLs already in DB (skip these).
        cost_tracker: CostTracker instance.
        wait_seconds: Initial wait for scraper.
        poll_interval: Polling interval.

    Returns:
        List of normalized profile dicts (only newly scraped ones).
    """
    from app.services.gift_pipeline.filters import normalize_linkedin_url

    urls_to_scrape = [
        url for url in profile_urls
        if normalize_linkedin_url(url) not in existing_urls
    ]

    logger.info(
        f"Profile scrape: {len(profile_urls)} total, "
        f"{len(profile_urls) - len(urls_to_scrape)} cached in DB, "
        f"{len(urls_to_scrape)} to scrape"
    )

    if not urls_to_scrape:
        return []

    try:
        profiles = await _start_and_poll_profile_scraper(
            urls_to_scrape,
            wait_seconds=wait_seconds,
            poll_interval=poll_interval,
        )
        cost_tracker.add_profile_scrape(len(profiles))
        logger.info(f"Scraped {len(profiles)} new profiles")
        return profiles
    except Exception as e:
        logger.error(f"Profile scraper error: {e}", exc_info=True)
        return []
