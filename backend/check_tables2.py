import psycopg2
conn = psycopg2.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
tables = [r[0] for r in cur.fetchall()]
print('Tables in DB:', tables)
# Check alembic version
try:
    cur.execute("SELECT version_num FROM alembic_version")
    v = cur.fetchall()
    print('Alembic version:', v)
except Exception as e:
    print('No alembic_version:', e)
conn.close()
