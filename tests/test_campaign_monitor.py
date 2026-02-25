"""Tests for campaign fuel monitor service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.campaign_monitor import (
    ACTIVE_CAMPAIGN_ID,
    DAILY_CONNECTION_LIMIT,
    check_campaign_fuel,
    get_daily_connection_stats,
    monitor_and_topup,
)


# ---------------------------------------------------------------------------
# Fixtures: mock HeyReach API responses
# ---------------------------------------------------------------------------

def _make_campaign(campaign_id, status, pending, in_progress, finished, total):
    return {
        "id": campaign_id,
        "name": "Smiths Competition",
        "status": status,
        "progressStats": {
            "pending": pending,
            "inProgress": in_progress,
            "finished": finished,
            "total": total,
        },
    }


CAMPAIGN_LOW_FUEL = _make_campaign(
    ACTIVE_CAMPAIGN_ID, "ACTIVE", pending=0, in_progress=158, finished=656, total=822
)

CAMPAIGN_OK_FUEL = _make_campaign(
    ACTIVE_CAMPAIGN_ID, "ACTIVE", pending=120, in_progress=100, finished=600, total=820
)

CAMPAIGN_WARNING_FUEL = _make_campaign(
    ACTIVE_CAMPAIGN_ID, "ACTIVE", pending=30, in_progress=100, finished=600, total=730
)


def _make_stats_response(daily_values: list[int]):
    """Build a mock GetOverallStats response with connectionsSent per day."""
    by_day = {}
    for i, v in enumerate(daily_values):
        date_key = f"2026-02-{17 + i:02d}T00:00:00Z"
        by_day[date_key] = {"connectionsSent": v, "connectionsAccepted": 0, "messagesSent": 0}
    return {"byDayStats": by_day, "overallStats": {}}


_FUEL_OK = {
    "pending": 120,
    "in_progress": 100,
    "finished": 600,
    "total": 820,
    "days_of_fuel": 4.0,
    "campaign_name": "Smiths Competition",
    "campaign_id": ACTIVE_CAMPAIGN_ID,
}

_FUEL_LOW = {
    "pending": 0,
    "in_progress": 158,
    "finished": 656,
    "total": 822,
    "days_of_fuel": 0.0,
    "campaign_name": "Smiths Competition",
    "campaign_id": ACTIVE_CAMPAIGN_ID,
}


# ---------------------------------------------------------------------------
# Tests: check_campaign_fuel
# ---------------------------------------------------------------------------

class TestCheckCampaignFuel:

    @pytest.mark.asyncio
    async def test_parses_low_fuel(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": [CAMPAIGN_LOW_FUEL]}

        with patch("app.services.campaign_monitor.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            result = await check_campaign_fuel()

        assert result["pending"] == 0
        assert result["in_progress"] == 158
        assert result["finished"] == 656
        assert result["total"] == 822
        assert result["days_of_fuel"] == 0.0
        assert result["campaign_name"] == "Smiths Competition"

    @pytest.mark.asyncio
    async def test_parses_ok_fuel(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": [CAMPAIGN_OK_FUEL]}

        with patch("app.services.campaign_monitor.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            result = await check_campaign_fuel()

        assert result["pending"] == 120
        assert result["days_of_fuel"] == 120 / DAILY_CONNECTION_LIMIT

    @pytest.mark.asyncio
    async def test_campaign_not_found_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": [
            _make_campaign(999, "ACTIVE", 50, 50, 50, 150)
        ]}

        with patch("app.services.campaign_monitor.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            result = await check_campaign_fuel()

        assert result is None


# ---------------------------------------------------------------------------
# Tests: get_daily_connection_stats
# ---------------------------------------------------------------------------

class TestGetDailyConnectionStats:

    @pytest.mark.asyncio
    async def test_parses_daily_stats(self):
        stats_data = _make_stats_response([30, 28, 25, 18, 8, 10, 15])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = stats_data

        with patch("app.services.campaign_monitor.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            result = await get_daily_connection_stats()

        assert result == [30, 28, 25, 18, 8, 10, 15]

    @pytest.mark.asyncio
    async def test_empty_stats(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"connectionsSent": []}

        with patch("app.services.campaign_monitor.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client_instance

            result = await get_daily_connection_stats()

        assert result == []


# ---------------------------------------------------------------------------
# Tests: monitor_and_topup (deficit-based logic)
# ---------------------------------------------------------------------------

class TestMonitorAndTopup:

    @pytest.mark.asyncio
    async def test_no_action_when_yesterday_hit_target(self):
        """No alert when yesterday's connections >= daily limit."""
        with patch("app.services.campaign_monitor.check_campaign_fuel") as mock_fuel, \
             patch("app.services.campaign_monitor.get_daily_connection_stats") as mock_stats, \
             patch("app.services.campaign_monitor.get_slack_bot") as mock_slack:

            mock_fuel.return_value = _FUEL_OK
            mock_stats.return_value = [28, 25, 30]  # yesterday=30, on target

            result = await monitor_and_topup()

        assert result["action"] == "none"
        assert result["deficit"] == 0
        assert result["yesterday_sent"] == 30
        mock_slack.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_action_when_yesterday_exceeded_target(self):
        """No alert when yesterday's connections > daily limit."""
        with patch("app.services.campaign_monitor.check_campaign_fuel") as mock_fuel, \
             patch("app.services.campaign_monitor.get_daily_connection_stats") as mock_stats, \
             patch("app.services.campaign_monitor.get_slack_bot") as mock_slack:

            mock_fuel.return_value = _FUEL_OK
            mock_stats.return_value = [28, 25, 35]  # yesterday=35, above target

            result = await monitor_and_topup()

        assert result["action"] == "none"
        assert result["deficit"] == 0
        mock_slack.assert_not_called()

    @pytest.mark.asyncio
    async def test_alerts_on_deficit_with_buying_signal_backlog(self):
        """Triggers buying signal batch when yesterday had deficit and backlog exists."""
        with patch("app.services.campaign_monitor.check_campaign_fuel") as mock_fuel, \
             patch("app.services.campaign_monitor.get_daily_connection_stats") as mock_stats, \
             patch("app.services.campaign_monitor.get_slack_bot") as mock_slack, \
             patch("app.services.campaign_monitor._count_unprocessed_prospects") as mock_count, \
             patch("app.services.buying_signal_outreach.process_buying_signal_batch") as mock_batch:

            mock_fuel.return_value = _FUEL_LOW
            mock_stats.return_value = [18, 8, 10]  # yesterday=10, deficit=20
            mock_count.return_value = 12
            mock_batch.return_value = {"processed": 12, "uploaded": 10}

            bot_instance = AsyncMock()
            mock_slack.return_value = bot_instance

            result = await monitor_and_topup()

        assert result["action"] == "alerted"
        assert result["deficit"] == 20
        assert result["yesterday_sent"] == 10
        assert result["batch_triggered"] is True
        mock_batch.assert_awaited_once()
        bot_instance.send_confirmation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_triggers_lead_finder_when_no_backlog(self):
        """Triggers lead finder pipeline when deficit exists and no buying signal backlog."""
        with patch("app.services.campaign_monitor.check_campaign_fuel") as mock_fuel, \
             patch("app.services.campaign_monitor.get_daily_connection_stats") as mock_stats, \
             patch("app.services.campaign_monitor.get_slack_bot") as mock_slack, \
             patch("app.services.campaign_monitor._count_unprocessed_prospects") as mock_count, \
             patch("asyncio.create_task") as mock_create_task:

            mock_fuel.return_value = _FUEL_LOW
            mock_stats.return_value = [18, 8, 10]  # yesterday=10, deficit=20
            mock_count.return_value = 0

            bot_instance = AsyncMock()
            mock_slack.return_value = bot_instance

            result = await monitor_and_topup()

        assert result["action"] == "alerted"
        assert result["deficit"] == 20
        assert result["lead_finder_triggered"] is True
        assert result["batch_triggered"] is False
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_prefers_batch_over_lead_finder(self):
        """When buying signal backlog exists, uses that instead of lead finder."""
        with patch("app.services.campaign_monitor.check_campaign_fuel") as mock_fuel, \
             patch("app.services.campaign_monitor.get_daily_connection_stats") as mock_stats, \
             patch("app.services.campaign_monitor.get_slack_bot") as mock_slack, \
             patch("app.services.campaign_monitor._count_unprocessed_prospects") as mock_count, \
             patch("app.services.buying_signal_outreach.process_buying_signal_batch") as mock_batch:

            mock_fuel.return_value = _FUEL_LOW
            mock_stats.return_value = [18, 8, 10]  # yesterday=10
            mock_count.return_value = 5
            mock_batch.return_value = {"processed": 5, "uploaded": 5}

            bot_instance = AsyncMock()
            mock_slack.return_value = bot_instance

            result = await monitor_and_topup()

        assert result["batch_triggered"] is True
        assert result.get("lead_finder_triggered", False) is False

    @pytest.mark.asyncio
    async def test_alerts_when_stats_empty(self):
        """Treats empty stats as 0 sent yesterday — triggers fill."""
        with patch("app.services.campaign_monitor.check_campaign_fuel") as mock_fuel, \
             patch("app.services.campaign_monitor.get_daily_connection_stats") as mock_stats, \
             patch("app.services.campaign_monitor.get_slack_bot") as mock_slack, \
             patch("app.services.campaign_monitor._count_unprocessed_prospects") as mock_count, \
             patch("asyncio.create_task"):

            mock_fuel.return_value = _FUEL_LOW
            mock_stats.return_value = []  # no data
            mock_count.return_value = 0

            bot_instance = AsyncMock()
            mock_slack.return_value = bot_instance

            result = await monitor_and_topup()

        assert result["action"] == "alerted"
        assert result["deficit"] == DAILY_CONNECTION_LIMIT  # full deficit
        assert result["yesterday_sent"] == 0

    @pytest.mark.asyncio
    async def test_no_action_when_campaign_not_found(self):
        """Returns error when campaign can't be found."""
        with patch("app.services.campaign_monitor.check_campaign_fuel") as mock_fuel:
            mock_fuel.return_value = None

            result = await monitor_and_topup()

        assert result["action"] == "error"
