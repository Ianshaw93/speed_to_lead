"""Reset database for fresh migrations."""
import os
import psycopg2

url = os.environ.get('DATABASE_URL')
if not url:
    print("ERROR: DATABASE_URL not set")
    exit(1)

# Add sslmode if not present
if 'sslmode' not in url:
    url = url + ('&' if '?' in url else '?') + 'sslmode=require'

print(f"Connecting to database...")
conn = psycopg2.connect(url)
conn.autocommit = True
cur = conn.cursor()

print("Dropping draft_status type...")
cur.execute('DROP TYPE IF EXISTS draft_status CASCADE')

print("Dropping message_direction type...")
cur.execute('DROP TYPE IF EXISTS message_direction CASCADE')

print("Dropping alembic_version table...")
cur.execute('DROP TABLE IF EXISTS alembic_version CASCADE')

print("Done! Database reset for fresh migrations.")
conn.close()
