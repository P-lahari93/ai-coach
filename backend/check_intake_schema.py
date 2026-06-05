import asyncio, asyncpg, json

async def check():
    conn = await asyncpg.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')
    rows = await conn.fetch("""
        SELECT cm.key, cm.name, mv.intake_schema, mv.scoring_rubric, mv.framework_name
        FROM coaching_modules cm
        JOIN module_versions mv ON mv.module_id = cm.id AND mv.is_current = true
        WHERE cm.status = 'published'
    """)
    for r in rows:
        print(f"\nModule: {r['key']} ({r['name']}) — framework: {r['framework_name']}")
        schema = json.loads(r['intake_schema']) if r['intake_schema'] else []
        print(f"  intake_schema fields ({len(schema)}):")
        for f in schema:
            print(f"    {f.get('field_key')}: label='{f.get('label')}' type={f.get('type')} required={f.get('required')}")
        rubric = json.loads(r['scoring_rubric']) if r['scoring_rubric'] else {}
        dims = rubric.get('dimensions', [])
        print(f"  scoring_rubric dimensions ({len(dims)}):")
        for d in dims:
            print(f"    {d.get('name')} weight={d.get('weight')}")
    await conn.close()

asyncio.run(check())
