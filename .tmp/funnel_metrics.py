import psycopg2

conn = psycopg2.connect(
    host="crossover.proxy.rlwy.net",
    port=56267,
    user="postgres",
    password="FxvzWGNDpTtzlFccSOQKATscwIXJirFA",
    dbname="railway"
)
cur = conn.cursor()
print("Connected OK")

# 1. TOTAL
cur.execute("SELECT COUNT(*) FROM prospects")
total = cur.fetchone()[0]
print("=" * 70)
print("1. TOTAL PROSPECTS")
print("=" * 70)
print(f"   Total: {total}")
