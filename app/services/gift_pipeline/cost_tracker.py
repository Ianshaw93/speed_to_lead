"""Cost tracking for the gift leads pipeline."""

from app.services.gift_pipeline.constants import APIFY_COSTS, DEEPSEEK_COSTS


class CostTracker:
    """Track costs across pipeline operations."""

    def __init__(self) -> None:
        self.costs = {
            "apify_google_search": 0.0,
            "apify_post_reactions": 0.0,
            "apify_profile_scraper": 0.0,
            "deepseek_icp": 0.0,
            "deepseek_personalization": 0.0,
        }
        self.counts = {
            "google_results": 0,
            "posts_scraped": 0,
            "profiles_scraped": 0,
            "icp_checks": 0,
            "personalizations": 0,
        }

    def add_google_search(self, num_results: int) -> None:
        self.counts["google_results"] += num_results
        self.costs["apify_google_search"] += num_results * APIFY_COSTS["google_search"]

    def add_post_reactions(self, num_posts: int) -> None:
        self.counts["posts_scraped"] += num_posts
        self.costs["apify_post_reactions"] += num_posts * APIFY_COSTS["post_reactions"]

    def add_profile_scrape(self, num_profiles: int) -> None:
        self.counts["profiles_scraped"] += num_profiles
        self.costs["apify_profile_scraper"] += num_profiles * APIFY_COSTS["profile_scraper"]

    def add_icp_check(self, num_checks: int = 1) -> None:
        self.counts["icp_checks"] += num_checks
        tokens = num_checks * DEEPSEEK_COSTS["avg_icp_tokens"]
        cost = (tokens / 1_000_000) * (DEEPSEEK_COSTS["input_per_1m"] + DEEPSEEK_COSTS["output_per_1m"]) / 2
        self.costs["deepseek_icp"] += cost

    def add_personalization(self, num_msgs: int = 1) -> None:
        self.counts["personalizations"] += num_msgs
        tokens = num_msgs * DEEPSEEK_COSTS["avg_personalization_tokens"]
        cost = (tokens / 1_000_000) * (DEEPSEEK_COSTS["input_per_1m"] + DEEPSEEK_COSTS["output_per_1m"]) / 2
        self.costs["deepseek_personalization"] += cost

    def get_total(self) -> float:
        return sum(self.costs.values())

    def get_summary(self) -> str:
        lines = [
            f"Google Search: ${self.costs['apify_google_search']:.4f} ({self.counts['google_results']} results)",
            f"Post Reactions: ${self.costs['apify_post_reactions']:.4f} ({self.counts['posts_scraped']} posts)",
            f"Profile Scraper: ${self.costs['apify_profile_scraper']:.4f} ({self.counts['profiles_scraped']} profiles)",
            f"DeepSeek ICP: ${self.costs['deepseek_icp']:.4f} ({self.counts['icp_checks']} checks)",
            f"DeepSeek Signal Notes: ${self.costs['deepseek_personalization']:.4f} ({self.counts['personalizations']} msgs)",
            f"TOTAL: ${self.get_total():.4f}",
        ]
        return " | ".join(lines)
