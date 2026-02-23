# Plan: Automate Gift Leads with Google Sheets

## Goal
Replace the manual multi-step gift leads flow with an autonomous system that:
1. Creates a Google Sheet with matching leads (shared via "anyone with link")
2. Sends a LinkedIn DM containing the Google Sheet link via HeyReach API
3. Optionally auto-triggers for buying signal prospects who reply positively

## Current Flow (4 human steps)
1. Human clicks "Gift Leads" button in Slack
2. Human confirms ICP & keywords in modal
3. Human reviews results, clicks "Send Leads to [Name]"
4. Human edits DM text in modal, clicks "Send via LinkedIn"

## Proposed Flow (1 human step OR fully autonomous)

### Semi-Autonomous (1-click approve):
1. Positive reply detected → auto-extract ICP/keywords → auto-search leads → auto-create Google Sheet
2. Post to Slack: "Found 12 leads for John Smith. [Sheet link]. [Approve & Send]"
3. Human clicks "Approve & Send" → DM sent with Sheet link

### Fully Autonomous (buying signal prospects only):
1. Positive reply detected from BUYING_SIGNAL prospect
2. Auto-extract ICP from `personalized_message` / `icp_reason`
3. Auto-search DB for matching leads
4. Auto-create Google Sheet with leads
5. Auto-send DM with Sheet link via HeyReach
6. Post to Slack for awareness: "Sent 12 gift leads to John Smith [Sheet link]"

---

## Implementation Steps

### Step 1: Add Google Sheets dependency
- Add `gspread` and `google-auth` to `pyproject.toml`
- These are the standard libraries for Google Sheets API access

### Step 2: Create Google Sheets service (`app/services/google_sheets.py`)

New service class `GoogleSheetsService`:

```python
class GoogleSheetsService:
    """Creates and shares Google Sheets for gift leads."""

    def __init__(self):
        # Auth via service account JSON (from env var)
        creds = service_account.Credentials.from_service_account_info(
            json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
            scopes=["https://www.googleapis.com/auth/spreadsheets",
                     "https://www.googleapis.com/auth/drive"]
        )
        self.gc = gspread.authorize(creds)

    async def create_gift_leads_sheet(
        self,
        prospect_name: str,
        leads: list[dict],  # full_name, job_title, company_name, activity_score, linkedin_url
    ) -> str:
        """Create a Google Sheet with leads data, shared via link.

        Returns: The shareable Google Sheet URL
        """
        # 1. Create spreadsheet
        title = f"Gift Leads for {prospect_name} - {date.today()}"
        spreadsheet = self.gc.create(title)

        # 2. Populate with headers + data
        worksheet = spreadsheet.sheet1
        worksheet.update("A1", [
            ["Name", "Title", "Company", "Activity Score", "LinkedIn"],
            *[
                [l["full_name"], l["job_title"], l["company_name"],
                 l["activity_score"], l["linkedin_url"]]
                for l in leads
            ]
        ])

        # 3. Format header row (bold)
        worksheet.format("A1:E1", {"textFormat": {"bold": True}})

        # 4. Share with "anyone with link" (viewer)
        spreadsheet.share(None, perm_type="anyone", role="reader")

        # 5. Return URL
        return spreadsheet.url
```

### Step 3: Add environment variable
- `GOOGLE_SERVICE_ACCOUNT_JSON` — the full service account JSON as a string
- Add to Railway environment
- Document in `.env.example`

### Step 4: Modify `_process_gift_leads_with_send` to create Sheet

In `app/routers/slack.py`, update the background task to:
1. After finding matching leads, call `GoogleSheetsService.create_gift_leads_sheet()`
2. Store the Sheet URL alongside the leads data
3. Include Sheet URL in the Slack results message
4. Pass Sheet URL through to the DM composition

### Step 5: Modify DM composition to include Sheet link

Update `handle_send_gift_leads_dm` to compose the DM as:
```
Hey {first_name}, I pulled together some people in your space
that might be worth connecting with:

{google_sheet_url}

Let me know if any of these are useful!
```

Instead of the current text list (which is long and hard to read in LinkedIn).

### Step 6: Add auto-trigger for buying signal prospects (optional, Phase 2)

Create a new handler that fires when:
- A conversation's `funnel_stage` moves to `POSITIVE_REPLY`
- AND the prospect has `source_type == BUYING_SIGNAL`

This would call the same pipeline (search → Sheet → send) without human intervention.

### Step 7: Add "Approve & Send" one-click button for non-buying-signal prospects

Modify the Slack notification to include a single button that:
- Shows the Sheet link and lead count
- Clicking it immediately sends the DM (no modal)

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `pyproject.toml` | Modify | Add `gspread`, `google-auth` |
| `app/services/google_sheets.py` | Create | Google Sheets service |
| `app/routers/slack.py` | Modify | Update gift leads flow to use Sheets |
| `app/services/slack.py` | Modify | Update result display to show Sheet link |
| `.env.example` | Modify | Document `GOOGLE_SERVICE_ACCOUNT_JSON` |
| `tests/test_gift_leads_flow.py` | Modify | Update tests for Sheet integration |

## Prerequisites
- Google Cloud project with Sheets API & Drive API enabled
- Service account created with appropriate permissions
- Service account JSON key added to Railway env vars

## Risk Assessment
- **Low risk**: Google Sheets API is well-documented and stable
- **Low risk**: `gspread` is the most popular Python Sheets library (7k+ GitHub stars)
- **Medium risk**: Service account needs setup in Google Cloud Console (manual step)
- **No risk to existing flow**: Changes are additive; existing text DM still works as fallback
