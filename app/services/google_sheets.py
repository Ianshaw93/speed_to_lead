"""Google Sheets service for creating shareable gift leads spreadsheets."""

import json
import logging
from datetime import date
from functools import lru_cache

import gspread
from google.oauth2.service_account import Credentials

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsError(Exception):
    """Raised when a Google Sheets operation fails."""


class GoogleSheetsService:
    """Creates and shares Google Sheets for gift leads."""

    def __init__(self) -> None:
        creds_json = settings.google_service_account_json
        if not creds_json:
            raise GoogleSheetsError(
                "GOOGLE_SERVICE_ACCOUNT_JSON environment variable not set"
            )
        creds = Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=SCOPES,
        )
        self._gc = gspread.authorize(creds)
        self._folder_id = settings.google_drive_folder_id or None

    def create_gift_leads_sheet(
        self,
        prospect_name: str,
        leads: list[dict],
    ) -> str:
        """Create a Google Sheet with leads data, shared via link.

        Creates in the configured Drive folder (if set) so the sheet uses
        the folder owner's quota instead of the service account's.

        Args:
            prospect_name: Name of the prospect receiving the leads.
            leads: List of lead dicts.

        Returns:
            The shareable Google Sheet URL.

        Raises:
            GoogleSheetsError: If creation fails.
        """
        try:
            title = f"Leads for {prospect_name} - {date.today()}"
            spreadsheet = self._gc.create(title, folder_id=self._folder_id)

            worksheet = spreadsheet.sheet1
            worksheet.update_title("Leads")

            # Full column set matching CSV output
            headers = [
                "Name",
                "Title",
                "Company",
                "Location",
                "Headline",
                "Activity Score",
                "ICP Reason",
                "LinkedIn",
            ]
            rows = [headers]
            for lead in leads:
                rows.append([
                    lead.get("full_name", ""),
                    lead.get("job_title", ""),
                    lead.get("company_name", ""),
                    lead.get("location", ""),
                    lead.get("headline", ""),
                    str(lead.get("activity_score", "")),
                    lead.get("icp_reason", ""),
                    lead.get("linkedin_url", ""),
                ])

            worksheet.update(range_name="A1", values=rows)

            # Bold header row
            header_range = f"A1:{chr(64 + len(headers))}1"
            worksheet.format(header_range, {"textFormat": {"bold": True}})

            # Auto-resize columns for readability
            worksheet.columns_auto_resize(0, len(headers))

            # Share with anyone who has the link (viewer)
            spreadsheet.share(None, perm_type="anyone", role="reader")

            logger.info(
                f"Created gift leads sheet for {prospect_name}: {spreadsheet.url}"
            )
            return spreadsheet.url

        except Exception as e:
            raise GoogleSheetsError(f"Failed to create gift leads sheet: {e}") from e


@lru_cache
def get_google_sheets_service() -> GoogleSheetsService | None:
    """Get cached GoogleSheetsService instance, or None if not configured."""
    try:
        return GoogleSheetsService()
    except GoogleSheetsError:
        logger.warning("Google Sheets not configured - gift leads will use text-only DMs")
        return None
