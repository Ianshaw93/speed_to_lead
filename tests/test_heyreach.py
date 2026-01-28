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
            "sender": {"id": "li_account_456"},
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
    async def test_webhook_invalid_payload(self, test_client: AsyncClient):
        """Should return 422 for invalid payload."""
        payload = {
            "lead": {"full_name": "John"},
            # Missing required fields: recent_messages, conversation_id, sender
        }

        response = await test_client.post("/webhook/heyreach", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_webhook_empty_body(self, test_client: AsyncClient):
        """Should return 422 for empty body."""
        response = await test_client.post("/webhook/heyreach", json={})
        assert response.status_code == 422
