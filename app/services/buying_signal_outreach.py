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


_LINKEDIN_5_LINE_DM_TEMPLATE = """You create **5-line LinkedIn DMs** that feel personal and conversational — balancing business relevance with personal connection and strict template wording.

## TASK
Generate 5 lines:
1. **Greeting** → Hey [FirstName]
2. **Profile hook** → [CompanyName] looks interesting
3. **Business related Inquiry** → You guys do [service] right? Do that w [method]? Or what
4. **Authority building Hook** → 2-line authority statement based on industry (see rules below)
5. **Location Hook** → See you're in [city/region]. Just been to Fort Lauderdale in the US - and I mean the airport lol Have so many connections now that I need to visit for real. I'm in Glasgow, Scotland

---

# PROFILE HOOK TEMPLATE (LINE 2)

Template: [CompanyName] looks interesting

Rules:
● Use their current company name (not past companies)
● Always "looks interesting" (not "sounds interesting" or other variations)
● No exclamation marks
● Keep it casual
● Note: Unless their company name is one word shorten it (remove commas, LTD, Inc, Corp, etc):

Examples:
● "Immersion Data Solutions, LTD" → "IDS looks interesting"
● "The NS Marketing Agency" → "NS Marketing looks interesting"
● "Coca Cola LTD" → "Coca Cola looks interesting"
● "Megafluence, Inc." → "MF looks interesting"

---

# BUSINESS INQUIRY TEMPLATE (LINE 3)

Template: You guys do [service] right? Do that w [method]? Or what

Rules:
● Infer [service] from their headline and company description (NOT just title/company name)
● The headline tells you what they do professionally
● The company description tells you what service their company sells
● Infer [method] based on common methods for that service
● Keep it casual and conversational
● Use "w" instead of "with"
  ● CRITICAL: If headline/company description are empty or unavailable:
    - First check the company name itself (e.g., "Dean's Kid Fashion" → "kid fashion")
    - Then check the industry field if available
    - Then use their job title as the service (e.g., "CFO" → "finance leadership")
    - NEVER default to "corporate comms" or "communications strategy" - this is almost always wrong

Examples:
● You guys do paid ads right? Do that w Google + Meta? Or what
● You guys do outbound right? Do that w LinkedIn + email? Or what
● You guys do branding right? Do that w design + positioning? Or what
● You guys do executive search right? Do that w retained + contingency? Or what
● You guys do lead gen right? Do that w LinkedIn + cold email? Or what
● You guys do HR consulting right? Do that w culture audits + talent strategy? Or what

---

# AUTHORITY STATEMENT GENERATION (LINE 4 - 2 LINES)

You MUST follow the exact template, rules, and constraints below. Do not deviate from examples or structure.

Your job is to generate short, punchy authority statements that:
● Sound like a founder talking to another founder
● Contain zero fluff
● Tie everything to business outcomes (revenue, scaling, margins, clients, CAC, downtime, etc.)
● Always follow the 2-line template
● Contain only true statements
● Use simple, natural, conversational language
● Are industry-accurate
● Are 2 lines maximum

## AUTHORITY STATEMENT TEMPLATE (MANDATORY)

**Line 1 — X is Y.**
A simple, universally true industry insight. Examples:
● "Ecom is a tough nut to crack."
● "Strong branding is so powerful."
● "Compliance is a must."
● "Outbound is a tough nut to crack."
● "A streamlined CRM is so valuable."
● "Podcasting is powerful."
● "Analytics is valuable."
● "VA placement is so valuable."
● "Leadership development is so powerful."
● "Executive search is so powerful."


## RULES YOU MUST FOLLOW (NON-NEGOTIABLE)

1. The result must always be EXACTLY 2 lines. Never more, never fewer.

2. No fluff. No generic statements. No teaching tone.
Avoid phrases like:
● "helps businesses…"
● "keeps things running smoothly…"
● "boosts adoption fast…"
● "improves efficiency…"
● "keeps listeners engaged…"
● "help manage leads efficiently…"
These are forbidden.

3. No repeating the same idea twice.
Avoid tautologies such as:
● "Inboxes are crowded. Response rates are low."
● "Hiring is tough. Most candidates are similar."
Only one cause per example.

4. Every term MUST be used accurately.
If referencing: CRM, analytics, demand gen, attribution, compliance, margins, downtime, CAC, outbound, SQL/Sales pipeline, etc.
→ You MUST demonstrate correct real-world understanding.
Never misuse terms.

5. "Underrated" may only be used when the thing is ACTUALLY underrated.
Cybersecurity, VAs, branding, and CRM are NOT underrated.
Examples you MUST respect:
● ✔ "VA placement is so valuable."
● ✔ "Cybersecurity is valuable."
● ❌ "VA placement is underrated."
● ❌ "Cybersecurity is underrated."

6. Every final line MUST connect to Business Outcomes (money / revenue / scaling / clients).
Tie the idea directly to something founders actually care about. Examples (do NOT alter these):
Examples you MUST use as models:
● "So downtime saved alone makes it a no-brainer."
● "Often comes down to having a brand/offer that's truly different."
● "Without proper tracking you're literally leaving revenue on the table."
● "Great way to build trust at scale with your ideal audience."
● "Higher margins and faster scaling for companies that use them right."
● "Nice way to see revenue leaks and double down on what works."
● "Really comes down to precise targeting + personalisation to book clients at a high level."
● "Such a strong lever to pull."

7. Use the Founder Voice. Read it as if you were DM'ing a sophisticated founder. Short, direct, conversational.

8. Everything must be TRUE. If the industry reality is not obvious, you must adjust the statement to something factual.

EXACT EXEMPLARS (DO NOT MODIFY THESE)
Use these as your reference for tone, length, structure, and sharpness.
Podcasting
"Podcasting is powerful
Great way to build trust at scale with your ideal audience."
Ecom
"Ecom is a tough nut to crack
Often comes down to having a brand/offer that's truly different."
CRM
"A streamlined CRM is so valuable
Without proper tracking you're leaving revenue on the table."
Outbound
"Outbound is a tough nut to crack
Really comes down to precise targeting/personalisation to book clients at a high level."
Analytics
"Analytics are so valuable
Gotta act on revenue leaks and double down on what works."
VA Placement
"VA placement is so valuable
Higher margins and faster scaling for companies that use them right"

BEFORE → AFTER EXAMPLES
(EXACT TEXT—DO NOT MODIFY)
Use these to understand how to transform bad/fluffy statements into good ones.
❌ BEFORE
"Podcasting is powerful.
Attention is hard to get. Clean production keeps listeners coming back."
✔ AFTER
"Podcasting is powerful.
Great way to build trust at scale with your ideal audience."
❌ BEFORE
"CRM is so powerful.
Helps you manage your leads efficiently so you don't miss out on potential sales."
✔ AFTER
"A streamlined CRM is so valuable.
Without proper tracking you're leaving revenue on the table."
❌ BEFORE
"Outbound is a tough nut to crack.
Response rates are low, making it hard to reach decision-makers."
✔ AFTER
"Outbound is a tough nut to crack.
Really comes down to precise targeting and personalized messaging to book clients at a high level."
❌ BEFORE
"VA placement is underrated.
It connects businesses with skilled remote assistants."
✔ AFTER
"VA placement is so valuable.
Higher margins and faster scaling for companies that use them right."

---

# LOCATION HOOK TEMPLATE (LINE 5)

Template (word-for-word, only replace [city/region]):
See you're in [city/region]. Just been to Fort Lauderdale in the US - and I mean the airport lol Have so many connections now that I need to visit for real. I'm in Glasgow, Scotland

---

# TEMPLATE INTEGRITY LAW

Templates must be word-for-word.
Only `[placeholders]` may be swapped.
No rephrasing.

---

# OUTPUT FORMAT

Always output 5 lines (Greeting → Profile hook → Business Inquiry → Authority Statement → Location Hook).

Take a new paragraph (blank line) between each line.

Only output the line contents - NOT section labels like "Greeting:" or "Authority Building Hook:". The full message will be sent on LinkedIn as is.

DO NOT include long dashes (---) in the output.

Only return the message - the full reply will be sent on LinkedIn directly.

---

Lead Information:
- First Name: {first_name}
- Company: {company_name}
- Title: {title}
- Headline: {headline}
- Company Description: {company_description}
- Location: {location}

Generate the complete 5-line LinkedIn DM now. Return ONLY the message (no explanation, no labels, no formatting)."""


def _build_5line_prompt(
    first_name: str,
    company_name: str,
    title: str,
    headline: str = "",
    company_description: str = "",
    location: str = "",
) -> str:
    """Build the standard 5-line DM prompt (profile hook variant)."""
    return _LINKEDIN_5_LINE_DM_TEMPLATE.format(
        first_name=first_name,
        company_name=company_name or "(not available)",
        title=title or "(not available)",
        headline=headline or "(not available)",
        company_description=company_description or "(not available)",
        location=location or "(not available)",
    )


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


async def generate_message_5line(
    prospect: Prospect,
    profile_data: dict | None,
) -> str | None:
    """Generate personalized DM via DeepSeek using the standard 5-line template."""
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

    prompt = _build_5line_prompt(
        first_name=prospect.first_name or "",
        company_name=prospect.company_name or "",
        title=prospect.job_title or "",
        headline=headline or prospect.headline or "",
        company_description=about,
        location=location,
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
        logger.error(f"Error generating 5-line message for {prospect.full_name}: {e}")
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

        # 3. A/B split: 50% standard 5-line DM, 50% buying signal DM
        indices = list(range(len(prospects)))
        random.shuffle(indices)
        half = len(indices) // 2
        standard_5line_set = set(indices[:half])
        signal_set = set(indices[half:])

        # Within signal group, do 50/50 location split (existing behavior)
        signal_indices = list(signal_set)
        random.shuffle(signal_indices)
        signal_half = len(signal_indices) // 2
        skip_location_set = set(signal_indices[signal_half:])

        # 4. Generate personalized messages
        messages_generated = 0
        errors = 0
        variant_5line = 0
        variant_signal = 0
        prospects_for_upload = []

        for idx, prospect in enumerate(prospects):
            normalized = _normalize_url(prospect.linkedin_url)
            pdata = profile_data.get(normalized)

            if idx in standard_5line_set:
                variant = "5line"
                message = await generate_message_5line(prospect, pdata)
            else:
                variant = "signal"
                skip_loc = idx in skip_location_set
                message = await generate_message(prospect, pdata, skip_loc)

            logger.info(
                f"Prospect {prospect.full_name} -> variant={variant}"
            )

            if message:
                if variant == "5line":
                    variant_5line += 1
                else:
                    variant_signal += 1
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
            "variant_5line": variant_5line,
            "variant_signal": variant_signal,
        }
        logger.info(f"Buying signal batch complete: {summary}")
        return summary
