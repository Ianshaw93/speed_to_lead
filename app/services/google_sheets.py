"""Google Sheets service for creating shareable gift leads spreadsheets."""

import csv
import io
import json
import logging
from datetime import date
from functools import lru_cache

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
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

    Uses OAuth refresh token from a personal Google account to avoid
    the 0-byte Drive storage quota on service accounts.
    """

    def __init__(self) -> None:
        refresh_token = settings.google_oauth_refresh_token
        client_id = settings.google_oauth_client_id
        client_secret = settings.google_oauth_client_secret
        if not all([refresh_token, client_id, client_secret]):
            raise GoogleSheetsError(
                "Google OAuth credentials not configured "
                "(GOOGLE_OAUTH_REFRESH_TOKEN, GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET)"
            )

        self._creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        self._creds.refresh(Request())
        self._drive = build("drive", "v3", credentials=self._creds)
        self._folder_id = settings.google_drive_folder_id or None

    def _ensure_valid_creds(self) -> None:
        """Refresh the token if expired."""
        if not self._creds.valid:
            self._creds.refresh(Request())

    def create_gift_leads_sheet(
        self,
        prospect_name: str,
        leads: list[dict],
    ) -> str:
        """Create a Google Sheet by uploading CSV with conversion.

        Args:
            prospect_name: Name of the prospect receiving the leads.
            leads: List of lead dicts.

        Returns:
            The shareable Google Sheet URL.

        Raises:
            GoogleSheetsError: If creation fails.
        """
        try:
            self._ensure_valid_creds()

            title = f"Leads for {prospect_name} - {date.today()}"

            # Build CSV in memory
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(HEADERS)
            for lead in leads:
                writer.writerow([str(lead.get(f, "") or "") for f in FIELD_MAP])

            csv_bytes = buf.getvalue().encode("utf-8")

            # Upload CSV to folder, converting to Google Sheets
            file_metadata = {
                "name": title,
                "mimeType": "application/vnd.google-apps.spreadsheet",
            }
            if self._folder_id:
                file_metadata["parents"] = [self._folder_id]

            media = MediaInMemoryUpload(
                csv_bytes, mimetype="text/csv", resumable=False,
            )

            file = self._drive.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
            ).execute()

            sheet_url = file.get(
                "webViewLink",
                f"https://docs.google.com/spreadsheets/d/{file['id']}",
            )

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
    except GoogleSheetsError as e:
        logger.warning(f"Google Sheets not configured - {e}")
        return None
