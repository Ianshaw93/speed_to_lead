
import psycopg2, json, textwrap
conn = psycopg2.connect(host="crossover.proxy.rlwy.net", port=56267, user="postgres", password="FxvzWGNDpTtzlFccSOQKATscwIXJirFA", dbname="railway", connect_timeout=10)
cur = conn.cursor()
def w(text, width=90, pfx="    "):
    if not text: return pfx + "(empty)"
    return chr(10).join(pfx + l for l in textwrap.fill(text.strip(), width=width).split(chr(10)))

print("=" * 100)
print("  1. TEN MOST RECENT APPROVED DRAFTS")
print("=" * 100)
cur.execute("SELECT d.id, d.ai_draft, d.triggering_message, d.is_first_reply, d.classification, d.created_at, c.linkedin_profile_url, c.lead_name, c.conversation_history, c.funnel_stage, c.id FROM drafts d JOIN conversations c ON d.conversation_id = c.id WHERE d.status = %s ORDER BY d.created_at DESC LIMIT 10", ("approved",))
for i, row in enumerate(cur.fetchall(), 1):
    did, ai, trig, first, cls, cat, url, name, hist, fs, cid = row
    print()
    print(f"--- Approved #{i} ({cat.strftime(chr(37)+"Y-"+chr(37)+"m-"+chr(37)+"d "+chr(37)+"H:"+chr(37)+"M")}) ---")
    print(f"  Lead: {name}")
    print(f"  LinkedIn: {url}")
    print(f"  First Reply: {first}  Classification: {cls}  Funnel: {fs}")
    print(f"  Triggering Msg (what WE sent):")
    print(w(trig))
    cur.execute("SELECT content, sent_at FROM message_log WHERE conversation_id = %s AND direction = %s ORDER BY sent_at DESC LIMIT 1", (cid, "inbound"))
    ib = cur.fetchone()
    if ib:
        print(f"  Lead Reply:")
        print(w(ib[0]))
    else:
        print("  Lead Reply: (none in message_log)")
    print(f"  AI Draft:")
    print(w(ai))
    cur.execute("SELECT content, sent_at FROM message_log WHERE conversation_id = %s AND direction = %s AND sent_at >= %s ORDER BY sent_at ASC LIMIT 1", (cid, "outbound", cat))
    ob = cur.fetchone()
    if ob:
        print(f"  ACTUALLY SENT:")
        print(w(ob[0]))
        if ob[0].strip() != (ai or "").strip():
            print("    ^ DIFFERS from AI draft!")
    else:
        print("  ACTUALLY SENT: (no outbound log after draft)")
    if hist:
        h = hist if isinstance(hist, list) else json.loads(hist) if isinstance(hist, str) else hist
        if isinstance(h, list) and len(h) > 0:
            print(f"  Conv History ({len(h)} msgs, last 4):")
            for m in h[-4:]:
                if isinstance(m, dict):
                    print(f"    [{m.get('direction','?')}] {m.get('content',str(m))[:150]}")
                else:
                    print(f"    {str(m)[:150]}")
    print()

print()
print("=" * 100)
print("  2. FIVE MOST RECENT PENDING DRAFTS")
print("=" * 100)
cur.execute("SELECT d.id, d.ai_draft, d.triggering_message, d.is_first_reply, d.classification, d.created_at, c.linkedin_profile_url, c.lead_name, c.funnel_stage, c.id FROM drafts d JOIN conversations c ON d.conversation_id = c.id WHERE d.status = %s ORDER BY d.created_at DESC LIMIT 5", ("pending",))
for i, row in enumerate(cur.fetchall(), 1):
    did, ai, trig, first, cls, cat, url, name, fs, cid = row
    print()
    print(f"--- Pending #{i} ({cat.strftime(chr(37)+"Y-"+chr(37)+"m-"+chr(37)+"d "+chr(37)+"H:"+chr(37)+"M")}) ---")
    print(f"  Lead: {name}")
    print(f"  LinkedIn: {url}")
    print(f"  First Reply: {first}  Classification: {cls}  Funnel: {fs}")
    print(f"  Triggering Msg:")
    print(w(trig))
    cur.execute("SELECT content, sent_at FROM message_log WHERE conversation_id = %s AND direction = %s ORDER BY sent_at DESC LIMIT 1", (cid, "inbound"))
    ib = cur.fetchone()
    if ib:
        print(f"  Lead Reply:")
        print(w(ib[0]))
    print(f"  AI Draft (awaiting approval):")
    print(w(ai))
    print()

print()
print("=" * 100)
print("  3. AI DRAFT vs ACTUALLY SENT COMPARISON")
print("=" * 100)
cur.execute("SELECT d.ai_draft, d.created_at, c.lead_name, ml.content, ml.sent_at FROM drafts d JOIN conversations c ON d.conversation_id = c.id JOIN message_log ml ON ml.conversation_id = c.id AND ml.direction = %s AND ml.sent_at >= d.created_at WHERE d.status = %s ORDER BY d.created_at DESC LIMIT 10", ("outbound", "approved"))
comps = cur.fetchall()
if not comps:
    print("  No outbound message_log entries found after approved drafts.")
    print("  Messages may be sent via HeyReach without being logged back.")
else:
    for i, (ai, dat, name, sent, sat) in enumerate(comps, 1):
        match = ai.strip() == sent.strip()
        print(f"  #{i} {name} - Match: {match}")
        if not match:
            print(f"    AI Draft:")
            print(w(ai, pfx="      "))
            print(f"    Actually Sent:")
            print(w(sent, pfx="      "))

print()
print("=" * 100)
print("  4. CONVERSATION HISTORY FORMAT")
print("=" * 100)
cur.execute("SELECT id, lead_name, conversation_history, linkedin_profile_url FROM conversations WHERE conversation_history IS NOT NULL AND jsonb_array_length(conversation_history) > 0 ORDER BY updated_at DESC LIMIT 3")
convos = cur.fetchall()
if not convos:
    print("  No conversations have stored message history in conversation_history.")
else:
    for i, (cid2, name2, hist2, url2) in enumerate(convos, 1):
        h2 = hist2 if isinstance(hist2, list) else json.loads(hist2) if isinstance(hist2, str) else hist2
        print(f"  Conversation: {name2} ({url2})")
        print(f"  Entries: {len(h2) if isinstance(h2, list) else 'unknown'}")
        if isinstance(h2, list):
            for j, msg in enumerate(h2[:6]):
                print(f"    [{j}] {json.dumps(msg, default=str)[:200]}")
            if len(h2) > 6:
                print(f"    ... and {len(h2) - 6} more")

print()
print("=" * 100)
print("  5. FULL MESSAGE THREADS for 3 recent approved-draft conversations")
print("=" * 100)
cur.execute("SELECT DISTINCT ON (c.id) c.id, c.lead_name, d.created_at FROM drafts d JOIN conversations c ON d.conversation_id = c.id WHERE d.status = %s ORDER BY c.id, d.created_at DESC", ("approved",))
all_approved_convos = cur.fetchall()
all_approved_convos.sort(key=lambda x: x[2], reverse=True)
for cid3, name3, _ in all_approved_convos[:3]:
    cur.execute("SELECT direction, content, sent_at FROM message_log WHERE conversation_id = %s ORDER BY sent_at ASC", (cid3,))
    msgs = cur.fetchall()
    print(f"  Thread: {name3} ({len(msgs)} messages)")
    for d2, c2, s2 in msgs:
        tag = "US  " if d2 == "outbound" else "THEM"
        print(f"    [{tag} {s2.strftime(chr(37)+"m/"+chr(37)+"d "+chr(37)+"H:"+chr(37)+"M")}] {c2[:200]}")
    print()

print()
print("=" * 100)
print("  6. SUMMARY STATS")
print("=" * 100)
cur.execute("SELECT count(*) FROM conversations")
print(f"  Total conversations: {cur.fetchone()[0]}")
cur.execute("SELECT count(*) FROM conversations WHERE conversation_history IS NOT NULL AND jsonb_array_length(conversation_history) > 0")
print(f"  Conversations with history: {cur.fetchone()[0]}")
cur.execute("SELECT direction, count(*) FROM message_log GROUP BY direction")
print(f"  Message log:")
for row in cur.fetchall():
    print(f"    {row[0]:10s} {row[1]}")
cur.execute("SELECT classification, count(*) FROM drafts WHERE classification IS NOT NULL GROUP BY classification ORDER BY count(*) DESC")
print(f"  Draft classifications:")
for row in cur.fetchall():
    print(f"    {str(row[0]):25s} {row[1]}")
cur.execute("SELECT status, classification, count(*) FROM drafts GROUP BY status, classification ORDER BY status, count(*) DESC")
print(f"  Draft status x classification:")
for row in cur.fetchall():
    print(f"    {str(row[0]):12s} {str(row[1]):25s} {row[2]}")
conn.close()
print("Done.")
