"""Tests for automatic follow-up list removal when prospects reply within 24 hours."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import Response
from sqlalchemy import select

from app.models import Conversation, Prospect, ProspectSource
from app.services.heyreach import HeyReachClient, HeyReachError


class TestRemoveLeadFromList:
    """Tests for the HeyReach remove_lead_from_list method."""

    @pytest.fixture
    def client(self):
        """Create a HeyReach client for testing."""
        return HeyReachClient(api_key="test_api_key")

    @pytest.mark.asyncio
    async def test_remove_lead_from_list_success(self, client):
        """Should successfully remove a lead from a HeyReach list."""
        mock_response = Response(
            200,
            json={"success": True, "removedCount": 1},
        )

        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await client.remove_lead_from_list(
                list_id=511495,
                linkedin_url="https://www.linkedin.com/in/johndoe",
            )

            assert result["success"] is True
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "/list/RemoveLeadsFromList"
            payload = call_args[1]["json"]
            assert payload["listId"] == 511495
            assert "https://www.linkedin.com/in/johndoe" in payload["profileUrls"]

    @pytest.mark.asyncio
    async def test_remove_lead_from_list_api_error(self, client):
        """Should raise HeyReachError on API failure."""
        mock_response = Response(
            400,
            json={"error": "Invalid list ID"},
        )

        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(HeyReachError) as exc_info:
                await client.remove_lead_from_list(
                    list_id=999999,
                    linkedin_url="https://www.linkedin.com/in/test",
                )

            assert "Failed to remove lead from list" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_remove_lead_from_list_network_error(self, client):
        """Should raise HeyReachError on network failure."""
        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = Exception("Connection refused")

            with pytest.raises(HeyReachError) as exc_info:
                await client.remove_lead_from_list(
                    list_id=511495,
                    linkedin_url="https://www.linkedin.com/in/test",
                )

            assert "Connection refused" in str(exc_info.value)


class TestFollowupListTracking:
    """Tests for tracking when prospects are added to follow-up list."""

    @pytest.mark.asyncio
    async def test_prospect_followup_fields_exist(self, test_db_session):
        """Should have fields to track follow-up list addition."""
        # Create a prospect with follow-up tracking fields
        prospect = Prospect(
            linkedin_url="https://www.linkedin.com/in/testuser",
            full_name="Test User",
            source_type=ProspectSource.COMPETITOR_POST,
            followup_list_id=511495,
            added_to_followup_at=datetime.now(timezone.utc),
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        # Fetch and verify
        result = await test_db_session.execute(
            select(Prospect).where(Prospect.linkedin_url == "https://www.linkedin.com/in/testuser")
        )
        fetched = result.scalar_one()

        assert fetched.followup_list_id == 511495
        assert fetched.added_to_followup_at is not None

    @pytest.mark.asyncio
    async def test_prospect_should_be_removed_from_followup_within_24h(self, test_db_session):
        """Prospect should be removable if they reply within 24 hours of being added."""
        now = datetime.now(timezone.utc)
        added_20_hours_ago = now - timedelta(hours=20)

        prospect = Prospect(
            linkedin_url="https://www.linkedin.com/in/replyuser",
            full_name="Reply User",
            source_type=ProspectSource.COMPETITOR_POST,
            followup_list_id=511495,
            added_to_followup_at=added_20_hours_ago,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        # Check if within 24 hours
        result = await test_db_session.execute(
            select(Prospect).where(
                Prospect.linkedin_url == "https://www.linkedin.com/in/replyuser",
                Prospect.followup_list_id.isnot(None),
                Prospect.added_to_followup_at > now - timedelta(hours=24),
            )
        )
        fetched = result.scalar_one_or_none()
        assert fetched is not None
        assert fetched.followup_list_id == 511495

    @pytest.mark.asyncio
    async def test_prospect_should_not_be_removed_after_24h(self, test_db_session):
        """Prospect should NOT be removed if they reply after 24 hours."""
        now = datetime.now(timezone.utc)
        added_30_hours_ago = now - timedelta(hours=30)

        prospect = Prospect(
            linkedin_url="https://www.linkedin.com/in/lateruser",
            full_name="Later User",
            source_type=ProspectSource.COMPETITOR_POST,
            followup_list_id=511495,
            added_to_followup_at=added_30_hours_ago,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        # Check - should NOT find prospect within 24 hours
        result = await test_db_session.execute(
            select(Prospect).where(
                Prospect.linkedin_url == "https://www.linkedin.com/in/lateruser",
                Prospect.followup_list_id.isnot(None),
                Prospect.added_to_followup_at > now - timedelta(hours=24),
            )
        )
        fetched = result.scalar_one_or_none()
        assert fetched is None  # Not found because added > 24h ago


class TestCheckAndRemoveOnWebhook:
    """Tests for checking and removing prospects on webhook receipt."""

    @pytest.mark.asyncio
    async def test_check_and_remove_prospect_on_reply_within_24h(self, test_db_session):
        """Should stop campaign AND remove from list when they reply within 24h."""
        from app.main import normalize_linkedin_url, check_and_remove_from_followup

        now = datetime.now(timezone.utc)
        linkedin_url = "https://www.linkedin.com/in/quickreply"

        # Create prospect added to follow-up 10 hours ago
        prospect = Prospect(
            linkedin_url=normalize_linkedin_url(linkedin_url),
            full_name="Quick Reply",
            source_type=ProspectSource.COMPETITOR_POST,
            followup_list_id=511495,
            added_to_followup_at=now - timedelta(hours=10),
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        with patch("app.services.heyreach.get_heyreach_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.stop_lead_in_campaign.return_value = {"success": True}
            mock_client.remove_lead_from_list.return_value = {"success": True}
            mock_get_client.return_value = mock_client

            removed = await check_and_remove_from_followup(test_db_session, linkedin_url)

            assert removed is True
            # Should stop the campaign
            mock_client.stop_lead_in_campaign.assert_called_once_with(
                campaign_id=300178,
                linkedin_url=normalize_linkedin_url(linkedin_url),
            )
            # Should also remove from list (within 24h)
            mock_client.remove_lead_from_list.assert_called_once_with(
                list_id=511495,
                linkedin_url=normalize_linkedin_url(linkedin_url),
            )

        # Verify prospect's followup fields are cleared
        await test_db_session.refresh(prospect)
        assert prospect.followup_list_id is None
        assert prospect.added_to_followup_at is None

    @pytest.mark.asyncio
    async def test_no_action_after_24h(self, test_db_session):
        """Should take no action after 24h — HeyReach handles replies to follow-ups natively."""
        from app.main import normalize_linkedin_url, check_and_remove_from_followup

        now = datetime.now(timezone.utc)
        linkedin_url = "https://www.linkedin.com/in/latereply"

        # Create prospect added to follow-up 30 hours ago (past 24h window)
        prospect = Prospect(
            linkedin_url=normalize_linkedin_url(linkedin_url),
            full_name="Late Reply",
            source_type=ProspectSource.COMPETITOR_POST,
            followup_list_id=511495,
            added_to_followup_at=now - timedelta(hours=30),
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        with patch("app.services.heyreach.get_heyreach_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            removed = await check_and_remove_from_followup(test_db_session, linkedin_url)

            assert removed is False
            mock_client.stop_lead_in_campaign.assert_not_called()
            mock_client.remove_lead_from_list.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_action_for_prospect_not_in_followup(self, test_db_session):
        """Should not attempt any action if prospect is not in follow-up list."""
        from app.main import normalize_linkedin_url, check_and_remove_from_followup

        linkedin_url = "https://www.linkedin.com/in/normaluser"

        # Create prospect without follow-up tracking
        prospect = Prospect(
            linkedin_url=normalize_linkedin_url(linkedin_url),
            full_name="Normal User",
            source_type=ProspectSource.COMPETITOR_POST,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        with patch("app.services.heyreach.get_heyreach_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            removed = await check_and_remove_from_followup(test_db_session, linkedin_url)

            assert removed is False
            mock_client.stop_lead_in_campaign.assert_not_called()
            mock_client.remove_lead_from_list.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_action_for_unknown_prospect(self, test_db_session):
        """Should not attempt any action if prospect doesn't exist in database."""
        from app.main import check_and_remove_from_followup

        with patch("app.services.heyreach.get_heyreach_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            removed = await check_and_remove_from_followup(
                test_db_session,
                "https://www.linkedin.com/in/unknownuser",
            )

            assert removed is False
            mock_client.stop_lead_in_campaign.assert_not_called()
            mock_client.remove_lead_from_list.assert_not_called()


class TestUpdateProspectOnAddToList:
    """Tests for updating prospect when added to follow-up list."""

    @pytest.mark.asyncio
    async def test_update_prospect_followup_tracking(self, test_db_session):
        """Should update prospect with follow-up list tracking info."""
        from app.routers.slack import update_prospect_followup_tracking
        from app.main import normalize_linkedin_url

        linkedin_url = "https://www.linkedin.com/in/trackme"
        list_id = 511495
        normalized_url = normalize_linkedin_url(linkedin_url)

        # Create prospect without follow-up tracking
        prospect = Prospect(
            linkedin_url=normalized_url,
            full_name="Track Me",
            source_type=ProspectSource.COMPETITOR_POST,
        )
        test_db_session.add(prospect)
        await test_db_session.commit()

        # Update tracking
        await update_prospect_followup_tracking(test_db_session, linkedin_url, list_id)
        await test_db_session.commit()

        # Verify by re-fetching from the database
        result = await test_db_session.execute(
            select(Prospect).where(Prospect.linkedin_url == normalized_url)
        )
        fetched_prospect = result.scalar_one()
        assert fetched_prospect.followup_list_id == list_id
        assert fetched_prospect.added_to_followup_at is not None

    @pytest.mark.asyncio
    async def test_update_prospect_followup_tracking_no_prospect(self, test_db_session):
        """Should handle gracefully when prospect doesn't exist."""
        from app.routers.slack import update_prospect_followup_tracking

        # Should not raise error
        await update_prospect_followup_tracking(
            test_db_session,
            "https://www.linkedin.com/in/nonexistent",
            511495,
        )
