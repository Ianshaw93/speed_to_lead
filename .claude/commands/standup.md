# Morning Standup

Daily snapshot of yesterday's draft activity, QA performance, funnel progression, and conversations. Like a sales standup but data-driven.

## Instructions

### 1. Run the standup script

```bash
python scripts/standup.py --output .tmp/standup.md
```

For a specific date:
```bash
python scripts/standup.py --date 2026-02-20 --output .tmp/standup.md
```

### 2. Read and present the report

Read `.tmp/standup.md` and present each section with brief commentary:

- **Draft Activity** — Flag if volume is unusually low/high
- **Human Edits vs AI** — Comment on AI accuracy trend
- **QA Performance** — Note any blocks or low scores
- **Funnel Progression** — Celebrate any bookings or calendar sends
- **Notable Conversations** — Highlight interesting exchanges
- **Learnings** — Summarize what the system is learning

### 3. Suggest actions

Based on the data, suggest 1-3 actions for today:
- If AI accuracy is low, suggest prompt tuning (use `/test-prompts`)
- If QA is blocking too many, check QA guidelines
- If funnel is stalled, check for snoozed or pending drafts
- If no learnings, consider running the learning agent

## When to use

- Start of each workday to review yesterday's activity
- After prompt changes to see next-day impact
- Weekly reviews (run for each day of the week)
