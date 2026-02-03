#!/usr/bin/env python3
"""Backfill prospects table from multichannel-outreach JSON files.

This script:
1. Reads all prospect JSON files from a specified directory
2. Inserts unique prospects into the database
3. Links existing conversations to prospects by LinkedIn URL

Run on Railway after deployment:
    python scripts/backfill_prospects.py /path/to/json/files

Or via the API endpoint for remote backfill.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.database import async_session_factory
from app.models import Conversation, Prospect, ProspectSource


def normalize_linkedin_url(url: str) -> str:
    """Normalize LinkedIn URL for consistent matching."""
    if not url:
        return ""
    url = url.lower().strip().rstrip("/")
    if "?" in url:
        url = url.split("?")[0]
    return url


def infer_source_type(filename: str) -> ProspectSource:
    """Infer source type from filename."""
    filename = filename.lower()
    if "competitor_post" in filename:
        return ProspectSource.COMPETITOR_POST
    elif "cold_outreach" in filename:
        return ProspectSource.COLD_OUTREACH
    elif "sales_nav" in filename:
        return ProspectSource.SALES_NAV
    elif "vayne" in filename:
        return ProspectSource.VAYNE
    return ProspectSource.OTHER


def infer_keyword(filename: str) -> str | None:
    """Try to infer keyword from filename."""
    # e.g., competitor_post_leads_20260202_164246.json doesn't have keyword
    # but if it did, it might be competitor_post_ceo_20260202.json
    return None


async def backfill_from_json_files(json_dir: str):
    """Backfill prospects from JSON files in a directory."""
    json_path = Path(json_dir)

    if not json_path.exists():
        print(f"Directory not found: {json_dir}")
        return

    json_files = list(json_path.glob("*.json"))
    print(f"Found {len(json_files)} JSON files in {json_dir}")

    all_prospects = []
    seen_urls = set()

    for json_file in json_files:
        filename = json_file.name

        # Skip validation/cache files
        if any(skip in filename for skip in ["validation", "cache", "heyreach_campaigns"]):
            continue

        try:
            with open(json_file) as f:
                data = json.load(f)

            if not isinstance(data, list):
                continue

            # Check if it looks like prospect data
            if not data or not isinstance(data[0], dict):
                continue

            first = data[0]
            if not any(key in first for key in ["linkedinUrl", "linkedin_url", "profileUrl"]):
                continue

            source_type = infer_source_type(filename)
            source_keyword = infer_keyword(filename)

            for p in data:
                linkedin_url = normalize_linkedin_url(
                    p.get("linkedinUrl") or p.get("linkedin_url") or p.get("profileUrl") or ""
                )

                if not linkedin_url or linkedin_url in seen_urls:
                    continue

                seen_urls.add(linkedin_url)
                all_prospects.append({
                    "linkedin_url": linkedin_url,
                    "full_name": p.get("fullName") or p.get("full_name"),
                    "first_name": p.get("firstName") or p.get("first_name"),
                    "last_name": p.get("lastName") or p.get("last_name"),
                    "job_title": p.get("jobTitle") or p.get("job_title") or p.get("position"),
                    "company_name": p.get("companyName") or p.get("company_name") or p.get("company"),
                    "company_industry": p.get("companyIndustry") or p.get("company_industry"),
                    "location": p.get("addressWithCountry") or p.get("location"),
                    "headline": p.get("headline"),
                    "source_type": source_type,
                    "source_keyword": source_keyword or p.get("source_keyword"),
                    "personalized_message": p.get("personalized_message"),
                    "icp_match": p.get("icp_match"),
                    "icp_reason": p.get("icp_reason"),
                    "heyreach_list_id": p.get("heyreach_list_id"),
                })

            print(f"  {filename}: {len(data)} records")

        except Exception as e:
            print(f"  Error reading {filename}: {e}")

    print(f"\nTotal unique prospects to import: {len(all_prospects)}")

    if not all_prospects:
        print("No prospects to import")
        return

    # Insert into database
    async with async_session_factory() as session:
        created = 0
        skipped = 0

        for p in all_prospects:
            # Check if already exists
            result = await session.execute(
                select(Prospect).where(Prospect.linkedin_url == p["linkedin_url"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            prospect = Prospect(
                linkedin_url=p["linkedin_url"],
                full_name=p["full_name"],
                first_name=p["first_name"],
                last_name=p["last_name"],
                job_title=p["job_title"],
                company_name=p["company_name"],
                company_industry=p["company_industry"],
                location=p["location"],
                headline=p["headline"],
                source_type=p["source_type"],
                source_keyword=p["source_keyword"],
                personalized_message=p["personalized_message"],
                icp_match=p["icp_match"],
                icp_reason=p["icp_reason"],
                heyreach_list_id=p["heyreach_list_id"],
                heyreach_uploaded_at=datetime.now(timezone.utc) if p["heyreach_list_id"] else None,
            )
            session.add(prospect)
            created += 1

            if created % 100 == 0:
                print(f"  Created {created} prospects...")

        await session.commit()
        print(f"\nBackfill complete: {created} created, {skipped} skipped (already exist)")

        # Link to existing conversations
        print("\nLinking prospects to conversations...")
        linked = 0

        # Get all conversations
        convos_result = await session.execute(select(Conversation))
        conversations = convos_result.scalars().all()

        for convo in conversations:
            # Try to match by LinkedIn URL
            # The linkedin_profile_url in conversations might be in different formats
            convo_url = normalize_linkedin_url(convo.linkedin_profile_url or "")

            if not convo_url or "linkedin://conversation/" in convo_url:
                continue

            result = await session.execute(
                select(Prospect).where(Prospect.linkedin_url == convo_url)
            )
            prospect = result.scalar_one_or_none()

            if prospect and not prospect.conversation_id:
                prospect.conversation_id = convo.id
                linked += 1

        await session.commit()
        print(f"Linked {linked} prospects to existing conversations")


async def main():
    if len(sys.argv) < 2:
        print("Usage: python backfill_prospects.py <json_directory>")
        print("Example: python backfill_prospects.py /app/.tmp")
        sys.exit(1)

    json_dir = sys.argv[1]
    await backfill_from_json_files(json_dir)


if __name__ == "__main__":
    asyncio.run(main())
