#!/usr/bin/env python3
"""Backfill positive replies from CSV export.

This script:
1. Reads a CSV file with positive reply data (Name, Last Name, LI Profile, Follow up needed?)
2. Finds matching prospects by LinkedIn URL
3. Sets positive_reply_at (from conversation data if available)
4. Sets positive_reply_notes from CSV
5. Sets icp_match based on notes

Run on Railway after deployment:
    python scripts/backfill_positive_replies.py /path/to/csv

CSV format expected:
    Name,Last Name,LI Profile,Follow up needed?,Follow up 1
"""

import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.database import async_session_factory
from app.models import Conversation, MessageDirection, MessageLog, Prospect


def normalize_linkedin_url(url: str) -> str:
    """Normalize LinkedIn URL for consistent matching."""
    if not url:
        return ""
    url = url.lower().strip().rstrip("/")
    if "?" in url:
        url = url.split("?")[0]
    return url


def parse_icp_match(notes: str) -> bool | None:
    """Parse ICP match from notes field.

    Returns:
        True: "yes" variants
        False: "no", "not icp", "employee", "no traction" variants
        None: unclear/empty
    """
    if not notes:
        return None

    notes_lower = notes.lower().strip()

    # Explicitly not ICP
    not_icp_indicators = [
        "not icp",
        "no - ",
        "no -",
        "employee",
        "not decision maker",
        "no traction",
        "between roles",
        "blank profile",
    ]
    for indicator in not_icp_indicators:
        if indicator in notes_lower:
            return False

    # Explicitly ICP / positive
    if notes_lower.startswith("yes"):
        return True

    # Other indicators of positive
    positive_indicators = [
        "hot prospect",
        "needs reply",
        "just reply",
        "follow up",
    ]
    for indicator in positive_indicators:
        if indicator in notes_lower:
            return True

    # Unclear
    if "unclear" in notes_lower:
        return None

    return None


async def get_first_reply_timestamp(
    session, conversation_id
) -> datetime | None:
    """Get timestamp of first inbound message in a conversation."""
    if not conversation_id:
        return None

    # Query MessageLog for first inbound message
    result = await session.execute(
        select(MessageLog)
        .where(MessageLog.conversation_id == conversation_id)
        .where(MessageLog.direction == MessageDirection.INBOUND)
        .order_by(MessageLog.sent_at.asc())
        .limit(1)
    )
    message = result.scalar_one_or_none()

    if message:
        return message.sent_at

    # Fallback: check conversation_history JSON if no MessageLog
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    convo = result.scalar_one_or_none()

    if convo and convo.conversation_history:
        for msg in convo.conversation_history:
            # Look for inbound messages in history
            if msg.get("direction") == "inbound" or msg.get("isInbound"):
                timestamp = msg.get("timestamp") or msg.get("sent_at")
                if timestamp:
                    if isinstance(timestamp, str):
                        try:
                            return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    elif isinstance(timestamp, datetime):
                        return timestamp

    return None


async def backfill_positive_replies(csv_path: str):
    """Backfill positive replies from CSV."""
    csv_file = Path(csv_path)

    if not csv_file.exists():
        print(f"CSV file not found: {csv_path}")
        return

    # Read CSV
    rows = []
    with open(csv_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Read {len(rows)} rows from CSV")

    async with async_session_factory() as session:
        updated = 0
        not_found = 0
        already_set = 0

        for row in rows:
            linkedin_url = normalize_linkedin_url(row.get("LI Profile", ""))
            if not linkedin_url:
                continue

            notes = row.get("Follow up needed?", "").strip()

            # Find prospect
            result = await session.execute(
                select(Prospect).where(Prospect.linkedin_url == linkedin_url)
            )
            prospect = result.scalar_one_or_none()

            if not prospect:
                name = f"{row.get('Name', '')} {row.get('Last Name', '')}".strip()
                print(f"  Not found: {name} - {linkedin_url}")
                not_found += 1
                continue

            # Skip if already has positive_reply_at set
            if prospect.positive_reply_at:
                already_set += 1
                continue

            # Get first reply timestamp from conversation if linked
            reply_at = None
            if prospect.conversation_id:
                reply_at = await get_first_reply_timestamp(session, prospect.conversation_id)

            # Update prospect
            prospect.positive_reply_at = reply_at  # Will be None if no timestamp found
            prospect.positive_reply_notes = notes if notes else None

            # Update ICP match if not already set
            if prospect.icp_match is None:
                prospect.icp_match = parse_icp_match(notes)

            updated += 1

            name = prospect.full_name or f"{prospect.first_name or ''} {prospect.last_name or ''}".strip()
            reply_status = f"reply_at={reply_at}" if reply_at else "no timestamp"
            icp_status = f"icp={prospect.icp_match}"
            print(f"  Updated: {name} - {reply_status}, {icp_status}")

        await session.commit()

        print(f"\nBackfill complete:")
        print(f"  Updated: {updated}")
        print(f"  Not found: {not_found}")
        print(f"  Already set: {already_set}")


async def main():
    if len(sys.argv) < 2:
        print("Usage: python backfill_positive_replies.py <csv_file>")
        print('Example: python backfill_positive_replies.py "positive_replies.csv"')
        sys.exit(1)

    csv_path = sys.argv[1]
    await backfill_positive_replies(csv_path)


if __name__ == "__main__":
    asyncio.run(main())
