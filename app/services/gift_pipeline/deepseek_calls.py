"""Async DeepSeek LLM calls for the gift leads pipeline.

Uses AsyncOpenAI (same pattern as app/services/deepseek.py).
"""

import json
import logging
from datetime import datetime, timedelta

from openai import AsyncOpenAI

from app.config import settings
from app.prompts.gift_leads import (
    get_gift_search_query_prompt,
    get_gift_signal_note_prompt,
    get_prospect_research_prompt,
)
from app.services.gift_pipeline.cost_tracker import CostTracker
from app.services.gift_pipeline.filters import normalize_linkedin_url

logger = logging.getLogger(__name__)


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    return clean.strip()


# ---------------------------------------------------------------------------
# Keyword derivation from ICP text
# ---------------------------------------------------------------------------


async def derive_search_phrases(icp_text: str, cost_tracker: CostTracker) -> list[str]:
    """Use DeepSeek to extract multi-word search phrases from ICP text.

    Returns 3-5 short phrases suitable for DB headline/title matching.
    Falls back to simple word-splitting on error.
    """
    if not icp_text:
        return []

    try:
        client = _get_client()
        completion = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "You extract search phrases from ICP descriptions. Always respond with valid JSON.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Extract 3-5 short multi-word search phrases from this ICP description "
                        f"that would match LinkedIn job titles or headlines.\n\n"
                        f"ICP: {icp_text}\n\n"
                        f"Return JSON: {{\"phrases\": [\"phrase 1\", \"phrase 2\", ...]}}\n\n"
                        f"Rules:\n"
                        f"- Each phrase should be 2-3 words\n"
                        f"- Phrases should be specific enough to filter relevant people\n"
                        f"- Include role/title variations and industry terms"
                    ),
                },
            ],
            max_tokens=200,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        cost_tracker.add_icp_check(1)

        content = completion.choices[0].message.content
        result = json.loads(_strip_code_fences(content))
        phrases = result.get("phrases", [])

        if isinstance(phrases, list) and phrases:
            return [p.strip() for p in phrases if isinstance(p, str) and p.strip()]

    except Exception as e:
        logger.warning(f"DeepSeek derive_search_phrases error: {e}")

    # Fallback: simple word splitting (existing behavior)
    return _fallback_derive_phrases(icp_text)


def _fallback_derive_phrases(icp_text: str) -> list[str]:
    """Fallback phrase derivation using simple splitting on delimiters."""
    import re

    # Split on commas, slashes, and common conjunctions to preserve multi-word groups
    parts = re.split(r'[,/]+', icp_text)
    phrases = []
    for part in parts:
        cleaned = part.strip().strip("&").strip()
        # Remove very short fragments
        words = cleaned.split()
        if len(words) >= 2:
            phrases.append(cleaned.lower())
        elif len(words) == 1 and len(cleaned) > 3:
            phrases.append(cleaned.lower())
    return phrases[:5] if phrases else []


# ---------------------------------------------------------------------------
# Step 2: Research prospect's business
# ---------------------------------------------------------------------------

async def research_prospect_business(
    profile: dict,
    cost_tracker: CostTracker,
    user_icp: str | None = None,
    user_pain_points: str | None = None,
) -> dict:
    """Research prospect's business to derive ICP, pain points, buying signals."""
    name = profile.get("fullName", profile.get("firstName", "Unknown"))
    headline = profile.get("headline", "")
    about = profile.get("about", "")
    company = profile.get("companyName", "")
    industry = profile.get("companyIndustry", "")

    experiences = profile.get("experiences", [])
    if experiences:
        exp_str = "; ".join(
            f"{e.get('title', '')} at {e.get('companyName', e.get('company', ''))} ({e.get('totalDuration', '')})"
            for e in experiences[:5]
        )
    else:
        exp_str = profile.get("jobTitle", "")

    prompt = get_prospect_research_prompt(
        name=name, headline=headline, about=about,
        company=company, industry=industry, experiences=exp_str,
        user_icp=user_icp, user_pain_points=user_pain_points,
    )

    try:
        client = _get_client()
        completion = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a B2B sales intelligence analyst. Always respond with valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        cost_tracker.add_icp_check(1)

        content = completion.choices[0].message.content
        result = json.loads(_strip_code_fences(content))

        # Ensure required fields
        for field in ["icp_description", "target_titles", "pain_points", "buying_signals"]:
            if field not in result:
                result[field] = []
        for field in ["search_angles", "target_industries", "target_verticals", "buyer_intent_phrases"]:
            if field not in result:
                result[field] = []

        return result

    except Exception as e:
        logger.warning(f"DeepSeek research error: {e}")
        return _fallback_research(profile, user_icp, user_pain_points)


def _fallback_research(
    profile: dict,
    user_icp: str | None = None,
    user_pain_points: str | None = None,
) -> dict:
    """Fallback research when DeepSeek is unavailable."""
    industry = profile.get("companyIndustry", "")
    return {
        "icp_description": user_icp or f"Decision-makers in {industry or 'B2B services'}",
        "target_titles": ["CEO", "Founder", "Managing Director", "VP"],
        "target_industries": [industry] if industry else ["SaaS", "Agency", "Consulting"],
        "target_verticals": [],
        "pain_points": (
            user_pain_points.split(",") if user_pain_points
            else ["lead generation", "outbound sales", "scaling"]
        ),
        "buying_signals": ["discussing growth challenges", "hiring for sales roles"],
        "buyer_intent_phrases": [],
        "search_angles": ["pain points", "hiring", "industry trends"],
    }


# ---------------------------------------------------------------------------
# Step 3: Generate search queries
# ---------------------------------------------------------------------------

async def generate_search_queries(
    research: dict,
    cost_tracker: CostTracker,
    days_back: int = 14,
    prospect_profile: dict | None = None,
) -> list[str]:
    """Generate 9 Google search queries from ICP research."""
    prompt = get_gift_search_query_prompt(
        icp_description=research.get("icp_description", ""),
        pain_points=research.get("pain_points", []),
        buying_signals=research.get("buying_signals", []),
        target_verticals=research.get("target_verticals"),
        prospect_name=prospect_profile.get("fullName") if prospect_profile else None,
        prospect_headline=prospect_profile.get("headline") if prospect_profile else None,
        prospect_company=prospect_profile.get("companyName") if prospect_profile else None,
    )

    date_cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    try:
        client = _get_client()
        completion = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You generate Google search queries. Always respond with valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.5,
            response_format={"type": "json_object"},
        )
        cost_tracker.add_icp_check(1)

        content = completion.choices[0].message.content
        result = json.loads(_strip_code_fences(content))

        if isinstance(result, list):
            queries = result
        elif isinstance(result, dict) and "queries" in result:
            queries = result["queries"]
        else:
            queries = next((v for v in result.values() if isinstance(v, list)), [])

        # Wrap each query with site: prefix and after: date
        validated = []
        for q in queries:
            if not isinstance(q, str):
                continue
            q = q.strip()
            if "site:linkedin.com/posts" not in q:
                q = f'site:linkedin.com/posts {q}'
            if "after:" not in q:
                q = f'{q} after:{date_cutoff}'
            validated.append(q)

        logger.info(f"Generated {len(validated)} search queries")
        return validated

    except Exception as e:
        logger.warning(f"DeepSeek query generation error: {e}")
        return _fallback_queries(research, days_back)


def _fallback_queries(research: dict, days_back: int) -> list[str]:
    """Fallback search queries when DeepSeek is unavailable."""
    date_cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    pain_points = research.get("pain_points", ["lead generation", "outbound sales"])
    target_verticals = research.get("target_verticals", [])

    queries = []
    if target_verticals:
        for vertical in target_verticals[:3]:
            queries.append(f'site:linkedin.com/posts "{vertical}" after:{date_cutoff}')
    else:
        for pain in pain_points[:3]:
            queries.append(f'site:linkedin.com/posts "{pain}" after:{date_cutoff}')

    if not queries:
        queries.append(f'site:linkedin.com/posts "B2B" "growth" after:{date_cutoff}')
    return queries


# ---------------------------------------------------------------------------
# Step 10: ICP qualification
# ---------------------------------------------------------------------------

async def check_icp_match(
    lead: dict, cost_tracker: CostTracker, icp_criteria: str | None = None,
    strict: bool = False,
) -> dict:
    """Check if a lead matches ICP using DeepSeek.

    Args:
        lead: Lead dict with profile info.
        cost_tracker: Cost tracker instance.
        icp_criteria: ICP description to match against.
        strict: If True, uses strict matching for a prospect's ICP (gift leads).
                If False, uses loose matching for Scaling Smiths' own ICP (default).
    """
    headline = lead.get('headline', 'N/A')
    company_desc = (lead.get('company_description') or lead.get('about') or '')[:300]

    lead_summary = f"""Lead: {lead.get('fullName', lead.get('full_name', 'Unknown'))}
Title: {lead.get('jobTitle', lead.get('job_title', lead.get('title', 'Unknown')))}
Headline: {headline}
Company: {lead.get('companyName', lead.get('company', lead.get('company_name', 'Unknown')))}
Company Description: {company_desc or 'N/A'}
Location: {lead.get('addressWithCountry', lead.get('location', 'Unknown'))}
Industry: {lead.get('companyIndustry', lead.get('industry', 'N/A'))}"""

    if strict and icp_criteria:
        system_prompt = "You are a strict lead qualification expert. You reject leads that don't clearly fit the target niche. Always respond with valid JSON."
        user_prompt = f"""We're looking for B2B decision-makers (founders, CEOs, MDs, etc.) whose business operates in a SPECIFIC industry/niche.

Target niche: "{icp_criteria}"

Lead Information:
{lead_summary}

The key question: Is this person a decision-maker whose BUSINESS/PRODUCT is in the "{icp_criteria}" space?

Rules:
- Assume they ARE a decision-maker (founder/CEO/etc.) — that's already filtered.
- The deciding factor is their INDUSTRY/NICHE. What does their company actually sell or do?
- A "tech founder" means their company builds/sells technology products (SaaS, software, hardware, dev tools, AI, etc.)
- Marketing agencies, coaches, consultants, content creators, LinkedIn experts, recruiters etc. are NOT "{icp_criteria}" unless their product is literally in that space.
- Be strict. When in doubt, reject. 5 perfect matches > 15 loose ones.

Respond in JSON:
{{
  "match": true/false,
  "confidence": "high" | "medium" | "low",
  "reason": "Brief explanation (1 sentence)"
}}"""
    else:
        system_prompt = "You are an expert at evaluating sales leads against ICP criteria. Always respond with valid JSON."
        user_prompt = f"""You are verifying if a LinkedIn lead matches the Ideal Customer Profile (ICP).

ICP Criteria: {icp_criteria or 'B2B decision-makers in high-ticket service industries'}

Lead Information:
{lead_summary}

Task: Determine if this lead matches the ICP.

Respond in JSON format:
{{
  "match": true/false,
  "confidence": "high" | "medium" | "low",
  "reason": "Brief explanation (1 sentence)"
}}"""

    try:
        client = _get_client()
        completion = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=150,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        cost_tracker.add_icp_check(1)

        content = completion.choices[0].message.content
        return json.loads(_strip_code_fences(content))

    except Exception as e:
        logger.warning(f"DeepSeek ICP error: {e}")
        return {"match": True, "confidence": "error", "reason": str(e)}


async def qualify_leads_with_deepseek(
    leads: list[dict], cost_tracker: CostTracker, icp_criteria: str | None = None,
) -> list[dict]:
    """Qualify leads using DeepSeek. Returns only those that match ICP."""
    qualified = []
    for lead in leads:
        name = lead.get("fullName") or lead.get("full_name", "Unknown")
        icp_result = await check_icp_match(
            lead, cost_tracker, icp_criteria, strict=True,
        )

        is_match = icp_result.get("match", False)
        lead["icp_match"] = is_match
        lead["icp_confidence"] = icp_result.get("confidence", "unknown")
        lead["icp_reason"] = icp_result.get("reason", "")

        logger.info(
            f"ICP check [{name}]: match={is_match}, "
            f"confidence={icp_result.get('confidence', '?')}, "
            f"reason={icp_result.get('reason', 'none')}"
        )

        if is_match:
            qualified.append(lead)

    logger.info(
        f"ICP qualification: {len(leads)} → {len(qualified)} leads "
        f"(rejected {len(leads) - len(qualified)})"
    )
    return qualified


# ---------------------------------------------------------------------------
# Step 11: Signal notes
# ---------------------------------------------------------------------------

async def generate_signal_notes(
    leads: list[dict], icp_description: str, cost_tracker: CostTracker,
) -> list[dict]:
    """Generate 1-line signal notes per lead."""
    if not leads:
        return leads

    batch_size = 10
    all_notes: dict[str, str] = {}

    for i in range(0, len(leads), batch_size):
        batch = leads[i:i + batch_size]
        prompt = get_gift_signal_note_prompt(icp_description, batch)

        try:
            client = _get_client()
            completion = await client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "You generate concise signal notes. Always respond with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            cost_tracker.add_personalization(len(batch))

            content = completion.choices[0].message.content
            result = json.loads(_strip_code_fences(content))

            notes_list = result if isinstance(result, list) else (
                result.get("notes") or result.get("leads") or
                next((v for v in result.values() if isinstance(v, list)), [])
            )

            for note in notes_list:
                url = note.get("linkedin_url", "")
                signal = note.get("signal_note", "")
                if url and signal:
                    all_notes[normalize_linkedin_url(url)] = signal[:100]

        except Exception as e:
            logger.warning(f"Signal note batch error: {e}")

    # Apply notes to leads
    for lead in leads:
        url = lead.get("linkedinUrl") or lead.get("linkedin_url", "")
        key = normalize_linkedin_url(url)
        lead["signal_note"] = all_notes.get(key, _fallback_signal_note(lead))

    return leads


def _fallback_signal_note(lead: dict) -> str:
    """Generate a fallback signal note."""
    eng_type = lead.get("engagement_type", "LIKE")
    action = "Liked" if eng_type == "LIKE" else "Engaged with"
    title = lead.get("jobTitle") or lead.get("title") or "professional"
    company = lead.get("companyName") or lead.get("company") or ""
    return f"{action} relevant post - {title} at {company}"[:100]
