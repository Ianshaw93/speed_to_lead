"""Post gift leads sheets + draft replies to Slack."""

import asyncio
import os
import sys

# Add project to path
sys.path.insert(0, r"C:\Users\IanShaw\localProgramming\smiths\LI_cross_repo\speed_to_lead")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:FxvzWGNDpTtzlFccSOQKATscwIXJirFA@crossover.proxy.rlwy.net:56267/railway")

from slack_sdk.web.async_client import AsyncWebClient

SLACK_TOKEN = None
CHANNEL_ID = None

# Load from Railway env
import subprocess, json
result = subprocess.run(
    ["cmd.exe", "/c", "railway variables --json"],
    capture_output=True, text=True
)
env = json.loads(result.stdout)
SLACK_TOKEN = env.get("SLACK_BOT_TOKEN")
CHANNEL_ID = env.get("SLACK_CHANNEL_ID")

PROSPECTS = [
    {
        "name": "Sumedha Patwardhan",
        "sheet_url": "https://docs.google.com/spreadsheets/d/1WKFg1qp88RbEQDGjYzCeG77QSrFYxfnYk1yl8PXbQbY/edit?usp=drivesdk",
        "lead_count": 12,
        "icp": "Small business owners, solopreneurs, coaches",
        "draft": "Here you go Sumedha - pulled together 12 people in your space showing strong buying signals right now. All small business owners, coaches, and solopreneurs looking to scale with AI:\n\nhttps://docs.google.com/spreadsheets/d/1WKFg1qp88RbEQDGjYzCeG77QSrFYxfnYk1yl8PXbQbY\n\nHope it helps 🤙",
        "context": "She said 'Awesome' and thumbs up confirming ICP",
    },
    {
        "name": "Dionna Burchell",
        "sheet_url": "https://docs.google.com/spreadsheets/d/1LiauvufRkmN-vN2njaIEm8FWSu3UyCrtzrbiRb6ruTo/edit?usp=drivesdk",
        "lead_count": 15,
        "icp": "Healthcare/medtech teams using Salesforce",
        "draft": "Hey Dionna - as promised, here are 15 healthcare and medtech people who are actively engaging on LinkedIn right now. Directors, VPs, ops managers - all in the Salesforce space:\n\nhttps://docs.google.com/spreadsheets/d/1LiauvufRkmN-vN2njaIEm8FWSu3UyCrtzrbiRb6ruTo\n\nHope some good connections in there for you",
        "context": "She confirmed: 'healthcare teams and medtech teams that use Salesforce are our target clients'",
    },
    {
        "name": "Richard Fleury",
        "sheet_url": "https://docs.google.com/spreadsheets/d/11SDiKdxpiNpJatfp42bVWWb38irwkaGV_F4lFNUV_a4/edit?usp=drivesdk",
        "lead_count": 15,
        "icp": "Tech founders (SaaS/AI/Cybersecurity) who need help selling",
        "draft": "Hey Richard - here's what I found. 15 tech founders (SaaS, AI, cybersecurity) who are active on LinkedIn right now and match your ICP:\n\nhttps://docs.google.com/spreadsheets/d/11SDiKdxpiNpJatfp42bVWWb38irwkaGV_F4lFNUV_a4\n\nBuilt a better mousetrap types who could use help scaling. Let me know what you think",
        "context": "He said: 'You got us pegged with the right icp... would like to see what you found'",
    },
    {
        "name": "Chandra Keyser",
        "sheet_url": "https://docs.google.com/spreadsheets/d/1vpR5KMFiHpkkB7PDAsos58Uz8J3IaGSEUucwiT09F68/edit?usp=drivesdk",
        "lead_count": 15,
        "icp": "AI/sales enablement/voice AI leaders",
        "draft": "Hey Chandra - Sincerity AI sounds fascinating, especially for sales enablement.\n\nPulled together 15 people in the AI and sales enablement space who are active on LinkedIn right now - could be good prospects or pilot candidates:\n\nhttps://docs.google.com/spreadsheets/d/1vpR5KMFiHpkkB7PDAsos58Uz8J3IaGSEUucwiT09F68\n\nHope some are useful",
        "context": "She pitched Voicera/Sincerity AI - trust signals for sales, hiring, fraud",
    },
]


async def main():
    client = AsyncWebClient(token=SLACK_TOKEN)

    for p in PROSPECTS:
        print(f"\nPosting: {p['name']}...")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🎁 Gift Leads Ready: {p['name']}", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*ICP:* {p['icp']}\n"
                        f"*Leads:* {p['lead_count']}\n"
                        f"*Sheet:* <{p['sheet_url']}|Open Google Sheet>\n"
                        f"*Context:* {p['context']}"
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Draft DM to send:*\n```{p['draft']}```",
                },
            },
        ]

        resp = await client.chat_postMessage(
            channel=CHANNEL_ID,
            blocks=blocks,
            text=f"Gift Leads Ready: {p['name']} ({p['lead_count']} leads)",
        )
        print(f"  Posted: {resp['ok']}")

    print("\nDone! Check Slack.")


asyncio.run(main())
