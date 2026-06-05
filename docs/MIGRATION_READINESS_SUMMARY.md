# Migration Readiness Summary
## Pre-Generation Checkpoint — Executive Overview

**Date:** 2026-06-04  
**Status:** 🔴 **NOT READY** — Awaiting Approvals on 5 Critical Decisions  
**Documents Generated:**
- `MIGRATION_ARCHITECTURE_REVIEW.md` (919 lines) — Complete migration design
- `MIGRATION_BLOCKING_ISSUES_ANALYSIS.md` (798 lines) — Detailed P1-P3 investigation

---

## TL;DR — What Must Happen Next

**3 code changes + 2 strategic decisions = GO for migration generation**

### Code Changes (Blocking)
1. **Delete `app/models/feedback.py`** after merging 4 useful fields into `session.py`
2. **Update `alembic/env.py`** to support non-transactional migrations for HNSW
3. **Decide now:** Partition `analytics_events`, `api_usage_logs`, `audit_logs` in first migration? (Retrofitting later = 12-hour downtime)

### Strategic Approvals Needed
- Confirm which 4 `FeedbackReport` columns to merge (recommendation: all 4)
- Confirm which 2 `Rubric` columns to merge (recommendation: both)

---

## Priority 1 — feedback.py Conflict (CRITICAL)

### Problem
Two files define the exact same table names with incompatible schemas:
- `app/models/feedback.py` → defines `feedback_reports` + `rubrics` (OLD, not imported anywhere)
- `app/models/session.py` → defines `feedback_reports` (CANONICAL, registered in `__init__.py`)
- `app/models/module.py` → defines `rubrics` (CANONICAL, registered in `__init__.py`)

### Impact if Not Resolved
Alembic autogenerate will attempt to create duplicate tables and fail with:
```
sqlalchemy.exc.InvalidRequestError: Table 'feedback_reports' is already defined
```

### Resolution
**Delete `app/models/feedback.py` entirely.**
File is imported by nothing. Zero consumers. Safe to delete right now.

**BUT FIRST: Merge 4 valuable columns into canonical models:**

#### Add to `session.py` FeedbackReport:
```python
raw_ai_response: Mapped[Optional[str]]  # LLM debugging
user_rating: Mapped[Optional[int]]      # 1-5 star reaction
user_notes: Mapped[Optional[str]]       # learner annotation
next_steps: Mapped[Optional[str]]       # actionable next step
```

#### Add to `module.py` Rubric:
```python
description: Mapped[Optional[str]]      # admin UI display
change_notes: Mapped[Optional[str]]     # wording change notes
```

### Approval Required
**Confirm:** Add all 6 fields above, then delete `feedback.py`?
- ☐ YES, merge and delete
- ☐ NO, explain alternate plan

---

## Priority 2 — Partitioning Decision (STRATEGIC)

### Problem
Three high-velocity tables will accumulate 100M-1.8B rows within 12 months:
- `analytics_events` — 5M events/day = 1.8B rows/year
- `api_usage_logs` — 2M logs/day = 730M rows/year
- `audit_logs` — 100k audits/day = 36M rows/year

Without partitioning: queries slow to 30+ seconds, VACUUM takes hours, eventual table unqueryable.

### Decision Point
**Option A (Recommended):** Implement monthly range partitioning NOW in the first migration.
- Cost now: 1 explicit migration file + 20 lines of DDL per table
- Cost later: 12-hour downtime + double disk space during retrofit
- Risk: Low (partitions are invisible to ORM)

**Option B:** Skip partitioning for MVP.
- Accept risk of disruptive migration in 6-12 months
- No complexity added now

### Recommendation
**Partition NOW.** Retrofitting a 100M-row table to partitioned requires dump → drop → recreate → reload = 2-12 hours downtime + 2× disk during transition.

### Approval Required
**Confirm:** Implement partitioning in first migration?
- ☐ Option A — Partition now (recommended)
- ☐ Option B — Skip for MVP

---

## Priority 3 — HNSW Index Build (TECHNICAL)

### Problem
HNSW index on `knowledge_chunks.embedding` (100k vectors = 10-15 min build) cannot be built inside a transaction. Alembic defaults to transactional migrations.

### Solution
Isolate HNSW in a separate migration file with `CREATE INDEX CONCURRENTLY` and run in AUTOCOMMIT mode via `-x non_transactional=true` flag.

### Required Code Changes
**Update `alembic/env.py`:** Add support for `-x non_transactional=true` flag to disable transaction wrapping.

**New migration file:** `010_create_hnsw_index.py` contains only:
```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kb_chunks_embedding
ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

### Invocation
```bash
# Normal migrations (transactional)
alembic upgrade head

# HNSW index separately (non-transactional, during maintenance window)
alembic upgrade 010_create_hnsw_index -x non_transactional=true
```

### Approval Required
**Confirm:** Use the isolated HNSW migration pattern above?
- ☐ YES, isolate HNSW + update env.py
- ☐ Alternative approach (explain)

---

## Migration Execution Roadmap (After Approvals)

### Phase 2B — Code Changes (2-4 hours)
1. Add 4 columns to `session.py` FeedbackReport
2. Add 2 columns to `module.py` Rubric
3. Delete `app/models/feedback.py`
4. Update `alembic/env.py` with non_transactional support
5. (If approved) Add partition declarations to analytics ORM models

### Phase 2C — Migration Generation (30 min)
```bash
alembic revision --autogenerate -m "initial schema"
```
Review generated migration, split into logical files:
- `001_create_extensions.py`
- `002_create_base_tables.py`
- `003_create_module_tables.py`
- `004_create_knowledge_tables.py`
- `005_create_session_tables.py`
- `006_create_analytics_tables.py` (with partition DDL if approved)
- `007_create_progress_gamification.py`
- `008_create_all_indexes.py`
- `009_enable_rls.py`
- `010_create_hnsw_index.py` (isolated, non-transactional)
- `011_seed_roles_permissions.py`
- `012_seed_achievements.py`

### Phase 2D — Testing (4-8 hours)
1. Test on empty local DB: `alembic upgrade head`
2. Test RLS policies: verify tenant isolation
3. Test HNSW build: synthetic 10k vectors
4. Test partitioning: verify inserts route to correct partition
5. Test rollback: `alembic downgrade -1` for each migration

### Phase 2E — Production Readiness (2-4 hours)
1. Dry-run on staging clone
2. Document maintenance window plan (2-4 hours)
3. Prepare rollback SQL scripts
4. Set up monitoring: pg_stat_progress_create_index

---

## Go/No-Go Checklist

| Item | Status | Blocker? |
|------|--------|----------|
| P1: feedback.py conflict analysed | ✅ DONE | N/A |
| P1: Merge fields into canonical models | ⏸️ AWAITING APPROVAL | YES |
| P1: Delete feedback.py | ⏸️ BLOCKED BY ABOVE | YES |
| P2: Partitioning decision | ⏸️ AWAITING APPROVAL | YES (strategic) |
| P3: HNSW env.py update designed | ✅ DONE | N/A |
| P3: HNSW env.py code changes | ⏸️ AWAITING APPROVAL | YES |

**Current Gate Status:** 🔴 RED — 3 blocking approvals required

---

## Next Steps

1. **Review both analysis documents:**
   - `MIGRATION_ARCHITECTURE_REVIEW.md` — 32-table execution plan
   - `MIGRATION_BLOCKING_ISSUES_ANALYSIS.md` — P1-P3 deep-dive

2. **Provide approvals on Section 5.3:**
   - Approval A — feedback.py deletion
   - Approval B — partitioning strategy (A or B)
   - Approval C — HNSW isolation strategy
   - Approval D — FeedbackReport column merge list
   - Approval E — Rubric column merge list

3. **Once all approvals received:**
   Execute code changes (Phase 2B), then **PROCEED TO PHASE 2B: MIGRATION FILE GENERATION**

---

*Generated: 2026-06-04 | Documents: 2 | Total analysis: 1717 lines*
