"""Google Sheets service for creating shareable gift leads spreadsheets."""

import csv
import io
import json
import logging
from datetime import date
from functools import lru_cache

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Name", "Title", "Company", "Location",
    "Headline", "Activity Score", "ICP Reason", "LinkedIn",
]

FIELD_MAP = [
    "full_name", "job_title", "company_name", "location",
    "headline", "activity_score", "icp_reason", "linkedin_url",
]


class GoogleSheetsError(Exception):
    """Raised when a Google Sheets operation fails."""


class GoogleSheetsService:
    """Creates and shares Google Sheets for gift leads.

    Uploads CSV to a shared Drive folder and converts to Google Sheets.
    The file counts against the folder owner's quota, not the service account's.
    """

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
        self._drive = build("drive", "v3", credentials=self._creds)
        self._folder_id = settings.google_drive_folder_id or None
        if not self._folder_id:
            raise GoogleSheetsError(
                "GOOGLE_DRIVE_FOLDER_ID not set - required for sheet creation"
            )

    def create_gift_leads_sheet(
        self,
        prospect_name: str,
        leads: list[dict],
    ) -> str:
        """Create a Google Sheet by uploading CSV and converting.

        Uploads a CSV to the shared Drive folder with conversion to
        Google Sheets format. Quota is charged to the folder owner.

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

            # Build CSV in memory
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(HEADERS)
            for lead in leads:
                writer.writerow([str(lead.get(f, "")) for f in FIELD_MAP])

            csv_bytes = buf.getvalue().encode("utf-8")

            # Upload CSV to shared folder, converting to Google Sheets
            file_metadata = {
                "name": title,
                "parents": [self._folder_id],
                "mimeType": "application/vnd.google-apps.spreadsheet",
            }
            media = MediaInMemoryUpload(
                csv_bytes,
                mimetype="text/csv",
                resumable=False,
            )

            file = self._drive.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
            ).execute()

            sheet_url = file.get("webViewLink", f"https://docs.google.com/spreadsheets/d/{file['id']}")

            # Share with anyone who has the link (viewer)
            self._drive.permissions().create(
                fileId=file["id"],
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
