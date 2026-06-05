"""
Verification test for Items 1-7.
No AI calls needed — verifies structure and endpoints only.
"""
import sys, os, warnings, asyncio, time, json
sys.path.insert(0, 'd:/PRD/ai-coach/backend')
os.environ['SECRET_KEY'] = 'test-key-for-validation-min-32-chars-xx'
os.environ['DATABASE_URL'] = 'postgresql+asyncpg://aicoach:aicoach@localhost:5432/aicoach'
warnings.filterwarnings('ignore')
import logging; logging.disable(logging.CRITICAL)

import httpx
from app.main import app

EMAIL = f"verify_{int(time.time())}@example.com"
PASSWORD = "Verify123!"

results = {}

def mark(item, status, evidence=""):
    results[item] = (status, evidence)
    icon = {"COMPLETE": "[COMPLETE]", "PARTIAL": "[PARTIAL ]", "FAILED": "[FAILED  ]", "NOT_STARTED": "[NOT START]"}
    print(f"  {icon.get(status, status)} Item {item}: {evidence[:120]}")

async def run():
    t = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://test", timeout=30) as c:

        # Auth
        await c.post("/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD, "full_name": "Verifier"})
        r = await c.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = r.json().get("access_token", "")
        auth = {"Authorization": f"Bearer {token}"}

        # Get published modules
        r = await c.get("/api/v1/modules/", headers=auth)
        modules = {m["key"]: m for m in r.json().get("items", []) if m.get("status") == "published"}
        sbi_id = modules.get("sbi_feedback", {}).get("id")
        grow_id = modules.get("grow_coaching", {}).get("id")
        print(f"  Published modules: {list(modules.keys())}")

        # ── ITEM 1: Dynamic intake schema from GET /sessions/coaching/{id} ──
        print("\n[Item 1] Dynamic intake form")
        if sbi_id:
            r = await c.post("/api/v1/sessions/coaching", json={"module_id": sbi_id}, headers=auth)
            sid = r.json().get("id")
            r2 = await c.get(f"/api/v1/sessions/coaching/{sid}", headers=auth)
            d = r2.json()
            schema = d.get("intake_schema", [])
            fw = d.get("framework_name", "")
            if len(schema) == 3 and fw == "SBI":
                mark(1, "COMPLETE", f"GET session returns intake_schema={[f['field_key'] for f in schema]}, framework={fw}")
            else:
                mark(1, "PARTIAL", f"schema len={len(schema)} framework='{fw}'")
            await c.post(f"/api/v1/sessions/coaching/{sid}/abandon", headers=auth)
        else:
            mark(1, "FAILED", "No SBI module found")

        # GROW: verify 4 fields returned
        print("\n[Item 1b] GROW dynamic intake")
        if grow_id:
            r = await c.post("/api/v1/sessions/coaching", json={"module_id": grow_id}, headers=auth)
            sid = r.json().get("id")
            r2 = await c.get(f"/api/v1/sessions/coaching/{sid}", headers=auth)
            d = r2.json()
            schema = d.get("intake_schema", [])
            fw = d.get("framework_name", "")
            if len(schema) == 4 and fw == "GROW":
                mark("1b", "COMPLETE", f"GROW: intake_schema={[f['field_key'] for f in schema]}, framework={fw}")
            else:
                mark("1b", "PARTIAL", f"GROW: schema len={len(schema)} framework='{fw}'")
            await c.post(f"/api/v1/sessions/coaching/{sid}/abandon", headers=auth)

        # ── ITEM 2: Rubric-driven scoring ──
        print("\n[Item 2] Rubric-driven scoring")
        import ast as _ast
        with open('D:/PRD/ai-coach/backend/app/ai/coaching_engine.py', encoding='utf-8') as f:
            src = f.read()
        has_rubric_method = '_extract_rubric_scores_from_feedback' in src
        has_placeholder_only = '_generate_placeholder_scores' in src
        uses_rubric = '_extract_rubric_scores_from_feedback(feedback_text, rubric)' in src
        if has_rubric_method and uses_rubric:
            mark(2, "PARTIAL", f"Rubric scoring method implemented and wired. Needs AI call to fully verify score varies by feedback quality.")
        elif has_placeholder_only and not has_rubric_method:
            mark(2, "NOT_STARTED", "Still using placeholder scores only")
        else:
            mark(2, "PARTIAL", f"has_rubric_method={has_rubric_method} uses_rubric={uses_rubric}")

        # ── ITEM 3: Roleplay feedback report generation ──
        print("\n[Item 3] Roleplay feedback reports")
        with open('D:/PRD/ai-coach/backend/app/api/v1/routers/sessions.py', encoding='utf-8') as f:
            sess_src = f.read()
        has_roleplay_feedback = 'roleplay_id=session_id' in sess_src
        has_roleplay_complete_logic = 'RoleplayMessage' in sess_src and 'roleplay_id' in sess_src
        if has_roleplay_feedback and has_roleplay_complete_logic:
            mark(3, "PARTIAL", "Roleplay complete endpoint generates feedback. Needs live AI to verify score > 0.")
        else:
            mark(3, "NOT_STARTED", f"has_roleplay_feedback={has_roleplay_feedback}")

        # ── ITEM 4: Real analytics aggregations ──
        print("\n[Item 4] Real analytics aggregations")
        r = await c.get("/api/v1/analytics/dashboard", headers=auth)
        if r.status_code == 200:
            d = r.json()
            keys = list(d.keys())
            mark(4, "PARTIAL", f"Dashboard returns {keys[:5]}. Values may be 0 for new user — queries run against DB.")
        else:
            mark(4, "FAILED", f"HTTP {r.status_code}")

        # ── ITEM 5: Citation display in feedback reports ──
        print("\n[Item 5] Citation display")
        import asyncpg
        try:
            conn = await asyncpg.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')
            row = await conn.fetchrow("SELECT id, citations, knowledge_used FROM feedback_reports WHERE overall_score > 0 LIMIT 1")
            await conn.close()
            if row:
                cites = row['citations']
                mark(5, "PARTIAL", f"feedback_reports.citations column exists, value={str(cites)[:60]}. RAG disabled without pgvector.")
            else:
                mark(5, "PARTIAL", "No completed feedback reports with score > 0 yet. Schema supports citations.")
        except Exception as e:
            mark(5, "PARTIAL", f"DB check error: {e}")

        # ── ITEM 6: pgvector / RAG ──
        print("\n[Item 6] pgvector / RAG integration")
        try:
            conn = await asyncpg.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')
            exts = await conn.fetch("SELECT extname FROM pg_extension")
            ext_names = [r['extname'] for r in exts]
            await conn.close()
            if 'vector' in ext_names:
                mark(6, "COMPLETE", f"pgvector installed: {ext_names}")
            else:
                mark(6, "FAILED", f"pgvector NOT installed. Extensions: {ext_names}. RAG pipeline code exists but vector search disabled.")
        except Exception as e:
            mark(6, "FAILED", f"Cannot check: {e}")

        # ── ITEM 7: Frontend dynamic rendering ──
        print("\n[Item 7] Frontend dynamic intake form rendering")
        with open('D:/PRD/ai-coach/frontend/src/pages/CoachingSession.tsx', encoding='utf-8') as f:
            tsx = f.read()
        is_dynamic = 'intake_schema' in tsx and 'intakeFields.map' in tsx
        is_hardcoded = "key: 'situation'" in tsx and 'session?.intake_schema' not in tsx
        if is_dynamic:
            mark(7, "COMPLETE", "CoachingSession.tsx reads session.intake_schema and renders fields dynamically via map()")
        elif is_hardcoded:
            mark(7, "FAILED", "Still hardcoded SBI fields")
        else:
            mark(7, "PARTIAL", "Partial dynamic rendering")

    # ── SUMMARY TABLE ──
    print("\n" + "="*80)
    print(f"{'Item':<6} {'Requirement':<40} {'Status':<12} Evidence")
    print("-"*80)
    req_names = {
        1: "Dynamic intake form (SBI)",
        "1b": "Dynamic intake form (GROW)",
        2: "Rubric-driven scoring",
        3: "Roleplay feedback reports",
        4: "Real analytics aggregations",
        5: "Citations in feedback reports",
        6: "pgvector / RAG integration",
        7: "Frontend dynamic rendering",
    }
    complete = partial = failed = not_started = 0
    for item, (status, evidence) in results.items():
        req = req_names.get(item, str(item))
        print(f"{str(item):<6} {req:<40} {status:<12} {evidence[:40]}")
        if status == "COMPLETE": complete += 1
        elif status == "PARTIAL": partial += 1
        elif status == "FAILED": failed += 1
        else: not_started += 1
    print("-"*80)
    print(f"COMPLETE: {complete}  PARTIAL: {partial}  FAILED: {failed}  NOT_STARTED: {not_started}")

asyncio.run(run())
