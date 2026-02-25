"""One-time script to create gift leads Google Sheets using personal OAuth."""

import csv
import io
import json
import os
import pickle

import psycopg2
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

CLIENT_SECRET = r"C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\speed_to_lead\.tmp\google_oauth_client.json"
TOKEN_FILE = r"C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\speed_to_lead\.tmp\google_token.pickle"
FOLDER_ID = "17YPIjbmRgNAQ61KTON3r8R_yyAdKSJH7"
DB_URL = "postgresql://postgres:FxvzWGNDpTtzlFccSOQKATscwIXJirFA@crossover.proxy.rlwy.net:56267/railway"

HEADERS = ["Name", "Title", "Company", "Location", "Headline", "Activity Score", "ICP Reason", "LinkedIn"]
FIELDS = ["full_name", "job_title", "company_name", "location", "headline", "activity_score", "icp_reason", "linkedin_url"]


def get_credentials():
    """Get or refresh OAuth credentials."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return creds


def get_leads_from_db(query, params=None):
    """Query the production DB for leads."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(query, params)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows


def create_sheet(drive, prospect_name, leads):
    """Create a Google Sheet in the shared folder."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADERS)
    for lead in leads:
        writer.writerow([str(lead.get(f, "") or "") for f in FIELDS])

    csv_bytes = buf.getvalue().encode("utf-8")

    from datetime import date
    title = f"Leads for {prospect_name} - {date.today()}"

    file_metadata = {
        "name": title,
        "parents": [FOLDER_ID],
        "mimeType": "application/vnd.google-apps.spreadsheet",
    }
    media = MediaInMemoryUpload(csv_bytes, mimetype="text/csv", resumable=False)

    file = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    # Share with anyone who has the link
    drive.permissions().create(
        fileId=file["id"],
        body={"type": "anyone", "role": "reader"},
        fields="id",
    ).execute()

    return file.get("webViewLink", f"https://docs.google.com/spreadsheets/d/{file['id']}")


def main():
    print("Authenticating with Google...")
    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)

    # 1. Sumedha - ICP matched leads from pipeline run window
    print("\n1. Sumedha Patwardhan - 12 leads (solopreneurs/coaches)")
    leads = get_leads_from_db("""
        SELECT full_name, job_title, company_name, location, headline,
               activity_score, icp_reason, linkedin_url
        FROM prospects
        WHERE source_type = 'competitor_post'
        AND icp_match = true
        AND created_at BETWEEN '2026-02-19 19:00:00+00' AND '2026-02-19 20:00:00+00'
        ORDER BY activity_score DESC
    """)
    url = create_sheet(drive, "Sumedha Patwardhan", leads)
    print(f"   {len(leads)} leads -> {url}")

    # 2. Dionna - ICP matched leads from pipeline run window
    print("\n2. Dionna Burchell - 15 leads (healthcare/medtech Salesforce)")
    leads = get_leads_from_db("""
        SELECT full_name, job_title, company_name, location, headline,
               activity_score, icp_reason, linkedin_url
        FROM prospects
        WHERE source_type = 'competitor_post'
        AND icp_match = true
        AND created_at BETWEEN '2026-02-19 20:00:00+00' AND '2026-02-19 21:00:00+00'
        ORDER BY activity_score DESC
    """)
    url = create_sheet(drive, "Dionna Burchell", leads)
    print(f"   {len(leads)} leads -> {url}")

    # 3. Richard Fleury - tech founders (SaaS/AI/Cyber) from DB pool
    print("\n3. Richard Fleury - tech founders (SaaS/AI/Cybersecurity)")
    leads = get_leads_from_db("""
        SELECT full_name, job_title, company_name, location, headline,
               activity_score, icp_reason, linkedin_url
        FROM prospects
        WHERE activity_score IS NOT NULL
        AND (job_title ILIKE '%%SaaS%%' OR job_title ILIKE '%%founder%%' OR headline ILIKE '%%SaaS%%'
             OR job_title ILIKE '%%AI %%' OR headline ILIKE '%%AI founder%%'
             OR job_title ILIKE '%%cybersecurity%%' OR headline ILIKE '%%cybersecurity%%'
             OR job_title ILIKE '%%CTO%%' OR job_title ILIKE '%%CEO%%startup%%'
             OR headline ILIKE '%%B2B tech%%' OR headline ILIKE '%%tech startup%%'
             OR headline ILIKE '%%founder%%AI%%' OR headline ILIKE '%%founder%%SaaS%%')
        ORDER BY activity_score DESC
        LIMIT 15
    """)
    url = create_sheet(drive, "Richard Fleury", leads)
    print(f"   {len(leads)} leads -> {url}")

    # 4. Chandra Keyser - AI/sales enablement from DB pool
    print("\n4. Chandra Keyser - AI/sales enablement/voice AI")
    leads = get_leads_from_db("""
        SELECT full_name, job_title, company_name, location, headline,
               activity_score, icp_reason, linkedin_url
        FROM prospects
        WHERE activity_score IS NOT NULL
        AND (job_title ILIKE '%%AI%%' OR headline ILIKE '%%AI%%'
             OR job_title ILIKE '%%sales enablement%%' OR headline ILIKE '%%sales enablement%%'
             OR job_title ILIKE '%%conversational AI%%' OR headline ILIKE '%%conversational AI%%'
             OR job_title ILIKE '%%voice AI%%' OR headline ILIKE '%%voice AI%%'
             OR job_title ILIKE '%%sales automation%%' OR headline ILIKE '%%sales automation%%')
        ORDER BY activity_score DESC
        LIMIT 15
    """)
    url = create_sheet(drive, "Chandra Keyser", leads)
    print(f"   {len(leads)} leads -> {url}")

    print("\nDone! All 4 sheets created in your Gift Leads folder.")


if __name__ == "__main__":
    main()
