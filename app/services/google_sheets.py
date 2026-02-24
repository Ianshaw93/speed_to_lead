"""Google Sheets service for creating shareable gift leads spreadsheets."""

import json
import logging
from datetime import date
from functools import lru_cache

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

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
        self._creds = Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=SCOPES,
        )
        self._gc = gspread.authorize(self._creds)
        self._sheets_api = build("sheets", "v4", credentials=self._creds)
        self._drive_api = build("drive", "v3", credentials=self._creds)
        self._folder_id = settings.google_drive_folder_id or None

    def create_gift_leads_sheet(
        self,
        prospect_name: str,
        leads: list[dict],
    ) -> str:
        """Create a Google Sheet with leads data, shared via link.

        Uses Sheets API to create, then Drive API to move to folder and share.

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

            # Build full data including headers
            headers = [
                "Name", "Title", "Company", "Location",
                "Headline", "Activity Score", "ICP Reason", "LinkedIn",
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

            # Create spreadsheet with data in one API call via Sheets API
            body = {
                "properties": {"title": title},
                "sheets": [{
                    "properties": {"title": "Leads"},
                    "data": [{
                        "startRow": 0,
                        "startColumn": 0,
                        "rowData": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": cell},
                                        **({"userEnteredFormat": {"textFormat": {"bold": True}}}
                                           if row_idx == 0 else {}),
                                    }
                                    for cell in row
                                ]
                            }
                            for row_idx, row in enumerate(rows)
                        ],
                    }],
                }],
            }

            result = self._sheets_api.spreadsheets().create(body=body).execute()
            spreadsheet_id = result["spreadsheetId"]
            sheet_url = result["spreadsheetUrl"]

            # Move to shared folder if configured
            if self._folder_id:
                try:
                    # Get current parent, move to target folder
                    file = self._drive_api.files().get(
                        fileId=spreadsheet_id, fields="parents"
                    ).execute()
                    prev_parents = ",".join(file.get("parents", []))
                    self._drive_api.files().update(
                        fileId=spreadsheet_id,
                        addParents=self._folder_id,
                        removeParents=prev_parents,
                        fields="id, parents",
                    ).execute()
                except Exception as e:
                    logger.warning(f"Could not move sheet to folder: {e}")

            # Share with anyone who has the link (viewer)
            self._drive_api.permissions().create(
                fileId=spreadsheet_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()

            logger.info(f"Created gift leads sheet for {prospect_name}: {sheet_url}")
            return sheet_url

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
