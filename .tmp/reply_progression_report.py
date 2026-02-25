"""
Report: Which edited/sent replies are progressing conversations through the funnel?

Analyses:
1. All approved drafts with their sent messages
2. Whether the conversation progressed after sending
3. The actual message thread showing what worked
4. Breakdown by funnel stage progression
"""
import psycopg2
import json
import textwrap
from datetime import datetime

conn = psycopg2.connect(
    host="crossover.proxy.rlwy.net",
    port=56267,
    user="postgres",
    password="FxvzWGNDpTtzlFccSOQKATscwIXJirFA",
    dbname="railway",
    connect_timeout=10,
)
cur = conn.cursor()


def w(text, width=85, pfx="    "):
    if not text:
        return pfx + "(empty)"
    return "\n".join(
        pfx + l for l in textwrap.fill(text.strip(), width=width).split("\n")
    )


def fmt_time(dt):
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M")


# ============================================================
# 1. FUNNEL OVERVIEW - How many conversations at each stage?
# ============================================================
print("=" * 100)
print("  FUNNEL STAGE OVERVIEW")
print("=" * 100)
cur.execute("""
    SELECT funnel_stage, COUNT(*)
    FROM conversations
    GROUP BY funnel_stage
    ORDER BY
        CASE funnel_stage
            WHEN 'initiated' THEN 1
            WHEN 'positive_reply' THEN 2
            WHEN 'pitched' THEN 3
            WHEN 'calendar_sent' THEN 4
            WHEN 'booked' THEN 5
            WHEN 'regeneration' THEN 6
            ELSE 7
        END
""")
for stage, count in cur.fetchall():
    print(f"  {stage or 'NULL':20s} : {count}")

# ============================================================
# 2. DRAFT SEND STATS
# ============================================================
print("\n" + "=" * 100)
print("  DRAFT STATUS BREAKDOWN")
print("=" * 100)
cur.execute("""
    SELECT status, COUNT(*) FROM drafts GROUP BY status ORDER BY COUNT(*) DESC
""")
for status, count in cur.fetchall():
    print(f"  {status:15s} : {count}")

# How many approved vs total?
cur.execute("SELECT COUNT(*) FROM drafts WHERE status = 'approved'")
approved_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM drafts")
total_count = cur.fetchone()[0]
print(f"\n  Approval rate: {approved_count}/{total_count} ({100*approved_count/total_count:.1f}%)")

# ============================================================
# 3. CONVERSATIONS THAT PROGRESSED PAST POSITIVE_REPLY
#    (pitched, calendar_sent, or booked)
# ============================================================
print("\n" + "=" * 100)
print("  CONVERSATIONS THAT PROGRESSED (pitched / calendar_sent / booked)")
print("=" * 100)

cur.execute("""
    SELECT
        c.id, c.lead_name, c.linkedin_profile_url, c.funnel_stage,
        c.conversation_history, c.created_at,
        p.company_name, p.job_title, p.pitched_at, p.calendar_sent_at, p.booked_at,
        p.positive_reply_at, p.source_type
    FROM conversations c
    LEFT JOIN prospects p ON p.linkedin_url = c.linkedin_profile_url
    WHERE c.funnel_stage IN ('pitched', 'calendar_sent', 'booked')
    ORDER BY
        CASE c.funnel_stage
            WHEN 'booked' THEN 1
            WHEN 'calendar_sent' THEN 2
            WHEN 'pitched' THEN 3
        END,
        c.created_at DESC
""")

progressed = cur.fetchall()
print(f"\n  Total progressed conversations: {len(progressed)}\n")

for i, row in enumerate(progressed, 1):
    (
        cid, name, url, stage, history, created,
        company, title, pitched_at, cal_at, booked_at,
        pos_reply_at, source
    ) = row

    print(f"\n{'─' * 100}")
    print(f"  #{i} | {name} | Stage: {stage.upper()}")
    print(f"  Company: {company or 'N/A'} | Title: {title or 'N/A'} | Source: {source or 'N/A'}")
    print(f"  LinkedIn: {url}")
    print(f"  Positive Reply: {fmt_time(pos_reply_at)} | Pitched: {fmt_time(pitched_at)} | Calendar: {fmt_time(cal_at)} | Booked: {fmt_time(booked_at)}")

    # Get all outbound messages (what we sent) from MessageLog
    cur.execute("""
        SELECT content, sent_at, direction
        FROM message_log
        WHERE conversation_id = %s
        ORDER BY sent_at ASC
    """, (str(cid),))
    msg_logs = cur.fetchall()

    # Get all drafts for this conversation
    cur.execute("""
        SELECT ai_draft, triggering_message, status, created_at, is_first_reply
        FROM drafts
        WHERE conversation_id = %s
        ORDER BY created_at ASC
    """, (str(cid),))
    drafts = cur.fetchall()

    # Show conversation thread from history (now with correct roles)
    print(f"\n  --- Conversation Thread ({len(history or [])} messages) ---")
    if history:
        for msg in history:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            time = msg.get("time", "")
            marker = ">>>" if role == "you" else "   "
            role_label = "YOU " if role == "you" else "LEAD"
            time_str = f" [{time[:16]}]" if time else ""
            # Truncate long messages
            content_display = content[:200] + "..." if len(content) > 200 else content
            print(f"    {marker} {role_label}{time_str}: {content_display}")
    else:
        print("    (no history)")

    # Show drafts
    if drafts:
        print(f"\n  --- Drafts ({len(drafts)}) ---")
        for j, (ai, trig, dstatus, dcreated, first) in enumerate(drafts, 1):
            print(f"    Draft {j} [{dstatus}] ({fmt_time(dcreated)}) first_reply={first}")
            if trig:
                print(f"      Triggering msg: {trig[:120]}...")
            print(f"      Sent text: {(ai or '')[:200]}{'...' if ai and len(ai) > 200 else ''}")

    print()


# ============================================================
# 4. WHAT MESSAGES ACTUALLY PROGRESSED CONVERSATIONS?
#    Focus on the outbound message sent JUST BEFORE stage changed
# ============================================================
print("\n" + "=" * 100)
print("  KEY MESSAGES THAT PROGRESSED CONVERSATIONS")
print("  (outbound messages sent near stage transitions)")
print("=" * 100)

for i, row in enumerate(progressed, 1):
    (
        cid, name, url, stage, history, created,
        company, title, pitched_at, cal_at, booked_at,
        pos_reply_at, source
    ) = row

    if not history:
        continue

    # Find the "you" messages in history
    you_messages = [
        msg for msg in history if msg.get("role") == "you"
    ]

    if not you_messages:
        continue

    print(f"\n  {name} ({company or 'N/A'}) -> {stage.upper()}")

    # Show all YOUR messages (these are the edited/sent replies)
    for msg in you_messages:
        content = msg.get("content", "")
        time = msg.get("time", "")
        time_str = f" [{time[:16]}]" if time else ""
        print(f"    YOU{time_str}: {content[:300]}")

    # Show if lead responded after
    lead_messages = [msg for msg in history if msg.get("role") == "lead"]
    if lead_messages:
        last_lead = lead_messages[-1]
        print(f"    LEAD (last): {last_lead.get('content', '')[:200]}")


# ============================================================
# 5. PATTERN ANALYSIS - Common themes in messages that worked
# ============================================================
print("\n" + "=" * 100)
print("  PATTERN ANALYSIS - Messages that led to progression")
print("=" * 100)

# Collect all "you" messages from progressed conversations
all_you_msgs = []
for row in progressed:
    cid, name, url, stage, history, *_ = row
    if not history:
        continue
    for msg in history:
        if msg.get("role") == "you":
            all_you_msgs.append({
                "content": msg.get("content", ""),
                "stage": stage,
                "lead": name,
            })

print(f"\n  Total 'you' messages in progressed conversations: {len(all_you_msgs)}")

# Check for common keywords/phrases
keywords = {
    "call": 0, "chat": 0, "meeting": 0, "schedule": 0, "calendly": 0,
    "calendar": 0, "15 min": 0, "quick": 0, "book": 0, "link": 0,
    "interested": 0, "pain point": 0, "challenge": 0, "linkedin": 0,
    "client acquisition": 0, "icp": 0, "?": 0,
}
for msg in all_you_msgs:
    content_lower = msg["content"].lower()
    for kw in keywords:
        if kw in content_lower:
            keywords[kw] += 1

print("\n  Keyword frequency in successful outbound messages:")
for kw, count in sorted(keywords.items(), key=lambda x: -x[1]):
    if count > 0:
        bar = "#" * count
        print(f"    {kw:25s} : {count:3d} {bar}")


# ============================================================
# 6. STALLED CONVERSATIONS - Approved but didn't progress
# ============================================================
print("\n" + "=" * 100)
print("  STALLED: Approved drafts where conversation DIDN'T progress")
print("=" * 100)

cur.execute("""
    SELECT
        c.id, c.lead_name, c.funnel_stage, c.conversation_history,
        d.ai_draft, d.created_at as draft_sent_at,
        p.company_name, p.job_title
    FROM drafts d
    JOIN conversations c ON d.conversation_id = c.id
    LEFT JOIN prospects p ON p.linkedin_url = c.linkedin_profile_url
    WHERE d.status = 'approved'
    AND c.funnel_stage = 'positive_reply'
    ORDER BY d.created_at DESC
    LIMIT 15
""")

stalled = cur.fetchall()
print(f"\n  Conversations with approved drafts still at positive_reply: showing {len(stalled)}")

for i, (cid, name, stage, history, ai, sent_at, company, title) in enumerate(stalled, 1):
    print(f"\n  #{i} {name} ({company or 'N/A'}) - sent {fmt_time(sent_at)}")
    print(f"      What was sent: {(ai or '')[:200]}")
    # Show if lead responded
    if history:
        lead_msgs_after = [
            m for m in history
            if m.get("role") == "lead"
        ]
        if lead_msgs_after:
            last = lead_msgs_after[-1]
            print(f"      Lead's last msg: {last.get('content', '')[:150]}")
        else:
            print(f"      (no lead reply found)")


# ============================================================
# 7. RESPONSE TIME ANALYSIS for progressed conversations
# ============================================================
print("\n" + "=" * 100)
print("  RESPONSE TIME - How fast were replies in progressed conversations?")
print("=" * 100)

for row in progressed:
    cid, name, url, stage, history, created, company, *_ = row
    if not history or len(history) < 2:
        continue

    # Look at time gaps between messages
    prev_time = None
    gaps = []
    for msg in history:
        t = msg.get("time", "")
        if not t:
            continue
        try:
            # Handle various time formats
            if "T" in t:
                cur_time = datetime.fromisoformat(t.replace("Z", "+00:00").split("+")[0])
            else:
                continue
            if prev_time and msg.get("role") == "you":
                gap_hours = (cur_time - prev_time).total_seconds() / 3600
                gaps.append(gap_hours)
            prev_time = cur_time
        except (ValueError, TypeError):
            continue

    if gaps:
        avg_gap = sum(gaps) / len(gaps)
        min_gap = min(gaps)
        print(f"  {name:30s} | Stage: {stage:15s} | Avg reply: {avg_gap:6.1f}h | Fastest: {min_gap:5.1f}h")


conn.close()
print("\n" + "=" * 100)
print("  REPORT COMPLETE")
print("=" * 100)
