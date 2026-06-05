import asyncio, asyncpg

async def check():
    conn = await asyncpg.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')
    modules = await conn.fetch('SELECT id, key, name, status FROM coaching_modules ORDER BY created_at LIMIT 10')
    print(f'Total modules: {len(modules)}')
    for m in modules:
        mid = m['id']
        key = m['key']
        status = m['status']
        versions = await conn.fetch(
            'SELECT id, version_number, is_current, published_at FROM module_versions WHERE module_id = $1', mid
        )
        print(f'  key={key} status={status} versions={len(versions)}', end='')
        for v in versions:
            print(f' [v{v["version_number"]} current={v["is_current"]} published={v["published_at"] is not None}]', end='')
        print()
    await conn.close()

asyncio.run(check())
