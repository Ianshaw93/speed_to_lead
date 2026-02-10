"""Tests for HeyReach service and webhook."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, Response

from app.services.heyreach import HeyReachClient, HeyReachError


class TestHeyReachClient:
    """Tests for the HeyReach API client."""

    @pytest.fixture
    def client(self):
        """Create a HeyReach client for testing."""
        return HeyReachClient(api_key="test_api_key")

    @pytest.mark.asyncio
    async def test_send_message_success(self, client):
        """Should successfully send a message via HeyReach API."""
        mock_response = Response(
            200,
            json={"success": True, "messageId": "msg_123"},
        )

        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await client.send_message(
                conversation_id="conv_123",
                linkedin_account_id="li_account_456",
                message="Hello, thanks for connecting!",
            )

            assert result["success"] is True
            assert result["messageId"] == "msg_123"
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_api_error(self, client):
        """Should raise HeyReachError on API failure."""
        mock_response = Response(
            400,
            json={"error": "Invalid conversation ID"},
        )

        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(HeyReachError) as exc_info:
                await client.send_message(
                    conversation_id="invalid_conv",
                    linkedin_account_id="li_account_456",
                    message="Hello!",
                )

            assert "Failed to send message" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_message_network_error(self, client):
        """Should raise HeyReachError on network failure."""
        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = Exception("Connection refused")

            with pytest.raises(HeyReachError) as exc_info:
                await client.send_message(
                    conversation_id="conv_123",
                    linkedin_account_id="li_account_456",
                    message="Hello!",
                )

            assert "Connection refused" in str(exc_info.value)

    def test_client_initialization(self):
        """Should initialize with correct API key."""
        client = HeyReachClient(api_key="my_secret_key")
        assert client._api_key == "my_secret_key"

    @pytest.mark.asyncio
    async def test_add_leads_to_list_success(self, client):
        """Should successfully add leads to a HeyReach list."""
        mock_response = Response(
            200,
            json={"addedCount": 1, "updatedCount": 0, "failedCount": 0},
        )

        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await client.add_leads_to_list(
                list_id=511495,
                leads=[{
                    "linkedin_url": "https://www.linkedin.com/in/johndoe",
                    "first_name": "John",
                    "last_name": "Doe",
                }],
            )

            assert result["addedCount"] == 1
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "/list/AddLeadsToListV2"
            payload = call_args[1]["json"]
            assert payload["listId"] == 511495
            assert len(payload["leads"]) == 1
            assert payload["leads"][0]["profileUrl"] == "https://www.linkedin.com/in/johndoe"

    @pytest.mark.asyncio
    async def test_add_leads_to_list_with_custom_fields(self, client):
        """Should add leads with custom fields."""
        mock_response = Response(
            200,
            json={"addedCount": 1, "updatedCount": 0, "failedCount": 0},
        )

        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await client.add_leads_to_list(
                list_id=511495,
                leads=[{
                    "linkedin_url": "https://www.linkedin.com/in/johndoe",
                    "first_name": "John",
                    "last_name": "Doe",
                    "custom_fields": {
                        "FOLLOW_UP1": "Hey John, following up on our chat.",
                        "FOLLOW_UP2": "Just checking in again.",
                        "FOLLOW_UP3": "Last follow up!",
                    },
                }],
            )

            assert result["addedCount"] == 1
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            lead = payload["leads"][0]
            assert "customUserFields" in lead
            custom_fields = {f["name"]: f["value"] for f in lead["customUserFields"]}
            assert custom_fields["FOLLOW_UP1"] == "Hey John, following up on our chat."
            assert custom_fields["FOLLOW_UP2"] == "Just checking in again."
            assert custom_fields["FOLLOW_UP3"] == "Last follow up!"

    @pytest.mark.asyncio
    async def test_add_leads_to_list_api_error(self, client):
        """Should raise HeyReachError on API failure."""
        mock_response = Response(
            400,
            json={"error": "Invalid list ID"},
        )

        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(HeyReachError) as exc_info:
                await client.add_leads_to_list(
                    list_id=999999,
                    leads=[{"linkedin_url": "https://www.linkedin.com/in/test"}],
                )

            assert "Failed to add leads to list" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_add_leads_to_list_skips_empty_custom_fields(self, client):
        """Should skip custom fields with empty values."""
        mock_response = Response(
            200,
            json={"addedCount": 1, "updatedCount": 0, "failedCount": 0},
        )

        with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await client.add_leads_to_list(
                list_id=511495,
                leads=[{
                    "linkedin_url": "https://www.linkedin.com/in/johndoe",
                    "custom_fields": {
                        "FOLLOW_UP1": "Has value",
                        "FOLLOW_UP2": "",  # Empty - should be skipped
                        "FOLLOW_UP3": None,  # None - should be skipped
                    },
                }],
            )

            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            lead = payload["leads"][0]
            # Only FOLLOW_UP1 should be present
            assert len(lead["customUserFields"]) == 1
            assert lead["customUserFields"][0]["name"] == "FOLLOW_UP1"


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
                linkedin_url="https://www.linkedin.com/in/testuser",
            )

            assert result["success"] is True
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "/list/RemoveLeadsFromList"
            payload = call_args[1]["json"]
            assert payload["listId"] == 511495
            assert "https://www.linkedin.com/in/testuser" in payload["profileUrls"]

    @pytest.mark.asyncio
    async def test_remove_lead_from_list_api_error(self, client):
        """Should raise HeyReachError on API failure when removing lead."""
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


class TestHeyReachWebhook:
    """Tests for the HeyReach webhook endpoint."""

    @pytest.mark.asyncio
    async def test_webhook_receives_message(self, test_client: AsyncClient):
        """Should receive and process a webhook payload."""
        payload = {
            "lead": {
                "full_name": "John Doe",
                "company_name": "Tech Corp",
                "company_url": "https://techcorp.com",
                "email_address": "john@techcorp.com",
            },
            "recent_messages": [
                {
                    "creation_time": "2024-01-27T10:00:00Z",
                    "message": "I'm interested in your product!",
                }
            ],
            "conversation_id": "conv_123",
            "sender": {"id": 456},
        }

        # Mock the services to avoid actual API calls
        with patch("app.main.process_incoming_message", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = {"draft_id": str(uuid.uuid4())}

            response = await test_client.post("/webhook/heyreach", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "received"
            assert data["conversation_id"] == "conv_123"
            assert data["lead_name"] == "John Doe"

    @pytest.mark.asyncio
    async def test_webhook_receives_full_heyreach_payload(self, test_client: AsyncClient):
        """Should handle full HeyReach payload with all fields."""
        payload = {
            "is_inmail": False,
            "recent_messages": [
                {"creation_time": "2026-01-28T16:28:01Z", "message": "Hi there", "is_reply": True},
            ],
            "conversation_id": "2-ODYwNmVkNDI=",
            "campaign": {"name": "Test Campaign", "id": 123},
            "sender": {"id": 123, "first_name": "John", "full_name": "John Doe"},
            "lead": {
                "id": "TestId",
                "profile_url": "https://www.linkedin.com/in/johndoe",
                "full_name": "John Doe",
                "company_name": "Test Company",
                "position": "CEO",
            },
            "timestamp": "2026-01-28T16:28:01Z",
            "event_type": "every_message_reply_received",
        }

        with patch("app.main.process_incoming_message", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = {"draft_id": str(uuid.uuid4())}

            response = await test_client.post("/webhook/heyreach", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "received"
            assert data["lead_name"] == "John Doe"

    @pytest.mark.asyncio
    async def test_webhook_empty_body(self, test_client: AsyncClient):
        """Should handle empty body gracefully."""
        response = await test_client.post("/webhook/heyreach", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received_raw"


class TestHeyReachOutgoingWebhook:
    """Tests for the HeyReach outgoing message webhook endpoint."""

    @pytest.mark.asyncio
    async def test_outgoing_webhook_receives_message(self, test_client: AsyncClient):
        """Should receive and process an outgoing webhook payload."""
        payload = {
            "lead": {
                "full_name": "Jane Smith",
                "company_name": "Acme Inc",
                "profile_url": "https://www.linkedin.com/in/janesmith",
            },
            "recent_messages": [
                {
                    "creation_time": "2026-02-10T10:00:00Z",
                    "message": "Hi Jane, I wanted to reach out about...",
                }
            ],
            "conversation_id": "conv_outgoing_123",
            "sender": {"id": 789},
        }

        with patch("app.main.process_outgoing_message", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = {"status": "logged", "message_log_id": str(uuid.uuid4())}

            response = await test_client.post("/webhook/heyreach/outgoing", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "received"
            assert data["conversation_id"] == "conv_outgoing_123"
            assert data["lead_name"] == "Jane Smith"
            assert data["direction"] == "outgoing"

    @pytest.mark.asyncio
    async def test_outgoing_webhook_with_campaign_data(self, test_client: AsyncClient):
        """Should parse campaign fields from outgoing webhook payload."""
        payload = {
            "lead": {
                "full_name": "Bob Johnson",
                "company_name": "StartupCo",
                "profile_url": "https://www.linkedin.com/in/bobjohnson",
            },
            "recent_messages": [
                {
                    "creation_time": "2026-02-10T11:00:00Z",
                    "message": "Hey Bob, saw your post about...",
                }
            ],
            "conversation_id": "conv_campaign_456",
            "sender": {"id": 101},
            "campaign": {"id": 42, "name": "Q1 Outreach Campaign"},
        }

        with patch("app.main.process_outgoing_message", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = {"status": "logged", "message_log_id": str(uuid.uuid4())}

            response = await test_client.post("/webhook/heyreach/outgoing", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "received"

            # Verify process_outgoing_message was called with payload containing campaign
            mock_process.assert_called_once()
            called_payload = mock_process.call_args[0][0]
            assert called_payload.campaign is not None
            assert called_payload.campaign.id == 42
            assert called_payload.campaign.name == "Q1 Outreach Campaign"

    @pytest.mark.asyncio
    async def test_outgoing_webhook_get_verification(self, test_client: AsyncClient):
        """GET should return verification response."""
        response = await test_client.get("/webhook/heyreach/outgoing")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "outgoing" in data["message"].lower() or "Outgoing" in data["message"]

    @pytest.mark.asyncio
    async def test_outgoing_webhook_empty_body(self, test_client: AsyncClient):
        """Should handle empty body gracefully."""
        response = await test_client.post("/webhook/heyreach/outgoing", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received_raw"
