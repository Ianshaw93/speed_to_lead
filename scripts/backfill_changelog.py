#!/usr/bin/env python3
"""Backfill changelog table with historical entries reconstructed from git history.

Inserts changelog entries for all significant system changes since inception.
Safe to re-run: checks for existing entries by git_commit + component.

Run:
    python -m scripts.backfill_changelog
    python scripts/backfill_changelog.py
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.database import async_session_factory
from app.models import Changelog, ChangelogCategory

CHANGELOG_ENTRIES = [
    # 2025-12-23 -- Foundation
    {
        "timestamp": "2025-12-23",
        "category": "infrastructure",
        "component": "foundation",
        "change_type": "added",
        "description": "Initial commit -- 28 execution scripts for scraping, personalization, outreach",
        "git_commit": "03eef70",
    },
    # 2026-01-03 -- Vayne pipeline
    {
        "timestamp": "2026-01-03",
        "category": "prospect_source",
        "component": "vayne_pipeline",
        "change_type": "added",
        "description": "First complete end-to-end pipeline: Vayne CSV -> personalization -> HeyReach upload",
        "git_commit": "f21a7df",
    },
    {
        "timestamp": "2026-01-03",
        "category": "heyreach",
        "component": "heyreach_upload",
        "change_type": "added",
        "description": "HeyReach API integration with custom field support (personalized_message), batch upload 100/chunk",
        "git_commit": "f21a7df",
    },
    # 2026-01-22 -- Intent signal system (MAJOR)
    {
        "timestamp": "2026-01-22",
        "category": "prompt",
        "component": "LINKEDIN_5_LINE_DM_PROMPT",
        "change_type": "added",
        "description": "Centralized prompt source of truth created in prompts.py -- 5-line LinkedIn DM template (greeting, profile hook, business inquiry, authority statement, location hook)",
        "git_commit": "64d5f97",
    },
    {
        "timestamp": "2026-01-22",
        "category": "prospect_source",
        "component": "keyword_signals",
        "change_type": "added",
        "description": "Intent signal monitoring via LinkedIn post keyword search -- scrapes post engagers for outreach",
        "git_commit": "64d5f97",
    },
    {
        "timestamp": "2026-01-22",
        "category": "prospect_source",
        "component": "competitor_signals",
        "change_type": "added",
        "description": "Competitor engagement tracking -- monitors engagers on competitor LinkedIn posts",
        "git_commit": "64d5f97",
    },
    {
        "timestamp": "2026-01-22",
        "category": "prospect_source",
        "component": "influencer_signals",
        "change_type": "added",
        "description": "Influencer engagement tracking -- monitors engagers on influencer LinkedIn posts",
        "git_commit": "64d5f97",
    },
    {
        "timestamp": "2026-01-22",
        "category": "icp_filter",
        "component": "deepseek_icp",
        "change_type": "added",
        "description": "DeepSeek ICP qualification system -- authority rules (CEO/Founder/VP), company size weighting, hard rejections (banks, students, physical labor), industry targeting (B2B SaaS/consulting)",
        "git_commit": "64d5f97",
    },
    {
        "timestamp": "2026-01-22",
        "category": "model",
        "component": "deepseek_primary",
        "change_type": "added",
        "description": "DeepSeek (deepseek-chat) adopted as primary model for ICP checks (temp=0.3) and personalization (temp=0.7) -- 100x cheaper than GPT-4o",
        "git_commit": "64d5f97",
    },
    {
        "timestamp": "2026-01-22",
        "category": "pipeline_config",
        "component": "country_filter",
        "change_type": "added",
        "description": "Country filter hardcoded: United States, Canada, USA, America -- applied after profile scraping",
        "git_commit": "64d5f97",
    },
    {
        "timestamp": "2026-01-22",
        "category": "pipeline_config",
        "component": "config_files",
        "change_type": "added",
        "description": "Config-driven approach: competitors.json (16 accounts), influencers.json (28 accounts), keyword_signals.json (20 keywords)",
        "git_commit": "64d5f97",
    },
    # 2026-01-25 -- Validation gate
    {
        "timestamp": "2026-01-25",
        "category": "validation",
        "component": "validate_personalization",
        "change_type": "added",
        "description": "QA validation gate before HeyReach upload -- 3-dimension scoring (service accuracy, method accuracy, authority relevance), pass>=4.0, auto-fix on failure",
        "git_commit": "e626c21",
    },
    {
        "timestamp": "2026-01-25",
        "category": "infrastructure",
        "component": "heyreach_webhook",
        "change_type": "added",
        "description": "Event-driven HeyReach reply handling via webhook",
        "git_commit": "633c218",
    },
    # 2026-02-04 -- API server + caching (MAJOR)
    {
        "timestamp": "2026-02-04",
        "category": "prompt",
        "component": "LINKEDIN_5_LINE_DM_PROMPT",
        "change_type": "modified",
        "description": "Extended authority statement examples, added CRITICAL fallback rules for service inference when headline/description empty -- checks company name, industry, job title as fallbacks. Added 'NEVER default to corporate comms' rule",
        "git_commit": "9d19ad3",
    },
    {
        "timestamp": "2026-02-04",
        "category": "infrastructure",
        "component": "api_server",
        "change_type": "added",
        "description": "FastAPI server deployed on Railway -- /health, /run-pipeline, /cache-stats endpoints. Cron scheduling for automated daily runs. Default: keywords=ceos, days_back=7, min_reactions=50, list_id=480247",
        "git_commit": "9d19ad3",
    },
    {
        "timestamp": "2026-02-04",
        "category": "pipeline_config",
        "component": "profile_caching",
        "change_type": "added",
        "description": "Profile cache (.tmp/profile_cache.json) to avoid re-scraping same LinkedIn profiles -- saves $0.025 per duplicate",
        "git_commit": "9d19ad3",
    },
    {
        "timestamp": "2026-02-04",
        "category": "icp_filter",
        "component": "headline_prefilter",
        "change_type": "added",
        "description": "Headline pre-filtering BEFORE Apify scrape -- rejects non-English (CJK, Cyrillic, Arabic, >15% non-ASCII), rejects interns/students/drivers/nurses. Saves $0.025 per filtered profile",
        "git_commit": "9d19ad3",
    },
    {
        "timestamp": "2026-02-04",
        "category": "pipeline_config",
        "component": "auto_db_sync",
        "change_type": "added",
        "description": "Auto-sync prospects to PostgreSQL after HeyReach upload -- closes feedback loop for reporting",
        "git_commit": "9d19ad3",
    },
    # 2026-02-07 -- Prospect enrichment
    {
        "timestamp": "2026-02-07",
        "category": "infrastructure",
        "component": "prospect_lookup",
        "change_type": "added",
        "description": "Prospect lookup tool + email field sync from central database during prospect sync",
        "git_commit": "8d45ebc",
    },
    # 2026-02-09 -- Bug fix
    {
        "timestamp": "2026-02-09",
        "category": "infrastructure",
        "component": "api_url_fix",
        "change_type": "modified",
        "description": "CRITICAL BUG FIX: Railway API URL corrected (speed-to-lead-production -> speedtolead-production). All DB syncs were failing before this fix",
        "git_commit": "23dd910",
    },
    # 2026-02-11 -- Buying signals (MAJOR)
    {
        "timestamp": "2026-02-11",
        "category": "prospect_source",
        "component": "buying_signal",
        "change_type": "added",
        "description": "Gojiberry buying signal integration -- webhook receives CSV with 54 fields, processes via buying_signal_outreach.py, scrapes posts for context, generates personalized DMs",
        "git_commit": "3291236",
    },
    {
        "timestamp": "2026-02-11",
        "category": "prompt",
        "component": "LINKEDIN_BUYING_SIGNAL_DM_PROMPT",
        "change_type": "added",
        "description": "New buying signal prompt template -- 4-5 line DM referencing specific post engagement or top 5% activity signal. Two signal types: 'post' (specific engagement) and 'top5' (general activity)",
        "git_commit": "3291236",
    },
    {
        "timestamp": "2026-02-11",
        "category": "ab_test",
        "component": "location_hook_split",
        "change_type": "added",
        "description": "50/50 A/B split on buying signal messages -- half include location hook ('See you're in [city]...'), half skip it. Random shuffle then split",
        "git_commit": "3291236",
    },
    {
        "timestamp": "2026-02-11",
        "category": "infrastructure",
        "component": "report_activity",
        "change_type": "added",
        "description": "Activity reporting script -- POSTs pipeline metrics (posts_scraped, profiles_scraped, icp_qualified, heyreach_uploaded, costs) to central API for daily tracking",
        "git_commit": "3291236",
    },
    {
        "timestamp": "2026-02-11",
        "category": "infrastructure",
        "component": "stop_lead",
        "change_type": "added",
        "description": "Lead pause/stop utility -- stops a lead from receiving further messages in a HeyReach campaign via API",
        "git_commit": "3291236",
    },
]


def parse_timestamp(ts_str: str) -> datetime:
    """Parse a date string to a timezone-aware datetime."""
    return datetime.strptime(ts_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


async def backfill():
    """Insert historical changelog entries, skipping duplicates."""
    async with async_session_factory() as session:
        created = 0
        skipped = 0

        for entry in CHANGELOG_ENTRIES:
            # Check for existing entry by git_commit + component
            result = await session.execute(
                select(Changelog).where(
                    Changelog.git_commit == entry["git_commit"],
                    Changelog.component == entry["component"],
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                print(f"  SKIP (exists): {entry['component']} @ {entry['git_commit']}")
                skipped += 1
                continue

            row = Changelog(
                timestamp=parse_timestamp(entry["timestamp"]),
                category=ChangelogCategory(entry["category"]),
                component=entry["component"],
                change_type=entry["change_type"],
                description=entry["description"],
                git_commit=entry["git_commit"],
            )
            session.add(row)
            created += 1
            print(f"  ADD: [{entry['category']}] {entry['component']} ({entry['timestamp']})")

        await session.commit()
        print(f"\nBackfill complete: {created} created, {skipped} skipped")


if __name__ == "__main__":
    asyncio.run(backfill())
