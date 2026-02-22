"""Trend Scout: async pipeline for discovering trending ICP-relevant topics.

Three-phase pipeline:
1. Parallel Perplexity Sonar searches (httpx.AsyncClient)
2. Claude ICP scoring & deduplication (anthropic.AsyncAnthropic)
3. Save to contentCreator's DB via sync session (content_db module)

Entry point: run_trend_scout_task() — called by scheduler or manual trigger.
"""

import asyncio
import json
import logging
import uuid

import anthropic
import httpx

from app.config import settings
from app.services.content_db import save_trending_topic

logger = logging.getLogger(__name__)

PERPLEXITY_BASE_URL = "https://api.perplexity.ai"

# Pre-built ICP-relevant search queries
SEARCH_QUERIES = [
    {
        "query": (
            "What are B2B founders, coaches, and consultants discussing on "
            "Reddit this week? Pain points, wins, and hot debates"
        ),
        "platform": "reddit",
    },
    {
        "query": (
            "Trending LinkedIn discussions among founders, coaches, and "
            "consultants about scaling, personal branding, and client acquisition"
        ),
        "platform": "linkedin",
    },
    {
        "query": (
            "Hot takes on AI for business, AI automation for coaches and "
            "consultants on social media this week"
        ),
        "platform": "twitter",
    },
    {
        "query": (
            "Top pain points and challenges entrepreneurs and consultants "
            "are sharing on Reddit right now"
        ),
        "platform": "reddit",
    },
    {
        "query": (
            "Content marketing and personal branding trends for B2B service "
            "providers and coaches in 2025-2026"
        ),
        "platform": "web",
    },
]


# ---------------------------------------------------------------------------
# Phase 1: Perplexity search
# ---------------------------------------------------------------------------


async def _search_perplexity(
    query: str,
    platform: str,
    client: httpx.AsyncClient,
) -> dict:
    """Run a single Perplexity Sonar search.

    Args:
        query: Search query text.
        platform: Label (reddit/linkedin/twitter/web).
        client: Shared httpx async client.

    Returns:
        Dict with content, citations, query, platform keys.
    """
    headers = {
        "Authorization": f"Bearer {settings.perplexity_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "sonar",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a trend research assistant. Find trending topics, "
                    "discussions, and pain points relevant to B2B founders, coaches, "
                    "and consultants. Focus on actionable, specific trends — not "
                    "generic advice."
                ),
            },
            {"role": "user", "content": query},
        ],
    }

    resp = await client.post(
        f"{PERPLEXITY_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "content": data["choices"][0]["message"]["content"],
        "citations": data.get("citations", []),
        "query": query,
        "platform": platform,
    }


async def _run_all_searches(
    queries: list[dict] | None = None,
) -> list[dict]:
    """Run all Perplexity searches concurrently (max 3 at a time).

    Args:
        queries: Optional custom queries list. Defaults to SEARCH_QUERIES.

    Returns:
        List of search result dicts.
    """
    queries = queries or SEARCH_QUERIES
    results: list[dict] = []
    sem = asyncio.Semaphore(3)

    async def _bounded_search(q: dict, client: httpx.AsyncClient) -> dict:
        async with sem:
            return await _search_perplexity(q["query"], q["platform"], client)

    async with httpx.AsyncClient() as client:
        tasks = [_bounded_search(q, client) for q in queries]
        settled = await asyncio.gather(*tasks, return_exceptions=True)

    for i, item in enumerate(settled):
        if isinstance(item, Exception):
            q = queries[i]
            logger.warning(f"Search failed for '{q['query'][:50]}...': {item}")
            results.append({
                "content": "",
                "citations": [],
                "query": q["query"],
                "platform": q["platform"],
                "error": str(item),
            })
        else:
            results.append(item)

    return results


# ---------------------------------------------------------------------------
# Phase 2: Claude ICP scoring
# ---------------------------------------------------------------------------


async def _score_and_extract_topics(search_results: list[dict]) -> list[dict]:
    """Use Claude to deduplicate, score for ICP relevance, and extract angles.

    Args:
        search_results: Output from _run_all_searches().

    Returns:
        List of scored topic dicts.
    """
    combined_text = ""
    for r in search_results:
        if r.get("error"):
            continue
        combined_text += f"\n\n--- Source: {r['platform']} (Query: {r['query']}) ---\n"
        combined_text += r["content"]
        if r.get("citations"):
            combined_text += "\nURLs: " + ", ".join(r["citations"])

    if not combined_text.strip():
        return []

    prompt = f"""Analyze these search results and extract distinct trending topics relevant to our ICP: B2B founders, coaches, and consultants who sell high-ticket services ($5k-$50k+).

SEARCH RESULTS:
{combined_text}

For each unique topic, provide:
1. topic: A concise topic title (max 10 words)
2. summary: 2-3 sentence summary of why this is trending
3. source_urls: Any relevant URLs from the citations
4. relevance_score: 1-10 score for ICP relevance (10 = perfectly relevant to B2B founders/coaches/consultants)
5. content_angles: 2-3 specific content angles Ian could use (e.g., "Share your contrarian take on X", "Story about how you solved Y for a client")
6. source_platform: Primary platform where this was found (reddit/twitter/linkedin/web)

Rules:
- Deduplicate similar topics
- Filter OUT anything below 5/10 relevance
- Focus on topics that would make good LinkedIn content
- Prefer specific, timely topics over generic evergreen advice

Return as JSON array:
[{{"topic": "...", "summary": "...", "source_urls": [...], "relevance_score": N, "content_angles": [...], "source_platform": "..."}}]

Return ONLY the JSON array, no other text."""

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()

    # Handle markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        topics = json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse Claude response as JSON: {text[:200]}...")
        return []

    if not isinstance(topics, list):
        return []

    return topics


# ---------------------------------------------------------------------------
# Phase 3: Save + notify
# ---------------------------------------------------------------------------


async def run_trend_scout_task(
    custom_queries: list[dict] | None = None,
) -> dict:
    """Main entry point: search, score, save to content DB, send Slack report.

    Called by the scheduler (Saturday 7am) or manual trigger endpoint.

    Args:
        custom_queries: Optional custom search queries.

    Returns:
        Summary dict with batch_id, topics_found, topics_saved, topics.
    """
    from app.services.slack import get_slack_bot

    batch_id = str(uuid.uuid4())[:8]
    logger.info(f"Trend scout starting (batch={batch_id})")

    # Phase 1: Parallel Perplexity searches
    search_results = await _run_all_searches(custom_queries)
    successful = [r for r in search_results if not r.get("error")]
    logger.info(f"Phase 1 complete: {len(successful)}/{len(search_results)} searches succeeded")

    if not successful:
        result = {"batch_id": batch_id, "topics_found": 0, "topics_saved": 0, "topics": []}
        try:
            bot = get_slack_bot()
            await bot.send_trend_scout_report(result)
        except Exception as e:
            logger.error(f"Failed to send empty Slack report: {e}")
        return result

    # Phase 2: Claude ICP scoring
    scored_topics = await _score_and_extract_topics(search_results)
    logger.info(f"Phase 2 complete: {len(scored_topics)} topics extracted")

    # Phase 3: Save to content DB (sync, run in executor to avoid blocking)
    saved: list[dict] = []
    loop = asyncio.get_event_loop()
    for t in scored_topics:
        search_query = None
        for r in search_results:
            if r["platform"] == t.get("source_platform"):
                search_query = r["query"]
                break

        try:
            topic_dict = await loop.run_in_executor(
                None,
                lambda t=t, sq=search_query: save_trending_topic(
                    topic=t["topic"],
                    summary=t.get("summary"),
                    source_urls=t.get("source_urls", []),
                    relevance_score=t.get("relevance_score"),
                    content_angles=t.get("content_angles", []),
                    search_query=sq,
                    batch_id=batch_id,
                    source_platform=t.get("source_platform"),
                ),
            )
            saved.append(topic_dict)
        except Exception as e:
            logger.error(f"Failed to save topic '{t.get('topic')}': {e}")

    logger.info(f"Phase 3 complete: {len(saved)} topics saved (batch={batch_id})")

    result = {
        "batch_id": batch_id,
        "topics_found": len(scored_topics),
        "topics_saved": len(saved),
        "topics": saved,
    }

    # Send Slack notification
    try:
        bot = get_slack_bot()
        await bot.send_trend_scout_report(result)
    except Exception as e:
        logger.error(f"Failed to send Slack report: {e}")

    return result
