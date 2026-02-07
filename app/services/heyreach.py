"""HeyReach API client for sending LinkedIn messages."""

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


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

    async def add_leads_to_list(
        self,
        list_id: int,
        leads: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add leads to a HeyReach list with custom fields.

        Args:
            list_id: The HeyReach list ID to add leads to.
            leads: List of lead dictionaries. Each lead should have:
                - linkedin_url: LinkedIn profile URL (required)
                - first_name: Lead's first name (optional)
                - last_name: Lead's last name (optional)
                - company_name: Lead's company (optional)
                - job_title: Lead's job title (optional)
                - custom_fields: Dict of custom field names to values (optional)
                  e.g., {"FOLLOW_UP1": "msg1", "FOLLOW_UP2": "msg2"}

        Returns:
            API response with addedCount, updatedCount, failedCount.

        Raises:
            HeyReachError: If the API call fails.
        """
        formatted_leads = []
        for lead in leads:
            formatted = {
                "profileUrl": lead.get("linkedin_url", ""),
            }

            # Add optional standard fields
            if lead.get("first_name"):
                formatted["firstName"] = lead["first_name"]
            if lead.get("last_name"):
                formatted["lastName"] = lead["last_name"]
            if lead.get("company_name"):
                formatted["companyName"] = lead["company_name"]
            if lead.get("job_title"):
                formatted["position"] = lead["job_title"]

            # Add custom fields
            custom_fields = lead.get("custom_fields", {})
            if custom_fields:
                custom_user_fields = []
                for field_name, field_value in custom_fields.items():
                    if field_value:  # Only add non-empty values
                        custom_user_fields.append({
                            "name": field_name,
                            "value": str(field_value),
                        })
                if custom_user_fields:
                    formatted["customUserFields"] = custom_user_fields

            formatted_leads.append(formatted)

        try:
            response = await self._client.post(
                "/list/AddLeadsToListV2",
                json={
                    "listId": list_id,
                    "leads": formatted_leads,
                },
            )

            if response.status_code != 200:
                try:
                    error_detail = response.json().get("error", response.text)
                except Exception:
                    error_detail = response.text
                raise HeyReachError(f"Failed to add leads to list: {error_detail}")

            try:
                result = response.json()
                logger.info(
                    f"Added leads to list {list_id}: "
                    f"added={result.get('addedCount', 0)}, "
                    f"updated={result.get('updatedCount', 0)}, "
                    f"failed={result.get('failedCount', 0)}"
                )
                return result
            except Exception:
                return {"success": True, "response": response.text}

        except HeyReachError:
            raise
        except Exception as e:
            raise HeyReachError(f"HeyReach API error: {e}") from e

    async def remove_lead_from_list(
        self,
        list_id: int,
        linkedin_url: str,
    ) -> dict[str, Any]:
        """Remove a lead from a HeyReach list.

        Args:
            list_id: The HeyReach list ID to remove the lead from.
            linkedin_url: LinkedIn profile URL of the lead to remove.

        Returns:
            API response with success status.

        Raises:
            HeyReachError: If the API call fails.
        """
        try:
            response = await self._client.post(
                "/list/RemoveLeadsFromList",
                json={
                    "listId": list_id,
                    "profileUrls": [linkedin_url],
                },
            )

            if response.status_code != 200:
                try:
                    error_detail = response.json().get("error", response.text)
                except Exception:
                    error_detail = response.text
                raise HeyReachError(f"Failed to remove lead from list: {error_detail}")

            try:
                result = response.json()
                logger.info(
                    f"Removed lead from list {list_id}: "
                    f"linkedin_url={linkedin_url}, response={result}"
                )
                return result
            except Exception:
                return {"success": True, "response": response.text}

        except HeyReachError:
            raise
        except Exception as e:
            raise HeyReachError(f"HeyReach API error: {e}") from e

    async def stop_lead_in_campaign(
        self,
        campaign_id: int,
        linkedin_url: str,
    ) -> dict[str, Any]:
        """Stop a lead from receiving further messages in a campaign.

        Args:
            campaign_id: The HeyReach campaign ID.
            linkedin_url: LinkedIn profile URL of the lead to stop.

        Returns:
            API response with success status.

        Raises:
            HeyReachError: If the API call fails.
        """
        try:
            response = await self._client.post(
                "/campaign/StopLeadInCampaign",
                json={
                    "campaignId": campaign_id,
                    "leadUrl": linkedin_url,
                },
            )

            if response.status_code == 404:
                logger.warning(
                    f"Lead not found in campaign {campaign_id}: {linkedin_url}"
                )
                return {"success": False, "error": "Lead not found in campaign"}

            if response.status_code != 200:
                try:
                    error_detail = response.json().get("error", response.text)
                except Exception:
                    error_detail = response.text
                raise HeyReachError(f"Failed to stop lead in campaign: {error_detail}")

            logger.info(
                f"Stopped lead in campaign {campaign_id}: linkedin_url={linkedin_url}"
            )
            return {"success": True}

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
