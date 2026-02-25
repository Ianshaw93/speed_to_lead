"""Competitor post pipeline: find ICP leads from LinkedIn post engagers.

13-step pipeline:
  Google Search -> Filter reactions -> Scrape engagers -> Headline pre-filter
  -> Dedup -> DB dedup -> Scrape profiles -> Location filter -> Completeness filter
  -> ICP qualify (DeepSeek) -> Personalize (DeepSeek) -> Validate & fix -> Upload to HeyReach

Ported from multichannel-outreach/execution/competitor_post_pipeline.py to run
as an async FastAPI service with DB-backed dedup and PipelineRun tracking.
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import PipelineRun, Prospect, ProspectSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Apify Actor IDs
# ---------------------------------------------------------------------------
GOOGLE_SEARCH_ACTOR = "nFJndFXA5zjCTuudP"
POST_REACTIONS_ACTOR = "J9UfswnR3Kae4O6vm"
PROFILE_SCRAPER_ACTOR = "supreme_coder~linkedin-profile-scraper"

HEYREACH_LIST_ID = 480247
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# ---------------------------------------------------------------------------
# Cost estimates (USD per unit)
# ---------------------------------------------------------------------------
APIFY_COSTS = {
    "google_search": 0.004,
    "post_reactions": 0.008,
    "profile_scraper": 0.004,
    "leads_finder": 0.003,
}
DEEPSEEK_COSTS = {
    "input_per_1m": 0.14,
    "output_per_1m": 0.28,
    "avg_icp_tokens": 400,
    "avg_personalization_tokens": 800,
}

# ---------------------------------------------------------------------------
# Headline filter lists
# ---------------------------------------------------------------------------
HEADLINE_AUTHORITY_KEYWORDS = [
    "ceo", "founder", "co-founder", "cofounder", "owner",
    "president", "managing director", "partner",
    "vp", "vice president", "director",
    "cto", "cfo", "coo", "cmo", "chief",
    "head of", "principal", "entrepreneur",
]
HEADLINE_REJECT_KEYWORDS = [
    "intern", "student", "trainee", "apprentice",
    "cashier", "driver", "technician", "mechanic",
    "nurse", "teacher", "professor", "doctor", "physician",
    "looking for", "seeking", "open to work",
    "retired", "unemployed",
]
NON_ENGLISH_INDICATORS = [
    "diretor", "gerente", "fundador", "empresário", "sócio", "coordenador",
    "empresario", "socio", "coordinador",
    "directeur", "fondateur", "gérant", "responsable",
    "geschäftsführer", "gründer", "leiter", "inhaber",
    "direttore", "fondatore", "titolare", "amministratore",
    "oprichter", "eigenaar",
]


# ===================================================================
# SHARED APIFY HELPER
# ===================================================================

async def run_apify_actor(
    actor_id: str,
    input_payload: dict,
    initial_wait: int = 60,
    poll_interval: int = 30,
    max_polls: int = 20,
) -> list[dict]:
    """Start an Apify actor, poll until done, return dataset items.

    Runs the blocking sleep in a thread so it doesn't block the event loop.
    """
    token = settings.apify_api_token
    if not token:
        logger.error("APIFY_API_TOKEN not set")
        return []

    start_url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={token}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(start_url, json=input_payload)
        resp.raise_for_status()
        run_data = resp.json()["data"]
        run_id = run_data["id"]
        dataset_id = run_data["defaultDatasetId"]
        logger.info(f"Apify run started: {run_id} (actor={actor_id})")

    # Wait for initial processing
    await asyncio.to_thread(time.sleep, initial_wait)

    # Poll for completion
    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}"
    for _ in range(max_polls):
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(status_url)
            resp.raise_for_status()
            status = resp.json()["data"]["status"]

        if status in ("SUCCEEDED", "ABORTED", "FAILED"):
            logger.info(f"Apify run {run_id} finished: {status}")
            break
        logger.info(f"Apify run {run_id} status: {status}, polling...")
        await asyncio.to_thread(time.sleep, poll_interval)

    # Fetch dataset items
    data_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(data_url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        items = resp.json()

    logger.info(f"Apify run {run_id}: {len(items)} items returned")
    return items


# ===================================================================
# STEP 1: GOOGLE SEARCH
# ===================================================================

def build_google_search_query(keywords: str, days_back: int = 7) -> str:
    date_cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    return f'site:linkedin.com/posts "{keywords}" after:{date_cutoff}'


async def search_google_linkedin_posts(
    keywords: str,
    days_back: int = 7,
    max_pages: int = 1,
    results_per_page: int = 10,
) -> list[dict]:
    query = build_google_search_query(keywords, days_back)
    logger.info(f"Google search query: {query}")
    return await run_apify_actor(
        GOOGLE_SEARCH_ACTOR,
        {"queries": query, "maxPagesPerQuery": max_pages, "resultsPerPage": results_per_page, "mobileResults": False},
        initial_wait=30,
        poll_interval=15,
        max_polls=10,
    )


# ===================================================================
# STEP 2: FILTER BY REACTIONS
# ===================================================================

def extract_reaction_count(reaction_str: str | None) -> int:
    if not reaction_str:
        return 0
    match = re.search(r"([\d,]+)\+?\s*reactions?", str(reaction_str), re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else 0


def filter_posts_by_reactions(posts: list[dict], min_reactions: int = 50) -> list[dict]:
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
        logger.info(f"No reaction data found, keeping all {len(filtered)} LinkedIn posts")
    return filtered


# ===================================================================
# STEP 3: SCRAPE POST ENGAGERS
# ===================================================================

async def scrape_post_engagers(post_urls: list[str]) -> list[dict]:
    all_engagers: list[dict] = []
    for url in post_urls:
        logger.info(f"Scraping engagers from: {url}")
        items = await run_apify_actor(
            POST_REACTIONS_ACTOR,
            {"post_urls": [url]},
            initial_wait=30,
            poll_interval=15,
            max_polls=10,
        )
        all_engagers.extend(items)
    logger.info(f"Total engagers found: {len(all_engagers)}")
    return all_engagers


# ===================================================================
# STEP 4: HEADLINE PRE-FILTER
# ===================================================================

def is_likely_english(text: str) -> tuple[bool, str]:
    if not text or len(text) < 3:
        return True, "too short to analyze"
    non_ascii = sum(1 for c in text if ord(c) > 127)
    if non_ascii / len(text) > 0.15:
        return False, "high non-ASCII ratio"
    if any("\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" or "\uac00" <= c <= "\ud7af" for c in text):
        return False, "contains CJK characters"
    if any("\u0400" <= c <= "\u04ff" for c in text):
        return False, "contains Cyrillic characters"
    if any("\u0600" <= c <= "\u06ff" for c in text):
        return False, "contains Arabic characters"
    text_lower = text.lower()
    for indicator in NON_ENGLISH_INDICATORS:
        if indicator in text_lower:
            return False, f"contains '{indicator}'"
    return True, "appears English"


def prefilter_engagers_by_headline(engagers: list[dict]) -> tuple[list[dict], int, int, int]:
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
        if any(kw in headline for kw in HEADLINE_REJECT_KEYWORDS):
            rejected_count += 1
            continue
        filtered.append(engager)
    logger.info(
        f"Headline pre-filter: {len(engagers)} -> {len(filtered)} "
        f"(rejected={rejected_count}, non-english={non_english_count})"
    )
    return filtered, len(filtered), rejected_count, non_english_count


# ===================================================================
# STEP 5: AGGREGATE & DEDUP URLs
# ===================================================================

def aggregate_and_deduplicate_urls(engagers: list[dict]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for engager in engagers:
        url = engager.get("reactor", {}).get("profile_url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


# ===================================================================
# STEP 6: DB DEDUP
# ===================================================================

def _normalize_url(url: str) -> str:
    return url.lower().strip().rstrip("/").split("?")[0]


async def filter_already_processed(urls: list[str], session: AsyncSession) -> list[str]:
    """Remove URLs that already exist as Prospect records in the DB."""
    if not urls:
        return []
    normalized = [_normalize_url(u) for u in urls]
    result = await session.execute(
        select(Prospect.linkedin_url).where(Prospect.linkedin_url.in_(normalized))
    )
    existing = {row[0] for row in result.all()}
    new_urls = [u for u, n in zip(urls, normalized) if n not in existing]
    logger.info(f"DB dedup: {len(urls)} -> {len(new_urls)} (skipped {len(urls) - len(new_urls)} existing)")
    return new_urls


# ===================================================================
# STEP 7: SCRAPE PROFILES
# ===================================================================

def _flatten_positions(positions: list) -> list:
    flat = []
    for pos in positions:
        if "positions" in pos and isinstance(pos["positions"], list):
            company = pos.get("company", {})
            for sub in pos["positions"]:
                flat.append({**sub, "company": company})
        else:
            flat.append(pos)
    return flat


def normalize_supreme_coder_profile(raw: dict) -> dict:
    """Convert supreme_coder actor output to normalized field format."""
    positions = _flatten_positions(raw.get("positions", []))
    experiences = []
    for pos in positions:
        company_obj = pos.get("company") or {}
        exp = {
            "companyName": company_obj.get("name", ""),
            "title": pos.get("title", ""),
            "jobDescription": pos.get("description", ""),
            "location": pos.get("locationName", ""),
        }
        tp = pos.get("timePeriod") or {}
        if tp:
            start = tp.get("startDate") or {}
            month, year = start.get("month", ""), start.get("year", "")
            exp["startedOn"] = f"{month}-{year}" if month and year else str(year)
            exp["stillWorking"] = tp.get("endDate") is None
        experiences.append(exp)

    current = positions[0] if positions else {}
    current_company = current.get("company") or {}
    job_title = raw.get("jobTitle") or current.get("title", "")
    company_name = raw.get("companyName") or current_company.get("name", "")
    geo_location = raw.get("geoLocationName", "")
    addr_without_country = geo_location.rsplit(",", 1)[0].strip() if "," in geo_location else geo_location

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
        "connections": raw.get("connectionsCount", 0),
        "followers": raw.get("followerCount", 0),
        "experiences": experiences,
        "experiencesCount": len(experiences),
        "profilePic": raw.get("pictureUrl"),
        "email": None,
    }


async def scrape_linkedin_profiles(urls: list[str]) -> list[dict]:
    if not urls:
        return []
    logger.info(f"Scraping {len(urls)} LinkedIn profiles")
    raw_profiles = await run_apify_actor(
        PROFILE_SCRAPER_ACTOR,
        {"urls": [{"url": u} for u in urls]},
        initial_wait=120,
        poll_interval=30,
        max_polls=20,
    )
    return [normalize_supreme_coder_profile(r) for r in raw_profiles]


# ===================================================================
# STEP 8: LOCATION FILTER
# ===================================================================

def filter_by_location(profiles: list[dict], allowed_countries: list[str]) -> list[dict]:
    allowed = [c.lower() for c in allowed_countries]
    filtered = [p for p in profiles if (p.get("addressCountryOnly") or "").lower() in allowed]
    logger.info(f"Location filter: {len(profiles)} -> {len(filtered)}")
    return filtered


# ===================================================================
# STEP 9: COMPLETENESS FILTER
# ===================================================================

EMPTY_HEADLINE_INDICATORS = ["--", "n/a", "na", "-", ""]


def is_profile_complete(lead: dict) -> bool:
    headline = (lead.get("headline") or "").strip().lower()
    has_headline = bool(headline) and headline not in EMPTY_HEADLINE_INDICATORS
    has_job_info = bool(lead.get("jobTitle") or lead.get("job_title")) and bool(lead.get("companyName") or lead.get("company"))
    has_experience = len(lead.get("experiences", [])) > 0 or lead.get("experiencesCount", 0) > 0
    return bool(has_job_info or (has_headline and has_experience))


def filter_complete_profiles(leads: list[dict]) -> list[dict]:
    complete = [l for l in leads if is_profile_complete(l)]
    logger.info(f"Completeness filter: {len(leads)} -> {len(complete)}")
    return complete


# ===================================================================
# STEP 10: ICP QUALIFICATION (DeepSeek)
# ===================================================================

ICP_SYSTEM_PROMPT = """Role: B2B Lead Qualification Filter.

Objective: Categorize LinkedIn profiles based on Authority and Industry fit for a Sales Automation and Personal Branding agency.

Rules for Authority (Strict):
- Qualify: CEOs, Founders, Co-Founders, Managing Directors, Owners, Partners, VPs, and C-Suite executives.
- Reject: Interns, Students, Junior staff, Administrative assistants, and low-level individual contributors.

Rules for B2B Industry (Lenient):
- Qualify: High-ticket service industries (Agencies, SaaS, Consulting, Coaching, Tech).

The "Benefit of Doubt" Rule: If you are unsure if a business is B2B or B2C, or unsure if the person is a top-level decision-maker, Qualify them (Set to true). Only reject if they are clearly non-decision makers or in non-business roles.

Hard Rejections:
- Leads from massive traditional Banking/Financial institutions (e.g., Santander, Getnet).
- Physical labor or local retail roles (e.g., Driver, Technician, Cashier).

You are an expert at evaluating sales leads. Always respond with valid JSON."""


def _build_lead_summary(lead: dict) -> str:
    return f"""Lead: {lead.get('fullName', lead.get('full_name', 'Unknown'))}
Title: {lead.get('jobTitle', lead.get('job_title', lead.get('title', 'Unknown')))}
Headline: {lead.get('headline', 'N/A')}
Company: {lead.get('companyName', lead.get('company', lead.get('company_name', 'Unknown')))}
Company Description: {(lead.get('about') or '')[:300] or 'N/A'}
Location: {lead.get('addressWithCountry', lead.get('location', 'Unknown'))}
Industry: {lead.get('companyIndustry', lead.get('industry', 'N/A'))}"""


async def check_icp_match_deepseek(lead: dict) -> dict:
    """Check if a single lead matches ICP via DeepSeek. Returns {match, confidence, reason}."""
    if not settings.deepseek_api_key:
        return {"match": True, "confidence": "no_api_key", "reason": "No API key, benefit of doubt"}

    user_prompt = f"""Evaluate this LinkedIn profile:

{_build_lead_summary(lead)}

Respond in JSON format:
{{
  "match": true/false,
  "confidence": "high" | "medium" | "low",
  "reason": "Brief explanation (1 sentence)"
}}"""

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            DEEPSEEK_API_URL,
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": ICP_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 150,
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        return json.loads(resp.json()["choices"][0]["message"]["content"])


async def qualify_leads_with_deepseek(leads: list[dict]) -> list[dict]:
    """Qualify a batch of leads with DeepSeek ICP check."""
    qualified = []
    for lead in leads:
        try:
            result = await check_icp_match_deepseek(lead)
        except Exception as e:
            logger.warning(f"ICP check failed for {lead.get('fullName', '?')}: {e}")
            result = {"match": True, "confidence": "error", "reason": str(e)}

        lead["icp_match"] = result.get("match", True)
        lead["icp_confidence"] = result.get("confidence", "unknown")
        lead["icp_reason"] = result.get("reason", "")

        if lead["icp_match"]:
            qualified.append(lead)
        else:
            logger.info(f"ICP reject: {lead.get('fullName', '?')} - {lead['icp_reason']}")

    logger.info(f"ICP qualification: {len(leads)} -> {len(qualified)}")
    return qualified


# ===================================================================
# STEP 11: PERSONALIZATION (DeepSeek)
# ===================================================================

def _casualize_company(company: str) -> str:
    if not company:
        return ""
    suffixes = [", Inc.", ", Inc", ", LLC", ", LTD", ", Ltd", " Inc.", " Inc", " LLC", " LTD", " Ltd", " Corp", " Corporation", " PLC", " plc", " Limited"]
    for s in suffixes:
        if company.endswith(s):
            company = company[: -len(s)]
    company = company.strip().rstrip(",").strip()
    words = company.split()
    if len(words) >= 3:
        abbr = "".join(w[0].upper() for w in words if w[0].isalpha())
        if len(abbr) >= 2:
            return abbr
    return company


def _extract_city(location: str) -> str:
    if not location:
        return ""
    return location.split(",")[0].strip()


# Import the 5-line DM template from buying_signal_outreach to stay consistent
_5LINE_SYSTEM_PROMPT = "You are an expert at creating personalized LinkedIn DMs following strict template rules. You write as a founder, not a salesperson."


async def generate_personalization_deepseek(lead: dict) -> str | None:
    """Generate a personalized 5-line LinkedIn DM for a lead."""
    if not settings.deepseek_api_key:
        logger.warning("DEEPSEEK_API_KEY not set, skipping personalization")
        return None

    first_name = lead.get("firstName", lead.get("first_name", ""))
    if not first_name:
        full_name = lead.get("fullName", lead.get("full_name", ""))
        first_name = full_name.split()[0] if full_name else "there"

    company = _casualize_company(lead.get("companyName", lead.get("company", lead.get("company_name", ""))))
    title = lead.get("jobTitle", lead.get("job_title", lead.get("title", "")))
    headline = lead.get("headline", "")
    about = (lead.get("about") or lead.get("summary") or "")[:200]
    location = _extract_city(lead.get("addressWithCountry", lead.get("location", "")))

    # Use the same 5-line template as buying_signal_outreach
    from app.services.buying_signal_outreach import _build_5line_prompt
    prompt = _build_5line_prompt(
        first_name=first_name,
        company_name=company,
        title=title or "(not available)",
        headline=headline or "(not available)",
        company_description=about or "(not available)",
        location=location or "(not available)",
    )

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                DEEPSEEK_API_URL,
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": _5LINE_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 400,
                    "temperature": 0.7,
                },
            )
            resp.raise_for_status()
            message = resp.json()["choices"][0]["message"]["content"].strip()
            if message.startswith('"') and message.endswith('"'):
                message = message[1:-1]
            message = message.replace("```", "").strip()
            return message
    except Exception as e:
        logger.error(f"Personalization failed for {lead.get('fullName', '?')}: {e}")
        return None


# ===================================================================
# STEP 12: VALIDATE & FIX (DeepSeek)
# ===================================================================

VALIDATION_SYSTEM_PROMPT = """You validate LinkedIn DMs for accuracy. Check if:
1. The service/method mentioned matches the lead's actual business
2. The authority statement is industry-accurate
3. The company name is correctly casualized

Respond in JSON:
{
  "flag": "PASS" | "REVIEW" | "FAIL",
  "inferred_service": "what the message says they do",
  "actual_service": "what they actually do",
  "reason": "brief explanation"
}"""


async def validate_single_message(lead: dict) -> dict:
    """Validate a personalized message against lead data."""
    if not settings.deepseek_api_key:
        return {"flag": "PASS", "reason": "no API key"}

    prompt = f"""Lead: {lead.get('fullName', '?')}
Headline: {lead.get('headline', 'N/A')}
Job Title: {lead.get('jobTitle', 'N/A')}
Company: {lead.get('companyName', 'N/A')}
About: {(lead.get('about') or '')[:200] or 'N/A'}

Message:
{lead.get('personalized_message', '')}"""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                DEEPSEEK_API_URL,
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 200,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            return json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:
        return {"flag": "ERROR", "reason": str(e)}


async def validate_and_fix_batch(leads: list[dict]) -> list[dict]:
    """Validate all personalized messages and regenerate failures."""
    for lead in leads:
        if not lead.get("personalized_message"):
            continue
        result = await validate_single_message(lead)
        lead["validation"] = result
        if result.get("flag") in ("FAIL", "REVIEW"):
            logger.info(f"Re-generating message for {lead.get('fullName', '?')}: {result.get('reason', '')}")
            new_msg = await generate_personalization_deepseek(lead)
            if new_msg:
                lead["personalized_message"] = new_msg
                lead["regenerated"] = True
    return leads


# ===================================================================
# STEP 13: UPLOAD TO HEYREACH
# ===================================================================

async def upload_to_heyreach(leads: list[dict], list_id: int) -> int:
    """Upload leads with personalized messages to HeyReach via the existing client."""
    from app.services.heyreach import get_heyreach_client, HeyReachError

    if not leads:
        return 0

    heyreach = get_heyreach_client()
    formatted = []
    for lead in leads:
        if not lead.get("personalized_message"):
            continue
        formatted.append({
            "linkedin_url": lead.get("linkedinUrl") or lead.get("linkedin_url") or "",
            "first_name": lead.get("firstName") or lead.get("first_name") or "",
            "last_name": lead.get("lastName") or lead.get("last_name") or "",
            "company_name": lead.get("companyName") or lead.get("company_name") or "",
            "job_title": lead.get("jobTitle") or lead.get("job_title") or "",
            "custom_fields": {"personalized_message": lead["personalized_message"]},
        })

    total = 0
    for i in range(0, len(formatted), 100):
        chunk = formatted[i : i + 100]
        try:
            result = await heyreach.add_leads_to_list(list_id, chunk)
            added = result.get("addedCount", 0) if isinstance(result, dict) else len(chunk)
            total += added
        except HeyReachError as e:
            logger.error(f"HeyReach upload failed: {e}")

    logger.info(f"Uploaded {total} leads to HeyReach list {list_id}")
    return total


# ===================================================================
# DB: CREATE PROSPECT RECORDS
# ===================================================================

async def create_prospect_records(
    leads: list[dict],
    source_type: ProspectSource,
    source_keyword: str | None,
    heyreach_list_id: int | None,
    session: AsyncSession,
) -> int:
    """Create Prospect records in the DB for qualified leads. Returns count created."""
    created = 0
    now = datetime.now(timezone.utc)
    for lead in leads:
        url = _normalize_url(lead.get("linkedinUrl") or lead.get("linkedin_url") or "")
        if not url:
            continue
        # Skip if already exists
        existing = await session.execute(select(Prospect.id).where(Prospect.linkedin_url == url))
        if existing.scalar():
            continue

        prospect = Prospect(
            linkedin_url=url,
            full_name=lead.get("fullName") or lead.get("full_name"),
            first_name=lead.get("firstName") or lead.get("first_name"),
            last_name=lead.get("lastName") or lead.get("last_name"),
            job_title=lead.get("jobTitle") or lead.get("job_title"),
            company_name=lead.get("companyName") or lead.get("company_name"),
            company_industry=lead.get("companyIndustry") or lead.get("industry"),
            location=lead.get("addressWithCountry") or lead.get("location"),
            headline=lead.get("headline"),
            email=lead.get("email"),
            source_type=source_type,
            source_keyword=source_keyword,
            icp_match=lead.get("icp_match"),
            icp_reason=lead.get("icp_reason"),
            personalized_message=lead.get("personalized_message"),
            heyreach_list_id=heyreach_list_id if lead.get("personalized_message") else None,
            heyreach_uploaded_at=now if lead.get("personalized_message") and heyreach_list_id else None,
        )
        session.add(prospect)
        created += 1

    if created:
        await session.commit()
    logger.info(f"Created {created} Prospect records (source={source_type.value})")
    return created


# ===================================================================
# SLACK HELPER
# ===================================================================

def _get_slack_bot():
    from app.services.slack import get_slack_bot
    return get_slack_bot()


async def _send_pipeline_summary(run_type: str, summary: dict):
    """Send a Slack summary of the pipeline run."""
    try:
        bot = _get_slack_bot()
        lines = [f"*Pipeline Complete — {run_type}*", ""]
        for k, v in summary.items():
            if k not in ("run_id", "status", "error"):
                lines.append(f"{k}: {v}")
        await bot.send_confirmation("\n".join(lines))
    except Exception as e:
        logger.error(f"Failed to send Slack summary: {e}")


# ===================================================================
# COST TRACKING HELPER
# ===================================================================

def _estimate_costs(counts: dict) -> dict:
    """Estimate costs from operation counts."""
    cost_apify_google = counts.get("google_results", 0) * APIFY_COSTS["google_search"]
    cost_apify_reactions = counts.get("posts_scraped", 0) * APIFY_COSTS["post_reactions"]
    cost_apify_profiles = counts.get("profiles_scraped", 0) * APIFY_COSTS["profile_scraper"]
    icp_tokens = counts.get("icp_checks", 0) * DEEPSEEK_COSTS["avg_icp_tokens"]
    cost_deepseek_icp = (icp_tokens / 1_000_000) * (DEEPSEEK_COSTS["input_per_1m"] + DEEPSEEK_COSTS["output_per_1m"]) / 2
    pers_tokens = counts.get("personalizations", 0) * DEEPSEEK_COSTS["avg_personalization_tokens"]
    cost_deepseek_pers = (pers_tokens / 1_000_000) * (DEEPSEEK_COSTS["input_per_1m"] + DEEPSEEK_COSTS["output_per_1m"]) / 2
    return {
        "cost_apify_google": Decimal(str(round(cost_apify_google, 4))),
        "cost_apify_reactions": Decimal(str(round(cost_apify_reactions, 4))),
        "cost_apify_profiles": Decimal(str(round(cost_apify_profiles, 4))),
        "cost_deepseek_icp": Decimal(str(round(cost_deepseek_icp, 4))),
        "cost_deepseek_personalize": Decimal(str(round(cost_deepseek_pers, 4))),
        "cost_total": Decimal(str(round(
            cost_apify_google + cost_apify_reactions + cost_apify_profiles + cost_deepseek_icp + cost_deepseek_pers, 4
        ))),
    }


# ===================================================================
# MAIN ORCHESTRATOR
# ===================================================================

async def run_competitor_post_pipeline(
    keywords: str = "ceos",
    days_back: int = 7,
    min_reactions: int = 50,
    allowed_countries: list[str] | None = None,
    heyreach_list_id: int = HEYREACH_LIST_ID,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the full 13-step competitor post pipeline.

    Creates a PipelineRun, runs all steps, creates Prospect records,
    uploads to HeyReach, and sends a Slack summary.

    Surplus leads (ICP-qualified but not yet personalized/uploaded) are stored
    as Prospect records with personalized_message=NULL so they can be
    picked up by future top-up runs without re-scraping.
    """
    from app.database import async_session_factory

    if allowed_countries is None:
        allowed_countries = ["United States", "Canada", "USA", "America"]

    async with async_session_factory() as session:
        # Create PipelineRun
        pipeline_run = PipelineRun(run_type="competitor_post", status="started")
        session.add(pipeline_run)
        await session.commit()
        run_id = pipeline_run.id

        counts: dict[str, int] = {}

        try:
            # Step 1: Google search
            logger.info("[1/13] Searching Google for LinkedIn posts...")
            search_results = await search_google_linkedin_posts(keywords, days_back)
            # Extract organic results
            posts = []
            for r in search_results:
                if "organicResults" in r:
                    org = r["organicResults"]
                    posts.extend(org if isinstance(org, list) else [org])
                else:
                    posts.append(r)
            counts["google_results"] = len(posts)
            pipeline_run.posts_found = len(posts)

            if not posts:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "posts_found": 0}

            # Step 2: Filter by reactions
            logger.info("[2/13] Filtering by reactions...")
            filtered_posts = filter_posts_by_reactions(posts, min_reactions)

            if not filtered_posts:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "posts_found": len(posts), "posts_filtered": 0}

            # Step 3: Scrape engagers
            logger.info("[3/13] Scraping post engagers...")
            post_urls = [p.get("url", p.get("link", "")) for p in filtered_posts if p.get("url") or p.get("link")]
            engagers = await scrape_post_engagers(post_urls)
            counts["posts_scraped"] = len(post_urls)
            pipeline_run.engagers_found = len(engagers)

            if not engagers:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "engagers_found": 0}

            # Step 4: Headline pre-filter
            logger.info("[4/13] Pre-filtering by headline...")
            engagers, _, _, _ = prefilter_engagers_by_headline(engagers)

            # Step 5: Dedup URLs
            logger.info("[5/13] Deduplicating URLs...")
            profile_urls = aggregate_and_deduplicate_urls(engagers)

            # Step 6: DB dedup
            logger.info("[6/13] DB dedup...")
            profile_urls = await filter_already_processed(profile_urls, session)

            if not profile_urls:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "new_urls": 0}

            # Step 7: Scrape profiles
            logger.info(f"[7/13] Scraping {len(profile_urls)} profiles...")
            profiles = await scrape_linkedin_profiles(profile_urls)
            counts["profiles_scraped"] = len(profiles)
            pipeline_run.profiles_scraped = len(profiles)

            # Step 8: Location filter
            logger.info("[8/13] Filtering by location...")
            profiles = filter_by_location(profiles, allowed_countries)
            pipeline_run.location_filtered = len(profiles)

            # Step 9: Completeness filter
            logger.info("[9/13] Filtering incomplete profiles...")
            profiles = filter_complete_profiles(profiles)

            if not profiles:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "complete_profiles": 0}

            # Step 10: ICP qualification
            logger.info(f"[10/13] ICP qualifying {len(profiles)} leads...")
            qualified = await qualify_leads_with_deepseek(profiles)
            counts["icp_checks"] = len(profiles)
            pipeline_run.icp_qualified = len(qualified)

            if not qualified:
                pipeline_run.status = "completed"
                pipeline_run.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"run_id": str(run_id), "status": "completed", "icp_qualified": 0}

            # Step 11: Personalize
            logger.info(f"[11/13] Personalizing {len(qualified)} leads...")
            for lead in qualified:
                lead["personalized_message"] = await generate_personalization_deepseek(lead)
            personalized = [l for l in qualified if l.get("personalized_message")]
            counts["personalizations"] = len(personalized)

            # Step 12: Validate & fix
            logger.info("[12/13] Validating messages...")
            personalized = await validate_and_fix_batch(personalized)

            # Step 13: Upload to HeyReach
            uploaded = 0
            if not dry_run and personalized:
                logger.info(f"[13/13] Uploading {len(personalized)} leads to HeyReach...")
                uploaded = await upload_to_heyreach(personalized, heyreach_list_id)
            else:
                logger.info("[13/13] Dry run — skipping upload")

            pipeline_run.final_leads = uploaded or len(personalized)

            # Create Prospect records for ALL qualified leads (including surplus)
            created = await create_prospect_records(
                qualified, ProspectSource.COMPETITOR_POST, keywords, heyreach_list_id if uploaded else None, session,
            )

            # Update PipelineRun with costs
            costs = _estimate_costs(counts)
            for k, v in costs.items():
                setattr(pipeline_run, k, v)
            pipeline_run.count_google_searches = counts.get("google_results", 0)
            pipeline_run.count_posts_scraped = counts.get("posts_scraped", 0)
            pipeline_run.count_profiles_scraped = counts.get("profiles_scraped", 0)
            pipeline_run.count_icp_checks = counts.get("icp_checks", 0)
            pipeline_run.count_personalizations = counts.get("personalizations", 0)

            pipeline_run.status = "completed"
            pipeline_run.completed_at = datetime.now(timezone.utc)
            duration = (pipeline_run.completed_at - pipeline_run.started_at).total_seconds()
            pipeline_run.duration_seconds = int(duration)
            await session.commit()

            summary = {
                "run_id": str(run_id),
                "status": "completed",
                "posts_found": pipeline_run.posts_found,
                "engagers_found": pipeline_run.engagers_found,
                "profiles_scraped": pipeline_run.profiles_scraped,
                "icp_qualified": pipeline_run.icp_qualified,
                "personalized": len(personalized),
                "uploaded": uploaded,
                "prospects_created": created,
                "cost_total": str(costs["cost_total"]),
                "duration_seconds": pipeline_run.duration_seconds,
            }

            await _send_pipeline_summary("Competitor Post", summary)
            return summary

        except Exception as e:
            logger.error(f"Competitor post pipeline failed: {e}", exc_info=True)
            pipeline_run.status = "failed"
            pipeline_run.error_message = str(e)[:500]
            pipeline_run.completed_at = datetime.now(timezone.utc)
            await session.commit()
            return {"run_id": str(run_id), "status": "failed", "error": str(e)}
