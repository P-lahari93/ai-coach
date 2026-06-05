import psycopg2
conn = psycopg2.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
tables = [r[0] for r in cur.fetchall()]
print('Tables:', tables)
cur.execute("SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='tenant_id' ORDER BY table_name")
has_tenant = [r[0] for r in cur.fetchall()]
print('Has tenant_id:', has_tenant)
# Check which RLS tables lack tenant_id
rls_tables = ['tenants','tenant_settings','coaching_modules','module_versions','module_framework_steps',
    'module_prompt_templates','module_personas','rubrics','knowledge_bases','knowledge_sources',
    'knowledge_chunks','coaching_sessions','roleplay_sessions','feedback_reports','user_progress',
    'achievements','user_achievements','notifications']
no_tenant = [t for t in rls_tables if t not in has_tenant]
print('RLS tables WITHOUT tenant_id:', no_tenant)
conn.close()
