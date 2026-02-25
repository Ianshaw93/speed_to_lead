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
        headers={"X-API-KEY": settings.heyreach_api_key, "Accept": "text/plain"},
        timeout=30.0,
    ) as client:
        resp = await client.get(
            f"{HEYREACH_BASE_URL}/campaign/GetAll",
            params={"offset": 0, "limit": 50},
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
                "campaignId": ACTIVE_CAMPAIGN_ID,
                "accountIds": ACCOUNT_IDS,
                "startDate": start.strftime("%Y-%m-%dT00:00:00Z"),
                "endDate": now.strftime("%Y-%m-%dT23:59:59Z"),
            },
        )
        resp.raise_for_status()
        data = resp.json()

    entries = data.get("connectionsSent", [])
    return [entry.get("count", 0) for entry in entries]


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
    unprocessed: int,
    batch_triggered: bool,
    batch_result: dict | None,
    lead_finder_triggered: bool = False,
    lead_finder_result: dict | None = None,
) -> str:
    """Build the Slack alert message."""
    name = fuel["campaign_name"]
    pending = fuel["pending"]
    in_progress = fuel["in_progress"]
    finished = fuel["finished"]
    total = fuel["total"]

    last_3 = daily_stats[-3:] if len(daily_stats) >= 3 else daily_stats
    stats_str = ", ".join(str(s) for s in last_3) if last_3 else "no data"

    lines = [
        f"*Campaign Fuel Alert — {name}*",
        "",
        f"Pending: {pending} | In Progress: {in_progress} | Finished: {finished}/{total}",
        f"Last {len(last_3)} days connections: {stats_str}",
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

    # 3. Determine if fuel is low
    pending = fuel["pending"]
    recent_2day_avg = (
        sum(daily_stats[-2:]) / len(daily_stats[-2:])
        if len(daily_stats) >= 2
        else float("inf")
    )

    fuel_low = pending < LOW_FUEL_THRESHOLD
    activity_low = recent_2day_avg < LOW_ACTIVITY_THRESHOLD

    if not fuel_low and not activity_low:
        logger.info(
            f"Campaign fuel OK: {pending} pending, "
            f"2-day avg connections: {recent_2day_avg:.1f}"
        )
        return {"action": "none", "pending": pending, "avg_connections": recent_2day_avg}

    # 4. Fuel or activity is low — check for unprocessed prospects
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

    if not batch_triggered and unprocessed == 0 and pending < LOW_FUEL_THRESHOLD:
        try:
            from app.services.lead_finder_pipeline import run_lead_finder_pipeline

            # Calculate how many leads to fetch (deficit × 3 for funnel losses)
            deficit = DAILY_CONNECTION_LIMIT - int(recent_2day_avg) if recent_2day_avg < DAILY_CONNECTION_LIMIT else 30
            fetch_count = max(deficit * 3, 50)

            logger.info(f"Auto-triggering lead finder pipeline (fetch_count={fetch_count})")
            asyncio.create_task(run_lead_finder_pipeline(fetch_count=fetch_count))
            lead_finder_triggered = True
        except Exception as e:
            logger.error(f"Auto lead finder pipeline failed: {e}", exc_info=True)

    # 6. Send Slack alert
    try:
        bot = get_slack_bot()
        message = _build_alert_message(
            fuel, daily_stats, unprocessed, batch_triggered, batch_result,
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
        "avg_connections": recent_2day_avg,
        "unprocessed_count": unprocessed,
        "batch_triggered": batch_triggered,
        "batch_result": batch_result,
        "lead_finder_triggered": lead_finder_triggered,
    }
