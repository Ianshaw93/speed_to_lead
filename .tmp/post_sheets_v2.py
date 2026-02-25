"""Post gift leads with Send/Edit buttons to Slack."""

import asyncio
import json
import subprocess
import sys

sys.path.insert(0, r"C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\speed_to_lead")

from slack_sdk.web.async_client import AsyncWebClient
import psycopg2

DB_URL = "postgresql://postgres:FxvzWGNDpTtzlFccSOQKATscwIXJirFA@crossover.proxy.rlwy.net:56267/railway"

# Load Slack creds from Railway
result = subprocess.run(["cmd.exe", "/c", "railway variables --json"], capture_output=True, text=True)
env = json.loads(result.stdout)
SLACK_TOKEN = env["SLACK_BOT_TOKEN"]
CHANNEL_ID = env["SLACK_CHANNEL_ID"]

PROSPECTS = [
    {
        "name": "Sumedha Pats)",
        "sheet_url": "https://docs.google.com/spreadsheets/d/1WKFg1qp88RbEQDGjYzCeG77QSrFYxfnYk1yl8PXbQbY/edit?usp=drivesdk",
        "lead_count": 12,
        "icp": "Small business owners, solopreneurs, coaches",
        "context": "Said 'Awesome' confirming ICP",
        "draft": "Here you go Sumedha - pulled together 12 people in your space showing strong buying signals right now:\n\nhttps://docs.google.com/spreadsheets/d/1WKFg1qp88RbEQDGjYzCeG77QSrFYxfnYk1yl8PXbQbY\n\nHope it helps",
    },
    {
        "name": "Dionna Burchell",
        "sheet_url": "https://docs.google.com/spreadsheets/d/1LiauvufRkmN-vN2njaIEm8FWSu3UyCrtzrbiRb6ruTo/edit?usp=drivesdk",
        "lead_count": 15,
        "icp": "Healthcare/medtech teams using Salesforce",
        "context": "Said: 'healthcare teams and medtech teams that use Salesforce are our target clients'",
        "draft": "Hey Dionna - as promised, here are 15 healthcare and medtech people actively engaging on LinkedIn right now:\n\nhttps://docs.google.com/spreadsheets/d/1LiauvufRkmN-vN2njaIEm8FWSu3UyCrtzrbiRb6ruTo\n\nHope some good connections in there for you",
    },
    {
        "name": "Richard Fleury",
        "sheet_url": "https://docs.google.com/spreadsheets/d/11SDiKdxpiNpJatfp42bVWWb38irwkaGV_F4lFNUV_a4/edit?usp=drivesdk",
        "lead_count": 15,
        "icp": "Tech founders (SaaS/AI/Cybersecurity) who need help selling",
        "context": "Said: 'would like to see what you found'",
        "draft": "Hey Richard - here's what I found. 15 tech founders (SaaS, AI, cybersecurity) active on LinkedIn right now:\n\nhttps://docs.google.com/spreadsheets/d/11SDiKdxpiNpJatfp42bVWWb38irwkaGV_F4lFNUV_a4\n\nLet me know what you think",
    },
    {
        "name": "Chandra Keyser",
        "sheet_url": "https://docs.google.com/spreadsheets/d/1vpR5KMFiHpkkB7PDAsos58Uz8J3IaGSEUucwiT09F68/edit?usp=drivesdk",
        "lead_count": 15,
        "icp": "AI/sales enablement/voice AI leaders",
        "context": "Pitched Sincerity AI - trust signals for sales, hiring",
        "draft": "Hey Chandra - Sincerity AI sounds fascinating, especially for sales enablement.\n\nPulled together 15 people in the AI and sales enablement space who are active on LinkedIn right now:\n\nhttps://docs.google.com/spreadsheets/d/1vpR5KMFiHpkkB7PDAsos58Uz8J3IaGSEUucwiT09F68\n\nHope some are useful",
    },
]


def get_prospect_id(name):
    """Get prospect ID from DB by matching conversation name -> linkedin URL -> prospect."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    # First find conversation
    cur.execute(
        "SELECT linkedin_profile_url FROM conversations WHERE lead_name ILIKE %s ORDER BY updated_at DESC LIMIT 1",
        (f"%{name.split(')')[0]}%",)
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    linkedin_url = row[0]
    # Then find prospect by URL
    cur.execute(
        "SELECT id FROM prospects WHERE linkedin_url = %s LIMIT 1",
        (linkedin_url,)
    )
    row = cur.fetchone()
    conn.close()
    return str(row[0]) if row else None


async def main():
    client = AsyncWebClient(token=SLACK_TOKEN)

    for p in PROSPECTS:
        print(f"\nPosting: {p['name']}...")
        prospect_id = get_prospect_id(p["name"])
        if not prospect_id:
            print(f"  WARNING: No prospect ID found for {p['name']}, posting without buttons")

        sheet_text = f"\n*Sheet:* <{p['sheet_url']}|Open Google Sheet>" if p["sheet_url"] else ""

        button_value = json.dumps({
            "prospect_id": prospect_id or "",
            "sheet_url": p["sheet_url"],
            "draft_dm": p["draft"],
        })

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Gift Leads Ready: {p['name']}", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*ICP:* {p['icp']}\n"
                        f"*Leads:* {p['lead_count']}"
                        f"{sheet_text}\n"
                        f"*Context:* {p['context']}"
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Draft DM:*\n>>>{p['draft']}",
                },
            },
        ]

        if prospect_id:
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Send as is", "emoji": True},
                        "style": "primary",
                        "action_id": "send_gift_leads_as_is",
                        "value": button_value,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Edit & Send", "emoji": True},
                        "action_id": "edit_gift_leads_dm",
                        "value": button_value,
                    },
                ],
            })

        resp = await client.chat_postMessage(
            channel=CHANNEL_ID,
            blocks=blocks,
            text=f"Gift Leads Ready: {p['name']} ({p['lead_count']} leads)",
        )
        print(f"  Posted: {resp['ok']} (prospect_id: {prospect_id or 'NONE'})")

    print("\nDone! Check Slack.")


asyncio.run(main())
