"""Prompt templates for the gift leads pipeline."""

import json

PROSPECT_RESEARCH_PROMPT = """You are a B2B sales intelligence analyst. Analyze this LinkedIn profile to derive who their Ideal Customer Profile (ICP) would be, what pain points their prospects likely have, and what buying signals to look for.

## Profile Data
- Name: {name}
- Headline: {headline}
- About: {about}
- Company: {company}
- Industry: {industry}
- Experiences: {experiences}

## User-Provided Context (override if provided)
- ICP: {user_icp}
- Pain Points: {user_pain_points}

## Task
Analyze this person's business and output JSON with these fields:

{{
  "icp_description": "1-2 sentence description of who their ideal customers are",
  "target_titles": ["CEO", "Founder", ...],
  "target_industries": ["SaaS", "Agency", ...],
  "target_verticals": ["manufacturing", "HVAC", "construction", "industrial services"],
  "pain_points": ["scaling outbound", "lead generation", ...],
  "buying_signals": ["hiring SDRs", "discussing outbound challenges", ...],
  "buyer_intent_phrases": ["thinking about selling my business", "how to prepare for exit", "is now a good time to sell"],
  "search_angles": ["pain point discussions", "hiring signals", "industry trends"]
}}

Rules:
- If user provided ICP/pain points, use those as primary and supplement with profile analysis
- If no user context, derive everything from the profile
- Keep target_titles to 3-6 most relevant titles
- Keep pain_points to 3-5 specific, actionable pain points
- buying_signals should be things people would post or engage with on LinkedIn
- target_verticals: specific sub-industries or niches within their market (3-6). Think: what specific types of businesses does this person serve? E.g. an M&A advisor might serve manufacturing, HVAC, construction, industrial services.
- buyer_intent_phrases: natural-language phrases their ideal customers would post about or engage with on LinkedIn (3-5). Think: what is the prospect's client THINKING about? Not industry jargon — write from the buyer's perspective. E.g. "thinking about selling my business", "exit planning for business owners".
- Be specific to their industry, not generic

Respond ONLY with valid JSON."""


def get_prospect_research_prompt(
    name: str,
    headline: str,
    about: str,
    company: str,
    industry: str,
    experiences: str,
    user_icp: str | None = None,
    user_pain_points: str | None = None,
) -> str:
    return PROSPECT_RESEARCH_PROMPT.format(
        name=name or "Unknown",
        headline=headline or "(not available)",
        about=(about or "(not available)")[:500],
        company=company or "(not available)",
        industry=industry or "(not available)",
        experiences=experiences or "(not available)",
        user_icp=user_icp or "(not provided — derive from profile)",
        user_pain_points=user_pain_points or "(not provided — derive from profile)",
    )


GIFT_SEARCH_QUERY_PROMPT = """You are an expert at reverse-engineering LinkedIn post search queries for leadgen from any ICP profile.

Given a prospect's LinkedIn profile + their offer, generate **exactly 9 ultra-concise (2-3 word) search queries** across **3 angles** that will surface posts their ICP actually engages with.

## Prospect Profile
- Name: {prospect_name}
- Headline: {prospect_headline}
- Company: {prospect_company}
- ICP: {icp_description}

## Research Context
- Pain Points: {pain_points}
- Target Verticals: {target_verticals}
- Buying Signals: {buying_signals}

## Rules for queries
- 2-3 words ONLY. Never 4+ words. Shorter = better results.
- Use the ICP's insider terms/abbreviations (e.g. "ND" for naturopathic doctor, "M&A" for mergers & acquisitions)
- No complex booleans/quotes unless natural phrase
- Must return real LinkedIn results — think about what the ICP actually types/searches
- Focus where ICP comments/likes (pain, tools, authority)
- Do NOT include `site:linkedin.com/posts` or `after:` — those are added automatically

**Angle 1 — Founder Pain (1-3):**
Universal struggles their offer solves — use ICP niche + core pain word

**Angle 2 — Vertical-Specific (4-7):**
[ICP-NICHE] sub-types + their service hook — one vertical per query

**Angle 3 — Advisor/Thought-Leader Bait (8-9):**
Posts from [INDUSTRY] influencers/advisors that attract ICP

## Gold Standard Example 1

Prospect: Brody Zastrow — M&A advisor helping industrial business owners sell their companies.
ICP: Industrial business owners considering selling.

**Angle 1 — Founder Pain (1-3):**
1. `selling your business`
2. `exit planning business`
3. `prepare business sale`

**Angle 2 — Vertical-Specific (4-7):**
4. `sell manufacturing business`
5. `sell HVAC business`
6. `sell industrial company`
7. `sell construction company`

**Angle 3 — Advisor/Thought-Leader Bait (8-9):**
8. `M&A alive well`
9. `M&A mythbuster myths`

## Gold Standard Example 2

Prospect: Patrick Hennessy — Newsletter ghostwriter for naturopath founders.
ICP: Naturopath founders.

**Angle 1 — Founder Pain (1-3):**
1. `naturopath newsletter`
2. `ND patient retention`
3. `naturopath content`

**Angle 2 — Vertical-Specific (4-7):**
4. `functional medicine email`
5. `holistic health newsletter`
6. `naturopathic doctor content`
7. `integrative medicine patients`

**Angle 3 — Advisor/Thought-Leader Bait (8-9):**
8. `wellness authority naturopath`
9. `health practitioner newsletter`

Why these work: "naturopath newsletter" > "newsletter content struggle" because NDs search this exact phrase. "ND patient retention" > "time for marketing" because "ND" is their insider term and ties to the prospect's pitch. No 4-word phrases — LinkedIn search works best with 2-3 word combos the ICP actually uses.

## Output Format
Return valid JSON:
{{"queries": ["query one", "query two", ..., "query nine"]}}

Exactly 9 queries. 2-3 words each. No site: prefix. No after: suffix. Just the core search terms.

Respond ONLY with valid JSON."""


def get_gift_search_query_prompt(
    icp_description: str,
    pain_points: list[str] | str,
    buying_signals: list[str] | str,
    target_verticals: list[str] | str | None = None,
    prospect_name: str | None = None,
    prospect_headline: str | None = None,
    prospect_company: str | None = None,
) -> str:
    if target_verticals and isinstance(target_verticals, list):
        verticals_str = ", ".join(target_verticals)
    else:
        verticals_str = target_verticals or "(derive from ICP)"

    return GIFT_SEARCH_QUERY_PROMPT.format(
        prospect_name=prospect_name or "Unknown",
        prospect_headline=prospect_headline or "(not available)",
        prospect_company=prospect_company or "(not available)",
        icp_description=icp_description,
        pain_points=", ".join(pain_points) if isinstance(pain_points, list) else pain_points,
        buying_signals=", ".join(buying_signals) if isinstance(buying_signals, list) else buying_signals,
        target_verticals=verticals_str,
    )


GIFT_SIGNAL_NOTE_PROMPT = """You generate concise signal notes explaining WHY a lead is relevant to a prospect's ICP.

## Prospect's ICP
{icp_description}

## Leads to Annotate
{leads_json}

## Task
For each lead, generate a 1-line signal note (max 100 characters) that explains:
- What engagement they showed (liked/commented on what topic)
- Why that makes them relevant to the prospect's ICP

## Output Format
Return a JSON array with one object per lead:
[
  {{"linkedin_url": "...", "signal_note": "Commented on post about scaling SDR teams — likely evaluating outbound tools"}},
  ...
]

Rules:
- Max 100 characters per signal_note
- Reference the engagement type and topic when available
- Connect to the prospect's ICP/pain points
- Use natural language, not marketing jargon
- Start with the engagement action: "Liked post about...", "Commented on...", "Engaged with..."

Respond ONLY with valid JSON."""


def get_gift_signal_note_prompt(icp_description: str, leads: list[dict]) -> str:
    leads_summary = []
    for lead in leads:
        leads_summary.append({
            "linkedin_url": lead.get("linkedin_url") or lead.get("linkedinUrl", ""),
            "name": lead.get("name") or lead.get("fullName", "Unknown"),
            "title": lead.get("title") or lead.get("jobTitle", ""),
            "company": lead.get("company") or lead.get("companyName", ""),
            "engagement_type": lead.get("engagement_type", "LIKE"),
            "source_post_url": lead.get("source_post_url", ""),
        })
    return GIFT_SIGNAL_NOTE_PROMPT.format(
        icp_description=icp_description,
        leads_json=json.dumps(leads_summary, indent=2),
    )
