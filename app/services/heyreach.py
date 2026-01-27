"""HeyReach API client for sending LinkedIn messages."""

from typing import Any

import httpx

from app.config import settings


class HeyReachError(Exception):
    """Custom exception for HeyReach API errors."""

    pass


class HeyReachClient:
    """Client for interacting with the HeyReach API."""

    BASE_URL = "https://api.heyreach.io/api/v1"

    def __init__(self, api_key: str | None = None):
        """Initialize the HeyReach client.

        Args:
            api_key: HeyReach API key. Defaults to settings value.
        """
        self._api_key = api_key or settings.heyreach_api_key
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def send_message(self, lead_id: str, message: str) -> dict[str, Any]:
        """Send a message to a lead via HeyReach.

        Args:
            lead_id: The HeyReach lead ID.
            message: The message content to send.

        Returns:
            API response with success status and message ID.

        Raises:
            HeyReachError: If the API call fails.
        """
        try:
            response = await self._client.post(
                "/messages/send",
                json={
                    "leadId": lead_id,
                    "message": message,
                },
            )

            if response.status_code != 200:
                error_detail = response.json().get("error", "Unknown error")
                raise HeyReachError(f"Failed to send message: {error_detail}")

            return response.json()

        except HeyReachError:
            raise
        except Exception as e:
            raise HeyReachError(f"HeyReach API error: {e}") from e

    async def get_lead_info(self, lead_id: str) -> dict[str, Any]:
        """Get information about a lead.

        Args:
            lead_id: The HeyReach lead ID.

        Returns:
            Lead information including name, title, company.

        Raises:
            HeyReachError: If the API call fails.
        """
        try:
            response = await self._client.get(f"/leads/{lead_id}")

            if response.status_code != 200:
                error_detail = response.json().get("error", "Unknown error")
                raise HeyReachError(f"Failed to get lead info: {error_detail}")

            return response.json()

        except HeyReachError:
            raise
        except Exception as e:
            raise HeyReachError(f"HeyReach API error: {e}") from e

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()


# Global client instance
_client: HeyReachClient | None = None


def get_heyreach_client() -> HeyReachClient:
    """Get or create the HeyReach client singleton."""
    global _client
    if _client is None:
        _client = HeyReachClient()
    return _client
