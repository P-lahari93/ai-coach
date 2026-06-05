import asyncio, asyncpg

async def check():
    conn = await asyncpg.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')
    
    print("=== All coaching sessions ===")
    sessions = await conn.fetch("""
        SELECT id, status, final_score, completed_at, created_at 
        FROM coaching_sessions 
        ORDER BY created_at DESC LIMIT 10
    """)
    for s in sessions:
        print(f"  {s['id']} | status={s['status']} | score={s['final_score']} | completed={s['completed_at']}")
    
    print("\n=== All feedback reports ===")
    reports = await conn.fetch("""
        SELECT id, session_id, overall_score, created_at
        FROM feedback_reports
        ORDER BY created_at DESC LIMIT 10
    """)
    if reports:
        for r in reports:
            print(f"  report_id={r['id']}")
            print(f"  session_id={r['session_id']}")
            print(f"  score={r['overall_score']}")
            print(f"  created={r['created_at']}")
    else:
        print("  No feedback reports in database yet.")
    
    await conn.close()

asyncio.run(check())
