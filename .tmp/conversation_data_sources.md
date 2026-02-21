# Conversation Data Sources & Backfill Guide

## Overview

Each LinkedIn conversation has two data representations in the database:

1. **`Conversation.conversation_history`** (JSON column) — the full conversation thread as a list of `{role, content, time}` dicts. This is what gets passed to the AI for draft generation.
2. **`MessageLog`** table — individual message rows with a `direction` enum (`INBOUND` / `OUTBOUND`), linked to a conversation via `conversation_id`.

The `MessageLog` table is the **source of truth** for who sent what because each entry has a reliable `direction` field set from the HeyReach webhook's `is_reply` boolean at ingestion time.

## Data Sources

### 1. HeyReach Webhooks (primary, real-time)

HeyReach fires webhooks for both inbound (lead replies) and outbound (our messages) events.

**Inbound webhook** (`POST /webhook/heyreach`):
- Triggered when a lead sends a message
- Payload includes `is_reply: true` on the triggering message
- Payload field `recent_messages` contains the conversation thread — each message has an `is_reply` boolean
- Creates a `MessageLog` with `direction=INBOUND`
- Updates `Conversation.conversation_history` using `is_reply` to assign roles

**Outbound webhook** (`POST /webhook/heyreach-outgoing`):
- Triggered when we send a message (via HeyReach campaigns or manual sends)
- Iterates all `recent_messages` in the payload
- Uses `is_reply` to determine direction: `True` = INBOUND, `False` = OUTBOUND
- Creates `MessageLog` entries with correct direction and campaign info
- Deduplicates against existing messages (same content + direction + conversation)

**Important**: Full webhook coverage (both inbound and outbound) only started relatively recently. Older conversations may have incomplete `MessageLog` data or missing outbound entries.

### 2. MessageLog Table (source of truth for direction)

| Column | Purpose |
|--------|---------|
| `conversation_id` | FK to `conversations.id` |
| `direction` | `INBOUND` (lead sent) or `OUTBOUND` (we sent) |
| `content` | Message text |
| `sent_at` | Timestamp |
| `campaign_id` | HeyReach campaign ID (outbound only) |
| `campaign_name` | HeyReach campaign name (outbound only) |

The `direction` field is set correctly at write time from `is_reply`, making MessageLog reliable even when `conversation_history` had bugs.

### 3. Conversation.conversation_history (JSON, passed to AI)

```json
[
  {"role": "you", "content": "Hey, saw your post about AI", "time": "2026-02-15T10:00:00"},
  {"role": "lead", "content": "Thanks! Tell me more", "time": "2026-02-15T14:30:00"}
]
```

This JSON is what the AI sees when generating draft replies. The `role` field tells the AI who said what.

**Bug (now fixed):** Prior to Feb 2026, all messages were stored with `role: "lead"` regardless of sender, because the history-building code didn't check `is_reply`.

## Backfill Process

The `/admin/backfill-history-roles` endpoint fixes historical `conversation_history` data using a two-phase approach:

### Phase 1: Content Matching
For each conversation where all roles are `"lead"`:
1. Load all `MessageLog` entries for that conversation
2. Build a set of outbound message contents (from MessageLog where `direction=OUTBOUND`)
3. For each entry in `conversation_history`, check if its content matches an outbound message
4. If match found: set `role: "you"`, otherwise keep `role: "lead"`

This works when the conversation_history contains messages that are also in MessageLog.

### Phase 2: Full Rebuild from MessageLog
If content matching produces no changes (i.e., no outbound content matched), the history is likely incomplete — it may only contain lead messages while outbound messages were never stored in the history JSON.

In this case:
1. Sort all `MessageLog` entries by `sent_at`
2. Rebuild `conversation_history` entirely from MessageLog data
3. Each entry gets `role` from MessageLog's `direction` field

### Backfill Results (Feb 2026)
- **185 conversations**: Fixed via content matching (had outbound messages in history, just wrong role)
- **85 conversations**: Rebuilt entirely from MessageLog (history was missing outbound messages)
- **0 skipped**: All conversations with history had MessageLog data available

### Running the Backfill

```bash
curl -X POST https://speedtolead-production.up.railway.app/admin/backfill-history-roles \
  -H "Authorization: Bearer $SECRET_KEY"
```

The endpoint is idempotent — conversations already containing `role: "you"` are skipped.

## How New Messages Get Correct Roles Now

After the fix in `app/main.py`, the `process_incoming_message` function builds history like this:

```python
history = [
    {
        "role": "lead" if msg.is_reply else "you",
        "content": msg.message,
        "time": msg.creation_time,
    }
    for msg in payload.all_recent_messages
]
```

The key field is `msg.is_reply` from the HeyReach webhook payload — `True` means the lead sent it, `False` means we sent it.
