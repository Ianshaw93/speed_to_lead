import psycopg2

conn = psycopg2.connect(
    host='crossover.proxy.rlwy.net',
    port=56267,
    user='postgres',
    password='FxvzWGNDpTtzlFccSOQKATscwIXJirFA',
    dbname='railway'
)
cur = conn.cursor()
SEP = '=' * 70

cur.execute('SELECT COUNT(*) FROM prospects')
total = cur.fetchone()[0]
print(SEP)
print('1. TOTAL PROSPECTS')
print(SEP)
print(f'   Total: {total}')

print()
print(SEP)
print('2. PROSPECTS BY FUNNEL STAGE (timestamp fields)')
print(SEP)

stages = [
    ('connection_sent_at',     'Connection Sent'),
    ('connection_accepted_at', 'Connection Accepted'),
    ('positive_reply_at',      'Positive Reply'),
    ('pitched_at',             'Pitched'),
    ('calendar_sent_at',       'Calendar Sent'),
    ('booked_at',              'Booked'),
]

for col, label in stages:
    cur.execute(f'SELECT COUNT(*) FROM prospects WHERE {col} IS NOT NULL')
    count = cur.fetchone()[0]
    pct = (count / total * 100) if total > 0 else 0
    print(f'   {label:25s} {count:6d}   ({pct:5.1f}%% of total)')

print()
print(SEP)
print('3. CONVERSATIONS BY FUNNEL_STAGE ENUM')
print(SEP)

cur.execute('SELECT COUNT(*) FROM conversations')
total_convos = cur.fetchone()[0]
print(f'   Total conversations: {total_convos}')
print()

cur.execute('SELECT funnel_stage, COUNT(*) as cnt FROM conversations GROUP BY funnel_stage ORDER BY cnt DESC')
rows = cur.fetchall()
for row in rows:
    stage = row[0] if row[0] else '(NULL)'
    pct = (row[1] / total_convos * 100) if total_convos > 0 else 0
    print(f'   {stage:25s} {row[1]:6d}   ({pct:5.1f}%%)')

print()
print(SEP)
print('4. LAST 30 DAYS FUNNEL METRICS')
print(SEP)

for col, label in stages:
    q = 'SELECT COUNT(*) FROM prospects WHERE ' + col + ' IS NOT NULL AND ' + col + chr(32) + ">= NOW() - INTERVAL '30 days'"
    cur.execute(q)
    count = cur.fetchone()[0]
    print(f'   {label:25s} {count:6d}')

print()
print(SEP)
print('5. SPEED TO LEAD - RESPONSE TIME ANALYSIS')
print(SEP)

cur.execute('SELECT status, COUNT(*) FROM drafts GROUP BY status ORDER BY COUNT(*) DESC')
rows = cur.fetchall()
total_drafts = sum(r[1] for r in rows)
print(f'   Total drafts: {total_drafts}')
for row in rows:
    print(f'   Status {row[0]:15s}: {row[1]:6d}')
print()

q5a = (
    'SELECT COUNT(*) as cnt,'
    ' AVG(EXTRACT(EPOCH FROM (d.created_at - c.updated_at))) / 3600,'
    ' PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (d.created_at - c.updated_at))) / 3600,'
    ' MIN(EXTRACT(EPOCH FROM (d.created_at - c.updated_at))) / 3600,'
    ' MAX(EXTRACT(EPOCH FROM (d.created_at - c.updated_at))) / 3600'
    ' FROM drafts d JOIN conversations c ON d.conversation_id = c.id'
    ' WHERE d.created_at > c.updated_at'
)
cur.execute(q5a)
row = cur.fetchone()
print('   Draft creation after conversation update:')
print(f'     Count:  {row[0]}')
if row[1] is not None:
    print(f'     Avg:    {float(row[1]):.1f} hours')
    print(f'     Median: {float(row[2]):.1f} hours')
    print(f'     Min:    {float(row[3]):.2f} hours')
    print(f'     Max:    {float(row[4]):.1f} hours')
print()

q5b = (
    'SELECT COUNT(*) as cnt,'
    ' AVG(EXTRACT(EPOCH FROM (d.updated_at - d.created_at))) / 3600,'
    ' PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (d.updated_at - d.created_at))) / 3600'
    ' FROM drafts d'
    " WHERE d.status = 'approved' AND d.updated_at > d.created_at"
)
cur.execute(q5b)
row = cur.fetchone()
print('   Draft creation to approval (approved drafts):')
print(f'     Count:  {row[0]}')
if row[1] is not None:
    print(f'     Avg:    {float(row[1]):.1f} hours')
    print(f'     Median: {float(row[2]):.1f} hours')
print()

cur.execute('SELECT classification, COUNT(*) FROM drafts WHERE classification IS NOT NULL GROUP BY classification ORDER BY COUNT(*) DESC')
rows = cur.fetchall()
if rows:
    print('   Draft classification breakdown:')
    for row in rows:
        print(f'     {row[0]:20s}: {row[1]:6d}')

print()
print(SEP)
print('6. MONTHLY TRENDS (Last 3 Months)')
print(SEP)

for col, label in stages:
    q = "SELECT TO_CHAR(DATE_TRUNC('month', " + col + "), 'YYYY-MM') as month, COUNT(*) as cnt FROM prospects WHERE " + col + " IS NOT NULL AND " + col + " >= NOW() - INTERVAL '3 months' GROUP BY DATE_TRUNC('month', " + col + ") ORDER BY month"
    cur.execute(q)
    rows = cur.fetchall()
    if rows:
        month_str = '  |  '.join([f'{r[0]}: {r[1]}' for r in rows])
        print(f'   {label:25s} {month_str}')
    else:
        print(f'   {label:25s} (no data)')

print()

cur.execute("SELECT TO_CHAR(DATE_TRUNC('month', created_at), 'YYYY-MM'), COUNT(*) FROM conversations WHERE created_at >= NOW() - INTERVAL '3 months' GROUP BY DATE_TRUNC('month', created_at) ORDER BY 1")
rows = cur.fetchall()
label = 'Conversations Created'
if rows:
    print(f'   {label:25s} ' + '  |  '.join([f'{r[0]}: {r[1]}' for r in rows]))
else:
    print(f'   {label:25s} (no data)')

cur.execute("SELECT TO_CHAR(DATE_TRUNC('month', created_at), 'YYYY-MM'), COUNT(*) FROM drafts WHERE created_at >= NOW() - INTERVAL '3 months' GROUP BY DATE_TRUNC('month', created_at) ORDER BY 1")
rows = cur.fetchall()
label = 'Drafts Created'
if rows:
    print(f'   {label:25s} ' + '  |  '.join([f'{r[0]}: {r[1]}' for r in rows]))
else:
    print(f'   {label:25s} (no data)')

cur.execute("SELECT TO_CHAR(DATE_TRUNC('month', updated_at), 'YYYY-MM'), COUNT(*) FROM drafts WHERE status = 'approved' AND updated_at >= NOW() - INTERVAL '3 months' GROUP BY DATE_TRUNC('month', updated_at) ORDER BY 1")
rows = cur.fetchall()
label = 'Drafts Approved'
if rows:
    print(f'   {label:25s} ' + '  |  '.join([f'{r[0]}: {r[1]}' for r in rows]))
else:
    print(f'   {label:25s} (no data)')

print()
print(SEP)
print('BONUS: CONVERSION RATES (All-Time)')
print(SEP)

q_conv = (
    'SELECT'
    ' COUNT(*) FILTER (WHERE connection_sent_at IS NOT NULL) as sent,'
    ' COUNT(*) FILTER (WHERE connection_accepted_at IS NOT NULL) as accepted,'
    ' COUNT(*) FILTER (WHERE positive_reply_at IS NOT NULL) as positive,'
    ' COUNT(*) FILTER (WHERE pitched_at IS NOT NULL) as pitched,'
    ' COUNT(*) FILTER (WHERE calendar_sent_at IS NOT NULL) as calendar,'
    ' COUNT(*) FILTER (WHERE booked_at IS NOT NULL) as booked'
    ' FROM prospects'
)
cur.execute(q_conv)
r = cur.fetchone()
sent, accepted, positive, pitched, calendar, booked = r

def pct(num, denom):
    if denom > 0:
        return f'{num/denom*100:.1f}%%'
    return 'N/A'

print(f'   Sent -> Accepted:     {pct(accepted, sent):>8s}   ({accepted}/{sent})')
print(f'   Accepted -> Positive: {pct(positive, accepted):>8s}   ({positive}/{accepted})')
print(f'   Positive -> Pitched:  {pct(pitched, positive):>8s}   ({pitched}/{positive})')
print(f'   Pitched -> Calendar:  {pct(calendar, pitched):>8s}   ({calendar}/{pitched})')
print(f'   Calendar -> Booked:   {pct(booked, calendar):>8s}   ({booked}/{calendar})')
print(f'   End-to-End (Sent->Booked): {pct(booked, sent):>8s}   ({booked}/{sent})')

conn.close()
print()
print('Done.')
