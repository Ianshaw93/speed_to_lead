"""HeyReach API client for sending LinkedIn messages."""

from typing import Any

import httpx

from app.config import settings


class HeyReachError(Exception):
    """Custom exception for HeyReach API errors."""

    pass


class HeyReachClient:
    """Client for interacting with the HeyReach API."""

    BASE_URL = "https://api.heyreach.io/api/public"

    def __init__(self, api_key: str | None = None):
        """Initialize the HeyReach client.

        Args:
            api_key: HeyReach API key. Defaults to settings value.
        """
        self._api_key = api_key or settings.heyreach_api_key
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "X-API-KEY": self._api_key,
                "Content-Type": "application/json",
                "Accept": "text/plain",
            },
            timeout=30.0,
        )

    async def send_message(
        self,
        conversation_id: str,
        linkedin_account_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Send a message to a lead via HeyReach.

        Args:
            conversation_id: The HeyReach conversation ID.
            linkedin_account_id: The LinkedIn account ID (sender.id from webhook).
            message: The message content to send.

        Returns:
            API response with success status.

        Raises:
            HeyReachError: If the API call fails.
        """
        try:
            response = await self._client.post(
                "/inbox/SendMessage",
                json={
                    "message": message,
                    "subject": message,  # Often same as message for LinkedIn
                    "conversationId": conversation_id,
                    "linkedInAccountId": linkedin_account_id,
                },
            )

            if response.status_code != 200:
                try:
                    error_detail = response.json().get("error", response.text)
                except Exception:
                    error_detail = response.text
                raise HeyReachError(f"Failed to send message: {error_detail}")

            # Response may be plain text or JSON
            try:
                return response.json()
            except Exception:
                return {"success": True, "response": response.text}

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
