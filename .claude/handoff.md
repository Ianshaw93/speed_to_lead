# Speed to Lead — Handoff Context

## What We're Doing

Fixing the AI draft quality system so that auto-generated LinkedIn reply drafts are actually usable. This is the **#1 business lever** right now — 93 drafts sit unanswered in Slack because none of them are good enough to send.

## Strategic Context (Dickey Bush Framework Analysis)

We applied the Dickey Bush goal-setting framework to evaluate 5 business levers:

| Lever | Impact | Effort | Decision |
|-------|--------|--------|----------|
| **AI draft quality (speed to lead)** | **5** | **3** | **THIS WEEK'S FOCUS** |
| Content (5 posts/week) | 2 | 3 | Not now — top-of-funnel, doesn't fix the leak |
| Lead magnets | 2 | 3 | Not now — same reason |
| Engagement/commenting | 3 | 3 | Next priority after drafts |
| Buying signals (targeting) | 3 | 1 | Already running, A/B test in progress |

**Why this lever:** The system is demand-constrained with an **efficiency bottleneck**, not a volume bottleneck. There are 1,723 prospects, 270 conversations, 63 positive replies — but only 9 pitched and 2 booked. Adding more volume (content, lead magnets) pours water into a leaky bucket.

## Current Funnel Numbers (from prod DB, 2026-02-21)

```
1,723 Prospects
  270 Conversations (got a reply)
   63 Tagged Positive Reply
    9 Pitched
    3 Calendar Sent
    2 Booked
   93 Pending (unanswered) drafts
```

- **Draft AI generation:** instant
- **Human review/send time:** median 1.5 hours, average 8.9 hours
- **AI drafts actually good enough to send as-is:** 0 out of 136

## Diagnosis: Why Every Draft Is Unusable

### Bug 1: All messages labelled as "lead" role
**File:** `app/main.py:168-171`
```python
history = [
    {"role": "lead", "content": msg.message, "time": msg.creation_time}
    for msg in payload.all_recent_messages
]
```
Every message (ours AND theirs) gets `role: "lead"`. The AI literally can't tell who said what. The `is_reply` field exists on `HeyReachMessage` but is ignored. Fix: `"role": "lead" if msg.is_reply else "you"`.

### Bug 2: No prospect context passed to AI
Available but unused data:
- `payload.lead.position` (job title)
- `payload.lead.company_name`
- `payload.lead.location`
- `payload.lead.summary` / `payload.lead.about`
- `payload.lead.personalized_message` (the outreach message they received)
- `triggering_msg` (computed at line 199 but only used for Slack display)
- `is_first_reply` (computed at line 213 but only used for Slack display)

The AI is told to "Reference something specific from their profile" but has NO profile data.

### Problem 3: Generic prompts replaced the good ones
**Active prompt** (`app/prompts/stages/positive_reply.py`): Generic. "Be warm and conversational." No few-shot examples, no voice, no qualifying questions.

**Dead code prompt** (`app/prompts/sales_assistant.py:19-70`): The original, opinionated prompt with:
- Specific qualifying questions ("Is LinkedIn a big client acq channel for you?")
- Text-message style instructions
- 4 real few-shot conversation examples
- Clear output format (2-3 separate short messages per line)

This was the original approach, got left behind when the stage-routing system was built.

### Bug 4: Regeneration assigns DraftResult to string field
**File:** `app/routers/slack.py:440`
```python
draft.ai_draft = new_draft  # new_draft is DraftResult object, not string
```
Should be `draft.ai_draft = new_draft.reply`.

## The Fix (Plan at `.claude/plans/hashed-dreaming-micali.md`)

1. **Fix history role bug** — `main.py:168` use `msg.is_reply` to set correct role
2. **Create shared prompt utility** — `app/prompts/utils.py` (new) for history + context formatting
3. **Thread `lead_context`** through the generation chain: `main.py` → `deepseek.py` → stage prompts
4. **Update all prompt templates** to include lead context (company, title, triggering message)
5. **Replace positive_reply system prompt** with the persona-driven version from `sales_assistant.py`
6. **Fix regeneration bugs** in `slack.py` — DraftResult bug + add context to regen calls
7. **Tests** — update `test_deepseek.py`, create new `test_prompts.py`

## Key Files

| File | Purpose |
|------|---------|
| `app/main.py:155-280` | `process_incoming_message()` — webhook → draft flow |
| `app/services/deepseek.py` | Two-pass AI: stage detection → draft generation |
| `app/prompts/stages/positive_reply.py` | Active (generic) positive reply prompt |
| `app/prompts/sales_assistant.py` | Dead code with the GOOD prompt (few-shot examples, voice) |
| `app/prompts/stage_detector.py` | Stage detection prompt |
| `app/prompts/stages/*.py` | All stage-specific prompt modules |
| `app/routers/slack.py:410-466` | `_process_regenerate()` — has DraftResult bug |
| `tests/test_deepseek.py` | Existing tests for draft generation |

## Data Access

### Production DB
- Host: `crossover.proxy.rlwy.net:56267`
- User: `postgres`, DB: `railway`
- Password: in Railway variables (`cmd.exe /c "railway variables -s Postgres"`)
- Funnel query script: `.tmp/fm.py`

### Useful Queries
```sql
-- Pending drafts
SELECT d.ai_draft, c.lead_name, c.conversation_history
FROM drafts d JOIN conversations c ON d.conversation_id = c.id
WHERE d.status = 'pending' ORDER BY d.created_at DESC;

-- Approved drafts with what was actually sent
SELECT d.ai_draft, ml.content as actually_sent, c.lead_name
FROM drafts d
JOIN conversations c ON d.conversation_id = c.id
JOIN message_log ml ON ml.conversation_id = c.id
  AND ml.direction = 'outbound' AND ml.sent_at >= d.created_at
WHERE d.status = 'approved' ORDER BY d.created_at DESC;
```

## Status

- [x] Strategic analysis (Dickey Bush framework) — completed
- [x] Diagnosis of all 4 root causes — completed
- [x] Plan written at `.claude/plans/hashed-dreaming-micali.md`
- [ ] Implementation in progress (separate Claude instance)
- [ ] Verification
- [ ] Deploy & test with real webhook

## What's Next After Draft Quality

| Priority | Lever | Notes |
|----------|-------|-------|
| 1 | **AI draft quality** | In progress (separate instance) |
| 2 | **Content — 5 posts/week** | Standard going forward (was 2/wk last week) |
| 3 | **Engagement/commenting** | Next engineering focus after drafts ship |
| 4 | **A/B test initial messages** | High-leverage once reply pipeline is flowing — test different openers, tones, hooks to lift reply rate and positive reply quality |
| 5 | Lead magnets | Deferred — top-of-funnel, doesn't fix the leak |
