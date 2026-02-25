"""Campaign fuel monitor: checks HeyReach campaign health and auto-tops-up when low."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select

from app.config import settings
from app.models import Prospect, ProspectSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DAILY_CONNECTION_LIMIT = 30
LOW_FUEL_THRESHOLD = 50      # pending leads
LOW_ACTIVITY_THRESHOLD = 15  # connections/day, 2-day average
ACTIVE_CAMPAIGN_ID = 300178
ACCOUNT_IDS = [78135]
HEYREACH_LIST_ID = 480247

HEYREACH_BASE_URL = "https://api.heyreach.io/api/public"


# ---------------------------------------------------------------------------
# HeyReach API helpers
# ---------------------------------------------------------------------------

async def check_campaign_fuel() -> dict | None:
    """Fetch campaign progress stats from HeyReach GetAll endpoint.

    Returns dict with pending, in_progress, finished, total, days_of_fuel,
    campaign_name, campaign_id — or None if campaign not found.
    """
    async with httpx.AsyncClient(
        headers={
            "X-API-KEY": settings.heyreach_api_key,
            "Content-Type": "application/json",
            "Accept": "text/plain",
        },
        timeout=30.0,
    ) as client:
        resp = await client.post(
            f"{HEYREACH_BASE_URL}/campaign/GetAll",
            json={"offset": 0, "limit": 50},
        )
        resp.raise_for_status()
        data = resp.json()

    campaigns = data.get("items", [])
    for c in campaigns:
        if c.get("id") == ACTIVE_CAMPAIGN_ID:
            stats = c.get("progressStats", {})
            pending = stats.get("pending", 0)
            return {
                "campaign_id": c["id"],
                "campaign_name": c.get("name", "Unknown"),
                "pending": pending,
                "in_progress": stats.get("inProgress", 0),
                "finished": stats.get("finished", 0),
                "total": stats.get("total", 0),
                "days_of_fuel": pending / DAILY_CONNECTION_LIMIT if DAILY_CONNECTION_LIMIT else 0,
            }

    logger.warning(f"Campaign {ACTIVE_CAMPAIGN_ID} not found in HeyReach GetAll response")
    return None


async def get_daily_connection_stats() -> list[int]:
    """Fetch last 7 days of connectionsSent from HeyReach GetOverallStats.

    Returns list of daily connection counts (oldest first).
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=7)

    async with httpx.AsyncClient(
        headers={
            "X-API-KEY": settings.heyreach_api_key,
            "Content-Type": "application/json",
            "Accept": "text/plain",
        },
        timeout=30.0,
    ) as client:
        resp = await client.post(
            f"{HEYREACH_BASE_URL}/stats/GetOverallStats",
            json={
                "campaignIds": [ACTIVE_CAMPAIGN_ID],
                "accountIds": ACCOUNT_IDS,
                "startDate": start.strftime("%Y-%m-%dT00:00:00Z"),
                "endDate": now.strftime("%Y-%m-%dT23:59:59Z"),
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # Response shape: {"byDayStats": {"2026-02-18T00:00:00Z": {"connectionsSent": 30, ...}, ...}}
    by_day = data.get("byDayStats", {})
    if not by_day:
        logger.warning(f"GetOverallStats: no byDayStats in response. Keys: {list(data.keys())}")
        return []

    # Sort by date key and extract connectionsSent per day
    sorted_days = sorted(by_day.items())
    daily_counts = [day_stats.get("connectionsSent", 0) for _, day_stats in sorted_days]

    logger.info(f"Daily connection stats (last {len(daily_counts)} days): {daily_counts}")
    return daily_counts


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

async def _count_unprocessed_prospects() -> int:
    """Count buying-signal prospects with no personalized message yet."""
    from app.database import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(
            select(func.count(Prospect.id)).where(
                Prospect.source_type == ProspectSource.BUYING_SIGNAL,
                Prospect.personalized_message.is_(None),
            )
        )
        return result.scalar() or 0


# ---------------------------------------------------------------------------
# Slack helper
# ---------------------------------------------------------------------------

def get_slack_bot():
    """Lazy import to avoid circular deps."""
    from app.services.slack import get_slack_bot as _get
    return _get()


def _build_alert_message(
    fuel: dict,
    daily_stats: list[int],
    deficit: int,
    unprocessed: int,
    batch_triggered: bool,
    batch_result: dict | None,
    lead_finder_triggered: bool = False,
    lead_finder_result: dict | None = None,
) -> str:
    """Build the Slack alert message."""
    name = fuel["campaign_name"]
    pending = fuel["pending"]
    yesterday_sent = daily_stats[-1] if daily_stats else 0

    last_3 = daily_stats[-3:] if len(daily_stats) >= 3 else daily_stats
    stats_str = ", ".join(str(s) for s in last_3) if last_3 else "no data"

    lines = [
        f"*Campaign Fuel Check — {name}*",
        "",
        f"Yesterday: {yesterday_sent}/{DAILY_CONNECTION_LIMIT} connections sent"
        + (f" (deficit: {deficit})" if deficit > 0 else " ✓"),
        f"Pending in queue: {pending}",
        f"Last {len(last_3)} days: {stats_str}",
    ]

    if batch_triggered and batch_result:
        lines.append(
            f"\nAuto-processed {batch_result.get('processed', 0)} queued buying signal prospects"
        )
    elif unprocessed > 0:
        lines.append(f"\n{unprocessed} unprocessed buying signal prospects in queue")

    if lead_finder_triggered:
        lines.append("\nLead finder pipeline triggered (running in background)")
        if lead_finder_result:
            uploaded = lead_finder_result.get("uploaded", 0)
            surplus = lead_finder_result.get("surplus_from_prev", 0)
            if surplus:
                lines.append(f"  Surplus from previous runs: {surplus} uploaded")
            if uploaded:
                lines.append(f"  New leads uploaded: {uploaded}")

    if pending == 0:
        lines.append(f"\nFuel critically low — add more prospects to list {HEYREACH_LIST_ID}")
    elif pending < LOW_FUEL_THRESHOLD:
        lines.append(f"\nFuel running low ({pending} pending) — consider adding more prospects")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def monitor_and_topup() -> dict:
    """Check campaign fuel and auto-trigger buying signal batch if needed.

    Returns a summary dict describing what action was taken.
    """
    # 1. Get fuel status
    fuel = await check_campaign_fuel()
    if fuel is None:
        logger.error("Could not retrieve campaign fuel status")
        return {"action": "error", "reason": "campaign_not_found"}

    # 2. Get daily connection stats
    try:
        daily_stats = await get_daily_connection_stats()
    except Exception as e:
        logger.warning(f"Failed to get daily stats: {e}")
        daily_stats = []

    # 3. Check yesterday's connection count against daily limit
    pending = fuel["pending"]
    yesterday_sent = daily_stats[-1] if daily_stats else 0
    deficit = DAILY_CONNECTION_LIMIT - yesterday_sent

    if deficit <= 0:
        logger.info(
            f"Campaign on track: {yesterday_sent} connections sent yesterday "
            f"(target: {DAILY_CONNECTION_LIMIT}), {pending} pending"
        )
        return {
            "action": "none",
            "pending": pending,
            "yesterday_sent": yesterday_sent,
            "deficit": 0,
        }

    logger.info(
        f"Connection deficit: {yesterday_sent}/{DAILY_CONNECTION_LIMIT} sent yesterday, "
        f"deficit={deficit}, pending={pending}"
    )

    # 4. Deficit exists — try to fill from buying signal backlog first
    unprocessed = await _count_unprocessed_prospects()
    batch_triggered = False
    batch_result = None

    if unprocessed > 0:
        try:
            from app.services.buying_signal_outreach import process_buying_signal_batch

            logger.info(f"Auto-triggering buying signal batch ({unprocessed} unprocessed)")
            batch_result = await process_buying_signal_batch()
            batch_triggered = True
        except Exception as e:
            logger.error(f"Auto buying signal batch failed: {e}", exc_info=True)

    # 5. If no buying signal backlog, trigger lead finder pipeline
    lead_finder_triggered = False
    lead_finder_result = None

    if not batch_triggered and unprocessed == 0:
        try:
            from app.services.lead_finder_pipeline import run_lead_finder_pipeline

            # Fetch 3x deficit to account for funnel losses (dedup, ICP filter)
            fetch_count = max(deficit * 3, 50)

            logger.info(f"Auto-triggering lead finder pipeline (deficit={deficit}, fetch_count={fetch_count})")
            asyncio.create_task(run_lead_finder_pipeline(fetch_count=fetch_count))
            lead_finder_triggered = True
        except Exception as e:
            logger.error(f"Auto lead finder pipeline failed: {e}", exc_info=True)

    # 6. Send Slack alert
    try:
        bot = get_slack_bot()
        message = _build_alert_message(
            fuel, daily_stats, deficit, unprocessed, batch_triggered, batch_result,
            lead_finder_triggered=lead_finder_triggered,
            lead_finder_result=lead_finder_result,
        )
        await bot.send_confirmation(message)
        logger.info("Campaign fuel alert sent to Slack")
    except Exception as e:
        logger.error(f"Failed to send Slack alert: {e}", exc_info=True)

    return {
        "action": "alerted",
        "pending": pending,
        "yesterday_sent": yesterday_sent,
        "deficit": deficit,
        "unprocessed_count": unprocessed,
        "batch_triggered": batch_triggered,
        "batch_result": batch_result,
        "lead_finder_triggered": lead_finder_triggered,
    }
