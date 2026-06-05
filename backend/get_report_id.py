import asyncio, asyncpg
async def get():
    conn = await asyncpg.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')
    rows = await conn.fetch("""
        SELECT fr.id as report_id, fr.overall_score, fr.feedback_text, cs.id as session_id
        FROM feedback_reports fr
        JOIN coaching_sessions cs ON cs.id = fr.session_id
        ORDER BY fr.created_at DESC LIMIT 5
    """)
    for r in rows:
        print(f"session: {r['session_id']}")
        print(f"report:  {r['report_id']}")
        print(f"score:   {r['overall_score']}")
        print(f"text:    {str(r['feedback_text'])[:100]}")
        print()
    await conn.close()
asyncio.run(get())
