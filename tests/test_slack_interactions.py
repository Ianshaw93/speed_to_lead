"""Tests for Slack interactions endpoint."""

import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


def create_slack_signature(secret: str, timestamp: str, body: str) -> str:
    """Create a valid Slack signature for testing."""
    sig_basestring = f"v0:{timestamp}:{body}"
    return "v0=" + hmac.new(
        secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()


def create_block_actions_payload(action_id: str, draft_id: str, trigger_id: str = "test_trigger") -> dict:
    """Create a Slack block_actions payload."""
    return {
        "type": "block_actions",
        "user": {"id": "U123", "name": "testuser"},
        "trigger_id": trigger_id,
        "response_url": "https://hooks.slack.com/actions/test",
        "message": {"ts": "1234567890.123456"},
        "actions": [
            {
                "action_id": action_id,
                "value": draft_id,
                "type": "button",
            }
        ],
    }


def create_view_submission_payload(draft_id: str, edited_text: str) -> dict:
    """Create a Slack view_submission payload."""
    return {
        "type": "view_submission",
        "user": {"id": "U123", "name": "testuser"},
        "view": {
            "callback_id": f"edit_draft_{draft_id}",
            "private_metadata": draft_id,
            "state": {
                "values": {
                    "draft_input": {
                        "draft_text": {
                            "value": edited_text
                        }
                    }
                }
            }
        },
    }


class TestSlackSignatureVerification:
    """Tests for Slack signature verification."""

    @pytest.mark.asyncio
    async def test_missing_signature_returns_401(self, test_client: AsyncClient):
        """Request without signature should return 401."""
        response = await test_client.post(
            "/slack/interactions",
            content="payload={}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self, test_client: AsyncClient):
        """Request with invalid signature should return 401."""
        timestamp = str(int(time.time()))
        response = await test_client.post(
            "/slack/interactions",
            content="payload={}",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": "v0=invalid_signature",
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_old_timestamp_returns_401(self, test_client: AsyncClient):
        """Request with old timestamp (>5 min) should return 401."""
        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        body = "payload={}"
        signature = create_slack_signature("test_signing_secret", old_timestamp, body)

        response = await test_client.post(
            "/slack/interactions",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": old_timestamp,
                "X-Slack-Signature": signature,
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self, test_client: AsyncClient):
        """Request with valid signature should be processed."""
        timestamp = str(int(time.time()))
        payload = create_block_actions_payload("reject", str(uuid.uuid4()))
        body = f"payload={json.dumps(payload)}"
        signature = create_slack_signature("test_signing_secret", timestamp, body)

        with patch("app.routers.slack.handle_reject", new_callable=AsyncMock) as mock_handler:
            response = await test_client.post(
                "/slack/interactions",
                content=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Slack-Request-Timestamp": timestamp,
                    "X-Slack-Signature": signature,
                },
            )
            # Should return 200 (acknowledgment)
            assert response.status_code == 200


class TestBlockActionsRouting:
    """Tests for routing block_actions to correct handlers."""

    def _make_request_headers(self, body: str) -> dict:
        """Create valid request headers with signature."""
        timestamp = str(int(time.time()))
        signature = create_slack_signature("test_signing_secret", timestamp, body)
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        }

    @pytest.mark.asyncio
    async def test_approve_action_routes_to_handler(self, test_client: AsyncClient):
        """Approve action should route to approve handler."""
        draft_id = str(uuid.uuid4())
        payload = create_block_actions_payload("approve", draft_id)
        body = f"payload={json.dumps(payload)}"

        with patch("app.routers.slack.handle_approve", new_callable=AsyncMock) as mock_handler:
            response = await test_client.post(
                "/slack/interactions",
                content=body,
                headers=self._make_request_headers(body),
            )
            assert response.status_code == 200
            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_action_routes_to_handler(self, test_client: AsyncClient):
        """Edit action should route to edit handler."""
        draft_id = str(uuid.uuid4())
        payload = create_block_actions_payload("edit", draft_id, trigger_id="valid_trigger")
        body = f"payload={json.dumps(payload)}"

        with patch("app.routers.slack.handle_edit", new_callable=AsyncMock) as mock_handler:
            response = await test_client.post(
                "/slack/interactions",
                content=body,
                headers=self._make_request_headers(body),
            )
            assert response.status_code == 200
            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_regenerate_action_routes_to_handler(self, test_client: AsyncClient):
        """Regenerate action should route to regenerate handler."""
        draft_id = str(uuid.uuid4())
        payload = create_block_actions_payload("regenerate", draft_id)
        body = f"payload={json.dumps(payload)}"

        with patch("app.routers.slack.handle_regenerate", new_callable=AsyncMock) as mock_handler:
            response = await test_client.post(
                "/slack/interactions",
                content=body,
                headers=self._make_request_headers(body),
            )
            assert response.status_code == 200
            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_reject_action_routes_to_handler(self, test_client: AsyncClient):
        """Reject action should route to reject handler."""
        draft_id = str(uuid.uuid4())
        payload = create_block_actions_payload("reject", draft_id)
        body = f"payload={json.dumps(payload)}"

        with patch("app.routers.slack.handle_reject", new_callable=AsyncMock) as mock_handler:
            response = await test_client.post(
                "/slack/interactions",
                content=body,
                headers=self._make_request_headers(body),
            )
            assert response.status_code == 200
            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_snooze_1h_action_routes_to_handler(self, test_client: AsyncClient):
        """Snooze 1h action should route to snooze handler."""
        draft_id = str(uuid.uuid4())
        payload = create_block_actions_payload("snooze_1h", draft_id)
        body = f"payload={json.dumps(payload)}"

        with patch("app.routers.slack.handle_snooze", new_callable=AsyncMock) as mock_handler:
            response = await test_client.post(
                "/slack/interactions",
                content=body,
                headers=self._make_request_headers(body),
            )
            assert response.status_code == 200
            mock_handler.assert_called_once()


class TestViewSubmission:
    """Tests for view_submission (modal) handling."""

    def _make_request_headers(self, body: str) -> dict:
        """Create valid request headers with signature."""
        timestamp = str(int(time.time()))
        signature = create_slack_signature("test_signing_secret", timestamp, body)
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        }

    @pytest.mark.asyncio
    async def test_view_submission_routes_to_handler(self, test_client: AsyncClient):
        """View submission should route to modal handler."""
        draft_id = str(uuid.uuid4())
        payload = create_view_submission_payload(draft_id, "Edited draft text")
        body = f"payload={json.dumps(payload)}"

        with patch("app.routers.slack.handle_modal_submit", new_callable=AsyncMock) as mock_handler:
            response = await test_client.post(
                "/slack/interactions",
                content=body,
                headers=self._make_request_headers(body),
            )
            assert response.status_code == 200
            mock_handler.assert_called_once()
