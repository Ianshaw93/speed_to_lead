"""Apify service for scraping LinkedIn profile posts."""

import logging

from apify_client import ApifyClient

from app.config import settings

logger = logging.getLogger(__name__)

# datadoping/linkedin-profile-posts-scraper (No Cookie)
LINKEDIN_POSTS_ACTOR = "RE0MriXnFhR3IgVnJ"


class ApifyError(Exception):
    """Custom exception for Apify API errors."""

    pass


class ApifyService:
    """Service for interacting with Apify actors."""

    def __init__(self, api_token: str | None = None):
        self._api_token = api_token or settings.apify_api_token
        self._client = ApifyClient(self._api_token)

    def scrape_profile_posts(
        self,
        linkedin_urls: list[str],
        max_posts: int = 10,
    ) -> tuple[list[dict], float]:
        """Scrape recent posts from LinkedIn profiles.

        This is a synchronous call (Apify client is sync). Callers should
        wrap in asyncio.to_thread() for async usage.

        Args:
            linkedin_urls: List of LinkedIn profile URLs to scrape.
            max_posts: Maximum posts per profile.

        Returns:
            Tuple of (list of post dicts, cost in USD).

        Raises:
            ApifyError: If the actor run fails.
        """
        logger.info(f"Apify scraping posts for {len(linkedin_urls)} profiles")

        try:
            run = self._client.actor(LINKEDIN_POSTS_ACTOR).call(
                run_input={
                    "profiles": linkedin_urls,
                    "maxPosts": max_posts,
                }
            )

            cost_usd = float(run.get("usageTotalUsd", 0) or 0)

            results = list(
                self._client.dataset(run["defaultDatasetId"]).iterate_items()
            )

            logger.info(f"Apify returned {len(results)} posts (cost: ${cost_usd:.4f})")
            return results, cost_usd

        except Exception as e:
            raise ApifyError(f"Apify scrape failed: {e}") from e

    @staticmethod
    def extract_post_url(item: dict) -> str | None:
        """Extract and normalize the post URL from an actor result item."""
        url = item.get("postUrl") or item.get("url") or item.get("post_url") or ""
        if not url:
            return None
        # Strip query params
        if "?" in url:
            url = url.split("?")[0]
        return url

    @staticmethod
    def extract_post_text(item: dict) -> str:
        """Extract post text/content from an actor result item."""
        return (
            item.get("text")
            or item.get("postText")
            or item.get("content")
            or item.get("description")
            or ""
        )


# Global singleton
_service: ApifyService | None = None


def get_apify_service() -> ApifyService:
    """Get or create the Apify service singleton."""
    global _service
    if _service is None:
        _service = ApifyService()
    return _service
