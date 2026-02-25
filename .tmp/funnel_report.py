"""
Funnel Metrics Report — connects to prod DB and reports:
1. Prospect timestamp counts per funnel stage
2. Conversation.funnel_stage counts (cross-check)
3. Mismatches between the two
4. Conversion rates between stages
5. Names at pitched+ for quick reference
6. Pending draft count
"""
import psycopg2

conn = psycopg2.connect(
    host="crossover.proxy.rlwy.net",
    port=56267,
    user="postgres",
    password="FxvzWGNDpTtzlFccSOQKATscwIXJirFA",
    dbname="railway",
    connect_timeout=10,
)
cur = conn.cursor()


def fmt_time(dt):
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M")


# ============================================================
# 1. PROSPECT TIMESTAMP COUNTS (source of truth)
# ============================================================
print("=" * 80)
print("  PROSPECT TIMESTAMP COUNTS")
print("=" * 80)

stages = [
    ("Connection Sent", "connection_sent_at"),
    ("Connection Accepted", "connection_accepted_at"),
    ("Positive Reply", "positive_reply_at"),
    ("Pitched", "pitched_at"),
    ("Calendar Sent", "calendar_sent_at"),
    ("Booked", "booked_at"),
]

prospect_counts = {}
for label, col in stages:
    cur.execute(f"SELECT COUNT(*) FROM prospects WHERE {col} IS NOT NULL")
    count = cur.fetchone()[0]
    prospect_counts[label] = count
    print(f"  {label:25s} : {count}")

# Total prospects
cur.execute("SELECT COUNT(*) FROM prospects")
total_prospects = cur.fetchone()[0]
print(f"\n  Total prospects: {total_prospects}")

# ============================================================
# 2. CONVERSATION FUNNEL_STAGE COUNTS (cross-check)
# ============================================================
print("\n" + "=" * 80)
print("  CONVERSATION FUNNEL_STAGE COUNTS")
print("=" * 80)

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
conv_counts = {}
for stage, count in cur.fetchall():
    conv_counts[stage or "NULL"] = count
    print(f"  {(stage or 'NULL'):25s} : {count}")

cur.execute("SELECT COUNT(*) FROM conversations")
total_convos = cur.fetchone()[0]
print(f"\n  Total conversations: {total_convos}")

# ============================================================
# 3. MISMATCHES — prospects vs conversations
# ============================================================
print("\n" + "=" * 80)
print("  MISMATCHES — Prospect timestamps vs Conversation funnel_stage")
print("=" * 80)

# Prospects pitched but conversation not at pitched+
cur.execute("""
    SELECT p.full_name, p.linkedin_url, c.funnel_stage, p.pitched_at
    FROM prospects p
    LEFT JOIN conversations c ON p.conversation_id = c.id
    WHERE p.pitched_at IS NOT NULL
      AND (c.funnel_stage IS NULL OR c.funnel_stage NOT IN ('pitched', 'calendar_sent', 'booked'))
""")
mismatches_pitched = cur.fetchall()

# Conversations at pitched+ but prospect missing timestamp
cur.execute("""
    SELECT c.lead_name, c.linkedin_profile_url, c.funnel_stage, p.pitched_at
    FROM conversations c
    LEFT JOIN prospects p ON p.linkedin_url = c.linkedin_profile_url
    WHERE c.funnel_stage IN ('pitched', 'calendar_sent', 'booked')
      AND (p.pitched_at IS NULL OR p.id IS NULL)
""")
mismatches_conv = cur.fetchall()

if mismatches_pitched:
    print(f"\n  Prospects with pitched_at but conversation NOT at pitched+: {len(mismatches_pitched)}")
    for name, url, fs, pat in mismatches_pitched:
        print(f"    {name or 'N/A':30s} conv_stage={fs or 'NULL':15s} pitched_at={fmt_time(pat)}")
else:
    print("\n  No prospect->conversation mismatches found.")

if mismatches_conv:
    print(f"\n  Conversations at pitched+ but prospect missing pitched_at: {len(mismatches_conv)}")
    for name, url, fs, pat in mismatches_conv:
        print(f"    {name or 'N/A':30s} conv_stage={fs:15s} prospect.pitched_at={fmt_time(pat)}")
else:
    print("  No conversation->prospect mismatches found.")

# ============================================================
# 4. CONVERSION RATES
# ============================================================
print("\n" + "=" * 80)
print("  CONVERSION RATES (prospect timestamps)")
print("=" * 80)

prev_label = None
prev_count = None
for label, _ in stages:
    count = prospect_counts[label]
    if prev_count and prev_count > 0:
        rate = 100 * count / prev_count
        print(f"  {prev_label} -> {label}: {count}/{prev_count} = {rate:.1f}%")
    prev_label = label
    prev_count = count

# ============================================================
# 5. NAMES AT PITCHED+ (quick reference)
# ============================================================
print("\n" + "=" * 80)
print("  PROSPECTS AT PITCHED+ (names & timestamps)")
print("=" * 80)

cur.execute("""
    SELECT p.full_name, p.company_name, p.job_title,
           p.pitched_at, p.calendar_sent_at, p.booked_at,
           c.funnel_stage
    FROM prospects p
    LEFT JOIN conversations c ON p.conversation_id = c.id
    WHERE p.pitched_at IS NOT NULL
    ORDER BY
        CASE
            WHEN p.booked_at IS NOT NULL THEN 1
            WHEN p.calendar_sent_at IS NOT NULL THEN 2
            ELSE 3
        END,
        p.pitched_at DESC
""")
pitched_plus = cur.fetchall()

if pitched_plus:
    for name, company, title, pat, cat, bat, fs in pitched_plus:
        stage_tag = "BOOKED" if bat else ("CALENDAR" if cat else "PITCHED")
        print(f"  [{stage_tag:8s}] {name or 'N/A':30s} | {company or 'N/A':25s} | conv: {fs or 'N/A'}")
        print(f"             pitched={fmt_time(pat)}  calendar={fmt_time(cat)}  booked={fmt_time(bat)}")
else:
    print("  (none)")

# Also show conversations at pitched+ that may not have prospect records
cur.execute("""
    SELECT c.lead_name, c.funnel_stage, c.linkedin_profile_url
    FROM conversations c
    LEFT JOIN prospects p ON p.linkedin_url = c.linkedin_profile_url
    WHERE c.funnel_stage IN ('pitched', 'calendar_sent', 'booked')
      AND p.id IS NULL
""")
orphan_convos = cur.fetchall()
if orphan_convos:
    print(f"\n  Conversations at pitched+ WITHOUT a prospect record: {len(orphan_convos)}")
    for name, fs, url in orphan_convos:
        print(f"    {name:30s} | stage={fs} | {url}")

# ============================================================
# 6. DRAFT STATUS BREAKDOWN
# ============================================================
print("\n" + "=" * 80)
print("  DRAFT STATUS BREAKDOWN")
print("=" * 80)

cur.execute("""
    SELECT status, COUNT(*) FROM drafts GROUP BY status ORDER BY COUNT(*) DESC
""")
for status, count in cur.fetchall():
    print(f"  {status:15s} : {count}")

cur.execute("SELECT COUNT(*) FROM drafts WHERE status = 'pending'")
pending = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM drafts")
total_drafts = cur.fetchone()[0]
print(f"\n  Pending drafts awaiting action: {pending}")
print(f"  Total drafts: {total_drafts}")

conn.close()
print("\n" + "=" * 80)
print("  FUNNEL REPORT COMPLETE")
print("=" * 80)
