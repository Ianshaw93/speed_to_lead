"""Apify service for searching LinkedIn posts via Google Search actor."""

import logging
import re
from datetime import datetime, timedelta

from apify_client import ApifyClient

from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_ACTOR = "nFJndFXA5zjCTuudP"


class ApifyError(Exception):
    """Custom exception for Apify API errors."""

    pass


class ApifyService:
    """Service for interacting with Apify actors."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or settings.apify_api_key
        self._client = ApifyClient(self._api_key)

    def search_linkedin_posts(
        self,
        author_name: str,
        days_back: int = 3,
        max_results: int = 5,
    ) -> list[dict]:
        """Search for recent LinkedIn posts by an author using Google Search.

        This is a synchronous call (Apify client is sync). Callers should
        wrap in asyncio.to_thread() for async usage.

        Args:
            author_name: Name of the LinkedIn post author.
            days_back: How many days back to search.
            max_results: Maximum number of results per page.

        Returns:
            List of result dicts with 'url', 'title', 'description' keys.

        Raises:
            ApifyError: If the actor run fails.
        """
        date_cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        query = f'site:linkedin.com/posts "{author_name}" after:{date_cutoff}'

        logger.info(f"Apify search: {query}")

        try:
            run = self._client.actor(GOOGLE_SEARCH_ACTOR).call(
                run_input={
                    "queries": query,
                    "maxPagesPerQuery": 1,
                    "resultsPerPage": max_results,
                    "mobileResults": False,
                }
            )

            results = []
            for item in self._client.dataset(run["defaultDatasetId"]).iterate_items():
                organic = item.get("organicResults", [])
                if isinstance(organic, list):
                    results.extend(organic)
                elif isinstance(organic, dict):
                    results.append(organic)

            # Filter to actual LinkedIn post URLs and extract data
            posts = []
            for r in results:
                url = r.get("url", "")
                if not self._is_linkedin_post_url(url):
                    continue
                posts.append({
                    "url": self._normalize_post_url(url),
                    "title": r.get("title", ""),
                    "description": r.get("description", ""),
                })

            logger.info(f"Found {len(posts)} LinkedIn posts for '{author_name}'")
            return posts

        except Exception as e:
            raise ApifyError(f"Apify search failed for '{author_name}': {e}") from e

    @staticmethod
    def _is_linkedin_post_url(url: str) -> bool:
        """Check if a URL is a LinkedIn post URL."""
        return bool(re.search(r"linkedin\.com/(posts|feed/update)", url))

    @staticmethod
    def _normalize_post_url(url: str) -> str:
        """Normalize a LinkedIn post URL by removing query params."""
        if "?" in url:
            url = url.split("?")[0]
        return url


# Global singleton
_service: ApifyService | None = None


def get_apify_service() -> ApifyService:
    """Get or create the Apify service singleton."""
    global _service
    if _service is None:
        _service = ApifyService()
    return _service
