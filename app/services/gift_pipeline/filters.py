"""Pure filter functions for the gift leads pipeline."""

import logging
import re
from datetime import datetime, timezone

from app.services.gift_pipeline.constants import (
    EMPTY_HEADLINE_INDICATORS,
    HEADLINE_REJECT_KEYWORDS,
    NON_ENGLISH_INDICATORS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

def normalize_linkedin_url(url: str) -> str:
    """Normalize LinkedIn URL: lowercase, strip query params and trailing slash."""
    return url.split("?")[0].rstrip("/").lower()


# ---------------------------------------------------------------------------
# Profile normalization (supreme_coder → unified format)
# ---------------------------------------------------------------------------

def _flatten_positions(positions: list) -> list:
    """Flatten supreme_coder positions which may have nested position groups."""
    flat = []
    for pos in positions:
        if "positions" in pos and isinstance(pos["positions"], list):
            company = pos.get("company", {})
            for sub in pos["positions"]:
                merged = {**sub, "company": company}
                flat.append(merged)
        else:
            flat.append(pos)
    return flat


def normalize_supreme_coder_profile(raw: dict) -> dict:
    """Convert supreme_coder actor output to unified field format."""
    positions = _flatten_positions(raw.get("positions", []))

    experiences = []
    for pos in positions:
        company_obj = pos.get("company") or {}
        exp = {
            "companyName": company_obj.get("name", ""),
            "title": pos.get("title", ""),
            "jobDescription": pos.get("description", ""),
            "location": pos.get("locationName", ""),
            "totalDuration": pos.get("totalDuration", ""),
        }
        tp = pos.get("timePeriod") or {}
        if tp:
            start = tp.get("startDate") or {}
            month = start.get("month", "")
            year = start.get("year", "")
            exp["startedOn"] = f"{month}-{year}" if month and year else str(year)
            exp["stillWorking"] = tp.get("endDate") is None
        experiences.append(exp)

    current = positions[0] if positions else {}
    current_company = current.get("company") or {}

    job_title = raw.get("jobTitle") or current.get("title", "")
    company_name = raw.get("companyName") or current_company.get("name", "")

    geo_location = raw.get("geoLocationName", "")
    if geo_location and "," in geo_location:
        addr_without_country = geo_location.rsplit(",", 1)[0].strip()
    else:
        addr_without_country = geo_location

    return {
        "linkedinUrl": raw.get("inputUrl", ""),
        "firstName": raw.get("firstName", ""),
        "lastName": raw.get("lastName", ""),
        "fullName": f"{raw.get('firstName', '')} {raw.get('lastName', '')}".strip(),
        "headline": raw.get("headline", ""),
        "about": raw.get("summary", ""),
        "jobTitle": job_title,
        "companyName": company_name,
        "companyIndustry": None,
        "addressCountryOnly": raw.get("geoCountryName", ""),
        "addressWithCountry": geo_location,
        "addressWithoutCountry": addr_without_country,
        "connectionsCount": raw.get("connectionsCount", 0),
        "followersCount": raw.get("followerCount", 0),
        "experiences": experiences,
        "experiencesCount": len(experiences),
        "isCreator": raw.get("creator", False),
        "linkedinId": raw.get("id", ""),
        "publicIdentifier": raw.get("publicIdentifier", ""),
        "profilePic": raw.get("pictureUrl"),
    }


# ---------------------------------------------------------------------------
# Activity scoring
# ---------------------------------------------------------------------------

def compute_activity_score(profile: dict) -> float:
    """Compute activity score (0-100) from LinkedIn profile data."""
    score = 0.0

    connections = profile.get("connectionsCount") or profile.get("connection_count") or 0
    if isinstance(connections, str):
        connections = int(connections.replace(",", "").replace("+", "")) if connections.strip() else 0
    score += min(30, (connections / 500) * 30)

    followers = profile.get("followersCount") or profile.get("follower_count") or 0
    if isinstance(followers, str):
        followers = int(followers.replace(",", "").replace("+", "")) if followers.strip() else 0
    score += min(30, (followers / 1000) * 30)

    is_creator = profile.get("isCreator") or profile.get("is_creator") or False
    if not is_creator:
        posts = profile.get("posts") or profile.get("articles") or []
        is_creator = len(posts) > 0 if isinstance(posts, list) else bool(posts)
    if is_creator:
        score += 20

    has_engagement = bool(profile.get("engagement_type") or profile.get("engagement_comment"))
    if has_engagement:
        score += 20

    return round(score, 2)


def extract_activity_fields(profile: dict) -> dict:
    """Extract activity-related fields from a profile for DB sync."""
    connections = profile.get("connectionsCount") or profile.get("connection_count") or 0
    if isinstance(connections, str):
        connections = int(connections.replace(",", "").replace("+", "")) if connections.strip() else 0

    followers = profile.get("followersCount") or profile.get("follower_count") or 0
    if isinstance(followers, str):
        followers = int(followers.replace(",", "").replace("+", "")) if followers.strip() else 0

    is_creator = profile.get("isCreator") or profile.get("is_creator") or False
    if not is_creator:
        posts = profile.get("posts") or profile.get("articles") or []
        is_creator = len(posts) > 0 if isinstance(posts, list) else bool(posts)

    return {
        "connection_count": connections if connections else None,
        "follower_count": followers if followers else None,
        "is_creator": is_creator if is_creator else None,
        "activity_score": compute_activity_score(profile),
    }


# ---------------------------------------------------------------------------
# Headline pre-filter
# ---------------------------------------------------------------------------

def is_likely_english(text: str) -> tuple[bool, str]:
    """Check if text is likely English based on character analysis."""
    if not text or len(text) < 3:
        return True, "too short to analyze"

    non_ascii_chars = sum(1 for c in text if ord(c) > 127)
    non_ascii_ratio = non_ascii_chars / len(text)
    if non_ascii_ratio > 0.15:
        return False, f"high non-ASCII ratio ({non_ascii_ratio:.0%})"

    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af')
    if cjk_count > 0:
        return False, "contains CJK characters"

    cyrillic_count = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
    if cyrillic_count > 0:
        return False, "contains Cyrillic characters"

    arabic_count = sum(1 for c in text if '\u0600' <= c <= '\u06ff')
    if arabic_count > 0:
        return False, "contains Arabic characters"

    text_lower = text.lower()
    for indicator in NON_ENGLISH_INDICATORS:
        if indicator in text_lower:
            return False, f"contains '{indicator}'"

    return True, "appears English"


def prefilter_engagers_by_headline(
    engagers: list[dict],
) -> tuple[list[dict], int, int, int]:
    """Pre-filter engagers by headline before expensive profile scraping.

    Returns:
        (filtered_engagers, kept_count, rejected_count, non_english_count)
    """
    filtered = []
    rejected_count = 0
    non_english_count = 0

    for engager in engagers:
        reactor = engager.get("reactor", {})
        headline_raw = (reactor.get("headline") or "").strip()
        headline = headline_raw.lower()

        if not headline:
            filtered.append(engager)
            continue

        is_english, _ = is_likely_english(headline_raw)
        if not is_english:
            non_english_count += 1
            continue

        is_rejected = any(kw in headline for kw in HEADLINE_REJECT_KEYWORDS)
        if is_rejected:
            rejected_count += 1
            continue

        filtered.append(engager)

    return filtered, len(filtered), rejected_count, non_english_count


# ---------------------------------------------------------------------------
# Reaction count extraction & post filtering
# ---------------------------------------------------------------------------

def extract_reaction_count(reaction_str: str | None) -> int:
    """Extract numeric reaction count from string like '150+ reactions'."""
    if not reaction_str:
        return 0
    match = re.search(r'([\d,]+)\+?\s*reactions?', str(reaction_str), re.IGNORECASE)
    if match:
        return int(match.group(1).replace(',', ''))
    return 0


def filter_posts_by_reactions(posts: list[dict], min_reactions: int = 50) -> list[dict]:
    """Filter posts to keep only those with min_reactions or more."""
    filtered = []
    no_reaction_data = True

    for post in posts:
        reaction_str = post.get("followersAmount", "") or post.get("description", "") or ""
        count = extract_reaction_count(reaction_str)

        if count > 0:
            no_reaction_data = False
            if count >= min_reactions:
                filtered.append(post)
        else:
            url = post.get("url", post.get("link", ""))
            if "linkedin.com/posts" in url:
                filtered.append(post)

    if no_reaction_data and filtered:
        logger.info(f"No reaction data in search results, including all {len(filtered)} LinkedIn posts")

    return filtered


# ---------------------------------------------------------------------------
# Engagement context
# ---------------------------------------------------------------------------

def extract_post_date_from_url(post_url: str) -> datetime | None:
    """Extract post date from LinkedIn activity ID in URL."""
    match = re.search(r'activity-(\d+)', post_url)
    if not match:
        return None
    try:
        activity_id = int(match.group(1))
        timestamp_ms = activity_id >> 22
        linkedin_epoch_ms = 1288834974657
        actual_timestamp_ms = timestamp_ms + linkedin_epoch_ms
        return datetime.fromtimestamp(actual_timestamp_ms / 1000, tz=timezone.utc)
    except Exception:
        return None


def aggregate_profile_urls(engagers: list[dict]) -> list[str]:
    """Extract profile URLs from post engagers."""
    urls = []
    for engager in engagers:
        reactor = engager.get("reactor", {})
        profile_url = reactor.get("profile_url", "")
        if profile_url:
            urls.append(profile_url)
    return urls


def deduplicate_profile_urls(urls: list[str]) -> list[str]:
    """Remove duplicate profile URLs while preserving order."""
    seen: set[str] = set()
    unique = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def build_engagement_context(engagers: list[dict]) -> dict[str, dict]:
    """Build mapping of normalized profile_url → engagement context."""
    context: dict[str, dict] = {}
    scrape_time = datetime.now(tz=timezone.utc)

    for engager in engagers:
        reactor = engager.get("reactor", {})
        profile_url = reactor.get("profile_url", "")
        if not profile_url:
            continue

        normalized_url = normalize_linkedin_url(profile_url)
        metadata = engager.get("_metadata", {})
        post_url = metadata.get("post_url") or engager.get("input", "")

        context[normalized_url] = {
            "engagement_type": engager.get("reaction_type", "LIKE"),
            "source_post_url": post_url,
            "post_date": extract_post_date_from_url(post_url),
            "total_reactions": metadata.get("total_reactions"),
            "scraped_at": scrape_time,
        }

    return context


def enrich_profiles_with_engagement(
    profiles: list[dict], engagement_context: dict[str, dict],
) -> list[dict]:
    """Add engagement context fields to scraped profiles."""
    for profile in profiles:
        linkedin_url = profile.get("linkedinUrl") or profile.get("profileUrl") or ""
        normalized_url = normalize_linkedin_url(linkedin_url)

        engagement = engagement_context.get(normalized_url, {})
        if engagement:
            profile["engagement_type"] = engagement.get("engagement_type")
            profile["source_post_url"] = engagement.get("source_post_url")
            pd = engagement.get("post_date")
            profile["post_date"] = pd.isoformat() if pd else None
            sa = engagement.get("scraped_at")
            profile["scraped_at"] = sa.isoformat() if sa else None

    return profiles


# ---------------------------------------------------------------------------
# Location filter
# ---------------------------------------------------------------------------

def filter_by_location(profiles: list[dict], allowed_countries: list[str]) -> list[dict]:
    """Filter profiles by country."""
    allowed_normalized = [c.lower() for c in allowed_countries]
    return [
        p for p in profiles
        if (p.get("addressCountryOnly") or "").lower() in allowed_normalized
    ]


# ---------------------------------------------------------------------------
# Profile completeness filter
# ---------------------------------------------------------------------------

def is_profile_complete(lead: dict) -> dict:
    """Check if a LinkedIn profile has enough data to evaluate."""
    missing_fields = []

    headline = (lead.get("headline") or "").strip().lower()
    if not headline or headline in EMPTY_HEADLINE_INDICATORS:
        missing_fields.append("headline")

    if not (lead.get("jobTitle") or lead.get("job_title")):
        missing_fields.append("jobTitle")

    if not (lead.get("companyName") or lead.get("company")):
        missing_fields.append("companyName")

    exp_count = lead.get("experiencesCount", 0)
    experiences = lead.get("experiences", [])
    if exp_count == 0 and len(experiences) == 0:
        missing_fields.append("experiences")

    has_headline = "headline" not in missing_fields
    has_job_info = "jobTitle" not in missing_fields and "companyName" not in missing_fields
    has_experience = "experiences" not in missing_fields

    is_complete = has_job_info or (has_headline and has_experience)

    return {
        "complete": is_complete,
        "reason": "sufficient data" if is_complete else f"missing: {', '.join(missing_fields)}",
        "missing_fields": missing_fields,
    }


def filter_complete_profiles(leads: list[dict]) -> list[dict]:
    """Filter out leads with incomplete profiles."""
    complete = []
    for lead in leads:
        result = is_profile_complete(lead)
        if result["complete"]:
            complete.append(lead)
    logger.info(f"Profile completeness: {len(leads)} → {len(complete)} leads")
    return complete
