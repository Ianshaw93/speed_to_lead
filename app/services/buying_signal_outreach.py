"""Automated buying signal outreach: query unprocessed prospects, scrape, personalize, upload."""

import asyncio
import logging
import random
from datetime import datetime, timezone

import httpx
from apify_client import ApifyClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Prospect, ProspectSource

logger = logging.getLogger(__name__)

HEYREACH_LIST_ID = 480247
PROFILE_SCRAPER_ACTOR = "dev_fusion~Linkedin-Profile-Scraper"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# Top 5% signal instructions (same as multichannel-outreach/execution/prompts.py)
_TOP5_SIGNAL_INSTRUCTIONS = """Template (word-for-word, do NOT modify):
My system's showing you as top 5% most active on here in terms of b2b founders/decision makers. Other signals like commenting relevant pain points or engaging in relevant posts would be ofc be stronger

Rules:
- This is an EXACT template -- output it word-for-word including "ofc"
- Do NOT rephrase, shorten, or "improve" this line
- This replaces the post reference line when the signal is general activity, not a specific post"""

_BUYING_SIGNAL_DM_TEMPLATE = """You create LinkedIn DMs that reference a buying signal, then offer concrete value.

## TASK
Generate {line_count} lines:
1. **Greeting** -> Hey [FirstName]
2. **Signal reference** -> (see SIGNAL REFERENCE section below)
3. **Niche question** -> You guys target [niche] right?
4. **Value offer** -> (template, word-for-word -- see below)
{location_task_line}

---

# SIGNAL REFERENCE (LINE 2)

{signal_instructions}

---

# NICHE QUESTION (LINE 3)

Template: You guys target [niche] right?

Rules:
- Infer [niche] from their headline, about section, company description, company name, industry, job title
- Use the headline + about section as PRIMARY signals
- [niche] = the TYPE OF CUSTOMER their company serves (not what the company does)
- Think: "Who is this company's ideal customer?"
- Keep it short -- 1-4 words for the niche
- Casual tone

Examples:
- Company is an advertising agency -> "You guys target ecom brands right?"
- Company does IT consulting -> "You guys target mid-market SaaS right?"
- Company does executive search -> "You guys target C-suite hires right?"

---

# VALUE OFFER (LINE 4)

Template (word-for-word, do NOT modify):
Actually spent 30mins looking up 10 prospects showing strong buying signals relevant to tht icp. Makes it WAAY easier speaking to a starving crowd. Lmk that's your icp and I'll send them across

{location_section}

---

# TEMPLATE INTEGRITY LAW

Line 4 is an EXACT template -- word-for-word, including "tht" and "WAAY".
Only `[placeholders]` may be swapped.
No rephrasing. No "fixing" spelling. No adding punctuation.

---

# OUTPUT FORMAT

Always output EXACTLY {line_count} lines ({output_line_labels}).

Take a new paragraph (blank line) between each line.

Only output the line contents - NOT section labels like "Greeting:" or "Signal reference:". The full message will be sent on LinkedIn as is.

DO NOT include long dashes (---) in the output.
{no_location_reminder}

Only return the message - the full reply will be sent on LinkedIn directly.

---

Lead Information:
- First Name: {first_name}
- Company: {company_name}
- Title: {title}
- Industry: {industry}
- Location: {location}
{extra_lead_info}

Generate the complete {line_count}-line LinkedIn DM now. Return ONLY the message (no explanation, no labels, no formatting)."""


def _build_prompt(
    first_name: str,
    company_name: str,
    title: str,
    industry: str,
    location: str,
    skip_location: bool,
    headline: str = "",
    about: str = "",
) -> str:
    """Build the buying signal DM prompt for top5 signal type."""
    profile_lines = []
    if headline:
        profile_lines.append(f"- Headline: {headline}")
    if about:
        profile_lines.append(f"- About: {about[:500]}")

    extra_lead_info = "\n".join(profile_lines)

    if skip_location:
        line_count = 4
        location_task_line = ""
        location_section = ""
        output_line_labels = "Greeting -> Signal reference -> Niche question -> Value offer"
        no_location_reminder = "\nDo NOT include a location hook or any 5th line. End after the value offer."
    else:
        line_count = 5
        location_task_line = "5. **Location Hook** -> (template, word-for-word -- see below)"
        location_section = """---

# LOCATION HOOK TEMPLATE (LINE 5)

Template (word-for-word, only replace [city/region]):
See you're in [city/region]. Just been to Fort Lauderdale in the US - and I mean the airport lol Have so many connections now that I need to visit for real. I'm in Glasgow, Scotland"""
        output_line_labels = "Greeting -> Signal reference -> Niche question -> Value offer -> Location Hook"
        no_location_reminder = ""

    return _BUYING_SIGNAL_DM_TEMPLATE.format(
        first_name=first_name,
        company_name=company_name or "(not available)",
        title=title or "(not available)",
        industry=industry or "(not available)",
        location=location or "(not available)",
        signal_instructions=_TOP5_SIGNAL_INSTRUCTIONS,
        extra_lead_info=extra_lead_info,
        line_count=line_count,
        location_task_line=location_task_line,
        location_section=location_section,
        output_line_labels=output_line_labels,
        no_location_reminder=no_location_reminder,
    )


async def get_unprocessed_buying_signals(session: AsyncSession) -> list[Prospect]:
    """Query prospects where source_type=BUYING_SIGNAL and personalized_message IS NULL."""
    result = await session.execute(
        select(Prospect).where(
            Prospect.source_type == ProspectSource.BUYING_SIGNAL,
            Prospect.personalized_message.is_(None),
        )
    )
    return list(result.scalars().all())


def scrape_profiles_batch(profile_urls: list[str]) -> dict[str, dict]:
    """Scrape LinkedIn profiles via Apify. Returns dict keyed by normalized URL.

    Synchronous — caller should wrap in asyncio.to_thread().
    """
    import time

    if not settings.apify_api_token:
        logger.warning("APIFY_API_TOKEN not set, skipping profile scraping")
        return {}

    if not profile_urls:
        return {}

    logger.info(f"Starting profile scraper for {len(profile_urls)} profiles")

    start_url = f"https://api.apify.com/v2/acts/{PROFILE_SCRAPER_ACTOR}/runs?token={settings.apify_api_token}"
    payload = {"profileUrls": profile_urls}

    try:
        response = httpx.post(start_url, json=payload, timeout=30)
        response.raise_for_status()
        run_data = response.json()["data"]
        run_id = run_data["id"]
        dataset_id = run_data["defaultDatasetId"]
        logger.info(f"Apify run started: {run_id}")
    except Exception as e:
        logger.error(f"Error starting profile scraper: {e}")
        return {}

    # Poll for completion
    time.sleep(120)
    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={settings.apify_api_token}"
    for _ in range(20):  # max 10 min polling
        try:
            resp = httpx.get(status_url, timeout=15)
            resp.raise_for_status()
            status = resp.json()["data"]["status"]
            if status in ("SUCCEEDED", "ABORTED", "FAILED"):
                logger.info(f"Apify scraper finished: {status}")
                break
            logger.info(f"Apify status: {status}, polling...")
            time.sleep(30)
        except Exception as e:
            logger.error(f"Error polling Apify status: {e}")
            break

    # Fetch results
    data_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={settings.apify_api_token}"
    try:
        resp = httpx.get(data_url, headers={"Accept": "application/json"}, timeout=30)
        resp.raise_for_status()
        profiles = resp.json()
    except Exception as e:
        logger.error(f"Error fetching Apify results: {e}")
        return {}

    # Key by normalized URL
    result = {}
    for p in profiles:
        url = p.get("linkedinUrl") or p.get("profileUrl") or ""
        if url:
            key = _normalize_url(url)
            result[key] = p

    logger.info(f"Scraped {len(result)} profiles")
    return result


def _normalize_url(url: str) -> str:
    """Normalize LinkedIn URL for matching."""
    return url.lower().strip().rstrip("/").split("?")[0]


async def generate_message(
    prospect: Prospect,
    profile_data: dict | None,
    skip_location: bool,
) -> str | None:
    """Generate personalized DM via DeepSeek."""
    if not settings.deepseek_api_key:
        logger.error("DEEPSEEK_API_KEY not set")
        return None

    location = prospect.location or ""
    if "," in location:
        location = location.split(",")[0].strip()

    headline = ""
    about = ""
    if profile_data:
        headline = profile_data.get("headline", "")
        about = profile_data.get("about", "")

    prompt = _build_prompt(
        first_name=prospect.first_name or "",
        company_name=prospect.company_name or "",
        title=prospect.job_title or "",
        industry=prospect.company_industry or "",
        location=location,
        skip_location=skip_location,
        headline=headline or prospect.headline or "",
        about=about,
    )

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You are an expert at creating personalized LinkedIn DMs following strict template rules. You write as a founder, not a salesperson."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 400,
                    "temperature": 0.7,
                },
            )
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]["content"].strip()

            # Clean up
            if message.startswith('"') and message.endswith('"'):
                message = message[1:-1]
            message = message.replace("```", "").strip()
            return message

    except Exception as e:
        logger.error(f"Error generating message for {prospect.full_name}: {e}")
        return None


async def upload_to_heyreach(prospects_with_messages: list[dict], list_id: int) -> int:
    """Upload prospects with personalized messages to HeyReach list.

    Args:
        prospects_with_messages: List of dicts with prospect data + personalized_message
        list_id: HeyReach list ID

    Returns:
        Number of leads uploaded
    """
    from app.services.heyreach import get_heyreach_client, HeyReachError

    if not prospects_with_messages:
        return 0

    heyreach = get_heyreach_client()

    leads = []
    for p in prospects_with_messages:
        if not p.get("personalized_message"):
            continue
        leads.append({
            "linkedin_url": p["linkedin_url"],
            "first_name": p.get("first_name", ""),
            "last_name": p.get("last_name", ""),
            "company_name": p.get("company_name", ""),
            "job_title": p.get("job_title", ""),
            "custom_fields": {
                "personalized_message": p["personalized_message"],
            },
        })

    if not leads:
        return 0

    # Upload in chunks of 100
    total = 0
    for i in range(0, len(leads), 100):
        chunk = leads[i:i + 100]
        try:
            result = await heyreach.add_leads_to_list(list_id, chunk)
            added = result.get("addedCount", 0) if isinstance(result, dict) else len(chunk)
            total += added
            logger.info(f"HeyReach upload chunk: {added} added")
        except HeyReachError as e:
            logger.error(f"HeyReach upload failed: {e}")

    return total


async def process_buying_signal_batch() -> dict:
    """Main orchestrator: query -> scrape -> generate -> upload -> update DB -> return summary."""
    from app.database import async_session_factory

    logger.info("Starting buying signal outreach batch")

    async with async_session_factory() as session:
        # 1. Query unprocessed prospects
        prospects = await get_unprocessed_buying_signals(session)
        if not prospects:
            logger.info("No unprocessed buying signal prospects found")
            return {"processed": 0, "messages_generated": 0, "uploaded": 0, "errors": 0}

        logger.info(f"Found {len(prospects)} unprocessed buying signal prospects")

        # 2. Scrape LinkedIn profiles (async wrapper around sync Apify call)
        profile_urls = [p.linkedin_url for p in prospects if p.linkedin_url]
        profile_data = await asyncio.to_thread(scrape_profiles_batch, profile_urls)

        # 3. Apply 50/50 location split
        indices = list(range(len(prospects)))
        random.shuffle(indices)
        half = len(indices) // 2
        skip_location_set = set(indices[half:])

        # 4. Generate personalized messages
        messages_generated = 0
        errors = 0
        prospects_for_upload = []

        for idx, prospect in enumerate(prospects):
            skip_loc = idx in skip_location_set
            normalized = _normalize_url(prospect.linkedin_url)
            pdata = profile_data.get(normalized)

            message = await generate_message(prospect, pdata, skip_loc)
            if message:
                prospect.personalized_message = message
                messages_generated += 1
                prospects_for_upload.append({
                    "linkedin_url": prospect.linkedin_url,
                    "first_name": prospect.first_name,
                    "last_name": prospect.last_name,
                    "company_name": prospect.company_name,
                    "job_title": prospect.job_title,
                    "personalized_message": message,
                })
            else:
                errors += 1

        # 5. Upload to HeyReach
        uploaded = await upload_to_heyreach(prospects_for_upload, HEYREACH_LIST_ID)

        # 6. Update DB with upload timestamps
        now = datetime.now(timezone.utc)
        for prospect in prospects:
            if prospect.personalized_message:
                prospect.heyreach_list_id = HEYREACH_LIST_ID
                prospect.heyreach_uploaded_at = now

        await session.commit()

        summary = {
            "processed": len(prospects),
            "messages_generated": messages_generated,
            "uploaded": uploaded,
            "errors": errors,
        }
        logger.info(f"Buying signal batch complete: {summary}")
        return summary
