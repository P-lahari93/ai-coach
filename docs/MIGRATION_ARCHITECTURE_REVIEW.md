# Migration Architecture Review
## AI Coach Platform — All Batches 1–4

**Status:** Pre-migration analysis. No migration files generated.
**Scope:** All ORM models across Batch 1 (Tenant/User/RBAC), Batch 2 (Module),
Batch 3 (Session/Knowledge/Analytics), Batch 4 (Progress/Gamification/Notification).

---

## Section 1: Migration Execution Order

Migrations must be applied in a single ordered sequence across **4 logical phases**.
Each phase groups tables that share the same dependency tier.

### Phase 1 — Extensions & Foundation (no table dependencies)
```
1.1  CREATE EXTENSION IF NOT EXISTS "pgcrypto"     -- gen_random_uuid()
1.2  CREATE EXTENSION IF NOT EXISTS "uuid-ossp"    -- uuid_generate_v4() fallback
1.3  CREATE EXTENSION IF NOT EXISTS "vector"       -- pgvector for HNSW embeddings
1.4  CREATE EXTENSION IF NOT EXISTS "pg_trgm"      -- (optional) trigram for text search
```

### Phase 2 — Root Tables (no FK dependencies)
```
2.1  tenants
2.2  users
2.3  permissions
2.4  roles
```

### Phase 3 — First-Order Join & Extension Tables
```
3.1  tenant_settings          (FK → tenants)
3.2  role_permissions         (FK → roles, permissions)
3.3  user_roles               (FK → users, roles, tenants)
3.4  user_tenants             (FK → users, tenants)
3.5  refresh_tokens           (FK → users)
```

### Phase 4 — Module Domain
```
4.1  coaching_modules         (FK → tenants, users)
4.2  module_versions          (FK → coaching_modules, users)
4.3  module_framework_steps   (FK → module_versions)
4.4  module_prompt_templates  (FK → module_versions)
4.5  module_personas          (FK → module_versions)
4.6  rubrics                  (FK → module_versions)
```

### Phase 5 — Knowledge Domain
```
5.1  knowledge_bases          (FK → tenants, users, coaching_modules)
5.2  module_knowledge_bases   (FK → coaching_modules, knowledge_bases)
5.3  knowledge_sources        (FK → knowledge_bases, users)
5.4  knowledge_chunks         (FK → knowledge_bases, knowledge_sources, tenants)
```

### Phase 6 — Session Domain
```
6.1  coaching_sessions        (FK → users, coaching_modules, module_versions, tenants)
6.2  conversation_messages    (FK → coaching_sessions)
6.3  roleplay_sessions        (FK → users, coaching_modules, module_versions,
                                     module_personas, tenants)
6.4  roleplay_messages        (FK → roleplay_sessions)
6.5  feedback_reports         (FK → coaching_sessions, roleplay_sessions,
                                     users, tenants, rubrics)
```

### Phase 7 — Tracking & Engagement
```
7.1  user_progress            (FK → users, coaching_modules, tenants)
7.2  achievements             (FK → tenants)
7.3  user_achievements        (FK → users, achievements, tenants)
7.4  notifications            (FK → users, tenants)
```

### Phase 8 — Analytics & Audit (append-only / loose FK)
```
8.1  analytics_events         (FK → users[SET NULL], tenants[SET NULL])
8.2  audit_logs               (FK → users[SET NULL], tenants[SET NULL])
8.3  api_usage_logs           (NO FK — loose references only)
8.4  ai_generations           (FK → users[SET NULL], tenants[SET NULL])
```

### Phase 9 — Post-Table Structural Objects
```
9.1  All standard B-tree indexes (from __table_args__ Index() declarations)
9.2  All partial indexes (WHERE clause indexes)
9.3  All composite indexes
9.4  HNSW vector index on knowledge_chunks.embedding
9.5  Row Level Security policies (per table)
9.6  Seed data (roles, permissions, system config)
```

---

## Section 2: PostgreSQL Extensions Required

All extensions must be created before any table DDL executes.
They must be created with `IF NOT EXISTS` to be idempotent on re-run.

### 2.1 pgcrypto
```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
```
**Required for:** `gen_random_uuid()` — used as server_default on every UUID PK
column across all models. Without this, UUIDs are only generated client-side and
raw SQL inserts (seeds, data migrations) will fail.
**Risk if missing:** All server_default UUID generation silently fails;
rows inserted via `psql` or seed scripts get NULL primary keys.

### 2.2 uuid-ossp
```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```
**Required for:** `uuid_generate_v4()` — backup UUID function referenced by some
tooling and legacy compatibility. Not directly used in ORM models but required by
Alembic autogenerate patterns in some configurations.
**Risk if missing:** Low for ORM path; medium for manual SQL operations.

### 2.3 pgvector
```sql
CREATE EXTENSION IF NOT EXISTS "vector";
```
**Required for:** `vector(384)` column type on `knowledge_chunks.embedding`.
Also required for the HNSW index and cosine similarity operator `<=>`.
**Risk if missing:** CRITICAL — `knowledge_chunks` table creation fails entirely.
The entire RAG retrieval system cannot function.
**Version requirement:** pgvector >= 0.5.0 for HNSW index support (IVFFlat was
the only option in earlier versions; HNSW was added in 0.5.0).

### 2.4 pg_trgm (optional, recommended)
```sql
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
```
**Required for:** Trigram text search on `knowledge_chunks.content`, module
name search, and user search. Not directly referenced in current ORM models
but strongly recommended for the search endpoints planned in APIs.
**Risk if missing:** Text search falls back to ILIKE with sequential scans.

### Extension Installation Summary

| Extension  | Required | Used By                          | Risk if Missing |
|------------|----------|----------------------------------|-----------------|
| pgcrypto   | CRITICAL | All UUID server_defaults         | NULL PKs        |
| uuid-ossp  | HIGH     | UUID generation fallback         | Seed failures   |
| vector     | CRITICAL | knowledge_chunks.embedding       | RAG broken      |
| pg_trgm    | OPTIONAL | Future text search               | Search degraded |

---

## Section 3: Table Creation Sequence

Exact ordered list. Each table can only be created after all its FK targets exist.

```
 #  Table Name                  Depends On
─────────────────────────────────────────────────────────────────────────────
 1  tenants                     (none — root)
 2  users                       (none — root)
 3  permissions                 (none — root)
 4  roles                       (none — root)
 5  tenant_settings             tenants
 6  role_permissions            roles, permissions
 7  user_roles                  users, roles, tenants[nullable]
 8  user_tenants                users, tenants
 9  refresh_tokens              users
10  coaching_modules            tenants[nullable], users[nullable SET NULL]
11  module_versions             coaching_modules, users[nullable SET NULL]
12  module_framework_steps      module_versions
13  module_prompt_templates     module_versions
14  module_personas             module_versions
15  rubrics                     module_versions
16  knowledge_bases             tenants, users[nullable SET NULL],
                                coaching_modules[nullable]
17  module_knowledge_bases      coaching_modules, knowledge_bases
18  knowledge_sources           knowledge_bases, users[nullable SET NULL]
19  knowledge_chunks            knowledge_bases, knowledge_sources, tenants
20  coaching_sessions           users, coaching_modules[RESTRICT],
                                module_versions[RESTRICT], tenants[nullable]
21  conversation_messages       coaching_sessions
22  roleplay_sessions           users, coaching_modules[RESTRICT],
                                module_versions[RESTRICT],
                                module_personas[nullable SET NULL],
                                tenants[nullable]
23  roleplay_messages           roleplay_sessions
24  feedback_reports            coaching_sessions[nullable],
                                roleplay_sessions[nullable],
                                users, tenants[nullable],
                                rubrics[nullable SET NULL]
25  user_progress               users, coaching_modules, tenants[nullable]
26  achievements                tenants[nullable]
27  user_achievements           users, achievements, tenants[nullable]
28  notifications               users, tenants[nullable]
29  analytics_events            users[nullable SET NULL],
                                tenants[nullable SET NULL]
30  audit_logs                  users[nullable SET NULL],
                                tenants[nullable SET NULL]
31  api_usage_logs              (no FK constraints — loose references)
32  ai_generations              users[nullable SET NULL],
                                tenants[nullable SET NULL]
─────────────────────────────────────────────────────────────────────────────
```

### Critical Dependency Notes

**coaching_modules ↔ knowledge_bases (mutual reference):**
`coaching_modules` references `knowledge_bases` via `KnowledgeBase.module_id`.
`knowledge_bases` also references `coaching_modules` via `KnowledgeBase.module_id`.
Resolution: `coaching_modules` is created first (row 10). `knowledge_bases.module_id`
FK is added as an ALTER TABLE after `coaching_modules` exists. Since `coaching_modules`
also has no FK to `knowledge_bases` directly (it uses a join table `module_knowledge_bases`),
there is no circular dependency at the DDL level. The `module_id` FK on `knowledge_bases`
can be declared inline at table creation time (row 16) because `coaching_modules`
already exists.

**feedback_reports XOR constraint:**
`feedback_reports` has FKs to both `coaching_sessions` and `roleplay_sessions`.
Both must exist before `feedback_reports` can be created (rows 20, 22 before row 24).
The XOR CHECK `(session_id IS NOT NULL AND roleplay_id IS NULL) OR (session_id IS NULL
AND roleplay_id IS NOT NULL)` must be declared at CREATE TABLE time.

**feedback.py vs session.py conflict:**
Two separate files define `FeedbackReport` and `Rubric`:
- `app/models/session.py` — canonical versions used by the ORM
- `app/models/feedback.py` — legacy/alternative definitions with different schema

**This is a CRITICAL conflict.** Both cannot be mapped simultaneously.
`feedback.py` must be deprecated and all imports redirected to `session.py` before
migration generation. The `feedback.py` `FeedbackReport` uses `use_alter=True` and
a string UUID pattern (not `as_uuid=True`) — incompatible with the `session.py`
design. Resolution required before Phase 2B.

---

## Section 4: Index Creation Strategy

Indexes are created **after** all tables exist, in a separate migration phase
to avoid index-build overhead blocking table creation in a single transaction.

### 4.1 Standard B-tree Indexes

All indexes declared via `Index()` in `__table_args__` that do NOT have a
`postgresql_where=` clause.

#### Pattern
```sql
CREATE INDEX idx_<table>_<columns> ON <table> (<col1>, <col2>, ...);
```

#### Example Coverage
- `idx_tenants_slug` — tenant lookup by slug
- `idx_users_email_active` — UNIQUE partial index (see 4.2)
- `idx_coaching_modules_tenant_status` — filtered module list
- `idx_coaching_sessions_user_created` — user session history
- `idx_kb_chunks_tenant_kb` — composite pre-filter for HNSW

Total estimated: ~85 standard B-tree indexes across 32 tables.

---

### 4.2 Partial Indexes (WHERE clause)

Partial indexes dramatically reduce index size and improve write performance
by only indexing rows matching a predicate.

#### Critical Partial Indexes

**4.2.1 Soft-delete active-record indexes**
Every table with soft-delete (`deleted_at`) needs a partial index on its
primary lookup columns filtered by `WHERE deleted_at IS NULL`.

```sql
-- Example: users table
CREATE UNIQUE INDEX idx_users_email_active
ON users (email)
WHERE deleted_at IS NULL;
```
**Rationale:** Without this, querying active users requires scanning the
entire `users` table (including all soft-deleted rows). At 100k users with
20% historical soft-deletes, that's 20k unnecessary rows scanned per lookup.

**Tables requiring soft-delete partial indexes:**
- tenants, users, coaching_modules, module_versions (via ModuleVersion.is_current),
  knowledge_bases, coaching_sessions, roleplay_sessions, + all other BusinessBase
  inheritors (total: ~16 tables).

**4.2.2 NULL-safe uniqueness constraints**
PostgreSQL treats `NULL != NULL` in standard UNIQUE constraints, breaking
uniqueness for nullable FK columns. Partial indexes enforce correct uniqueness.

```sql
-- user_progress: one row per (user, module, tenant) scope
CREATE UNIQUE INDEX uq_user_progress_no_tenant
ON user_progress (user_id, module_id)
WHERE tenant_id IS NULL;

CREATE UNIQUE INDEX uq_user_progress_with_tenant
ON user_progress (user_id, module_id, tenant_id)
WHERE tenant_id IS NOT NULL;
```

**Tables requiring NULL-safe uniqueness:**
- `user_progress` (user, module, tenant)
- `user_achievements` (user, achievement, tenant)
- `achievements` (key, tenant)
- `coaching_modules` (key, tenant)

**4.2.3 is_current / is_default singleton indexes**
Enforce that only ONE row per group can have a boolean flag set to `true`.

```sql
-- Only one module_version per coaching_module can be is_current=true
CREATE UNIQUE INDEX uq_module_one_current_version
ON module_versions (module_id)
WHERE is_current = true;
```

**Tables requiring singleton indexes:**
- `module_versions.is_current` (one per module)
- `module_personas.is_default` (one per module_version)

---

### 4.3 Composite Indexes

Multi-column indexes for common query patterns. Order matters: most selective
column first, then the filter column, then the sort column.

```sql
-- Dashboard: "my completed sessions, newest first"
CREATE INDEX idx_coaching_sessions_user_tenant_status
ON coaching_sessions (user_id, tenant_id, status, created_at);
```

**Critical composites:**
- `(user_id, tenant_id, status, created_at)` — coaching_sessions, roleplay_sessions
- `(tenant_id, event_type, occurred_at)` — analytics_events
- `(actor_user_id, tenant_id, created_at)` — audit_logs (compliance queries)
- `(tenant_id, kb_id)` — knowledge_chunks (RAG pre-filter before HNSW)

Total estimated: ~25 composite indexes.

---

### 4.4 HNSW Vector Index

**Critical for RAG retrieval performance.** Must be created AFTER the table is
populated with embeddings (if seeding); creating HNSW on an empty table is
instant but on 100k+ vectors takes 5-30 minutes depending on `ef_construction`.

```sql
CREATE INDEX idx_kb_chunks_embedding
ON knowledge_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Tuning parameters:**
- `m = 16` — graph connectivity; 16 is default, 32 improves recall +20% but
  doubles index memory footprint.
- `ef_construction = 64` — build-time candidate list size; must be >= 2*m.
  Higher = better recall at cost of slower index creation.
- `ef_search` — query-time parameter set via `SET LOCAL hnsw.ef_search = 100;`
  in each retrieval query. 100 is production default, 200 for high-precision.

**Index size estimate:**
384-dim float32 vectors × 100k chunks × ~2KB per vector (HNSW graph overhead)
= ~20GB index size at scale. Plan for this in disk/memory provisioning.

**Build time estimate:**
~10-15 minutes for 100k vectors with ef_construction=64 on modern PostgreSQL
hardware (8 vCPU, 32GB RAM). Build serially, not in parallel with other indexes.

---

## Section 5: Row Level Security (RLS) Strategy

RLS enforces tenant isolation at the database layer, preventing cross-tenant
data leakage even if application code has a bug. Every query is automatically
filtered by the tenant_id set on the connection.

### 5.1 RLS Activation Pattern

For each multi-tenant table (any table with a `tenant_id` column):

```sql
-- Step 1: Enable RLS on the table
ALTER TABLE <table_name> ENABLE ROW LEVEL SECURITY;

-- Step 2: Create policy for tenant-scoped reads
CREATE POLICY tenant_isolation_policy ON <table_name>
FOR SELECT
USING (tenant_id = current_setting('app.current_tenant_id')::uuid);

-- Step 3: Create policy for tenant-scoped writes
CREATE POLICY tenant_isolation_write_policy ON <table_name>
FOR ALL
USING (tenant_id = current_setting('app.current_tenant_id')::uuid);

-- Step 4: Allow superadmin bypass (optional, use with caution)
CREATE POLICY superadmin_bypass_policy ON <table_name>
FOR ALL
USING (current_setting('app.is_superadmin', true) = 'true');
```

### 5.2 Connection Setup

Every API request sets the tenant context immediately after acquiring a
DB connection from the pool:

```python
# FastAPI middleware pattern
async with db_session() as session:
    await session.execute(
        text("SET LOCAL app.current_tenant_id = :tid"),
        {"tid": str(current_user.tenant_id)}
    )
    await session.execute(
        text("SET LOCAL app.is_superadmin = :flag"),
        {"flag": "true" if current_user.is_superadmin else "false"}
    )
    # ... proceed with request handler
```

**SET LOCAL** ensures the setting is transaction-scoped and automatically
cleared when the connection is returned to the pool.

### 5.3 Tables Requiring RLS

All tables with a `tenant_id` column must have RLS enabled:

**Tenant-scoped tables (18 tables):**
- tenants, tenant_settings
- coaching_modules, module_versions, module_framework_steps,
  module_prompt_templates, module_personas, rubrics
- knowledge_bases, knowledge_sources, knowledge_chunks
- coaching_sessions, roleplay_sessions, feedback_reports
- user_progress, achievements, user_achievements, notifications

**Nullable tenant_id (global + tenant):**
Tables where `tenant_id` can be NULL (platform-level records) require a
modified policy:

```sql
CREATE POLICY tenant_isolation_policy ON coaching_modules
FOR SELECT
USING (
  tenant_id IS NULL  -- global modules visible to all
  OR tenant_id = current_setting('app.current_tenant_id')::uuid
);
```

### 5.4 Admin Bypass

Superadmin users bypass RLS to access cross-tenant data for platform
administration. This is controlled by the `app.is_superadmin` session variable.

**SECURITY CRITICAL:** Superadmin flag must ONLY be set when:
1. The authenticated user has `users.is_superadmin = true`
2. The request is to an admin-scoped endpoint (`/admin/*`)
3. The action is audit-logged in `audit_logs`

Never set `app.is_superadmin = true` for regular user requests.

### 5.5 Service Account Access

Background jobs (embedding workers, notification schedulers, analytics
aggregators) run as a service account with RLS bypassed:

```sql
-- Service role setup (run once at DB init)
CREATE ROLE service_account WITH LOGIN PASSWORD '<secret>';
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_account;
ALTER ROLE service_account SET row_security = off;
```

Service accounts do NOT set `app.current_tenant_id` and operate across all
tenants. Every operation by a service account must be audit-logged.

### 5.6 RLS Performance Impact

**Read queries:** ~5-10% overhead due to the `tenant_id = ?` filter being
injected into every query. Mitigated by composite indexes with `tenant_id`
as the leading column.

**Write queries:** Negligible overhead (<1%).

**Monitoring:** Use `EXPLAIN ANALYZE` on slow queries to verify the
`tenant_id` filter is using an index scan, not a sequential scan.

### 5.7 RLS Testing

Every repository test must include:
```python
# Set tenant context
await session.execute(text("SET LOCAL app.current_tenant_id = :tid"), {"tid": tenant_a.id})

# Query should only return tenant_a records
results = await repo.list_modules()
assert all(m.tenant_id == tenant_a.id for m in results)

# Cross-tenant query must return empty
await session.execute(text("SET LOCAL app.current_tenant_id = :tid"), {"tid": tenant_b.id})
result = await repo.get_module(module_from_tenant_a.id)
assert result is None  # RLS blocks access
```

---

## Section 6: Seed Data Strategy

Seed data must be inserted AFTER all tables and indexes exist, but BEFORE
the application starts serving traffic. Seeds are idempotent: safe to re-run.

### 6.1 Default Roles & Permissions

**Execution order:**
1. Insert Permissions (atomic permission definitions)
2. Insert Roles (named sets)
3. Insert RolePermissions (join table)

**Seed SQL pattern:**
```sql
-- Permissions (all resource:action pairs used by the platform)
INSERT INTO permissions (id, resource, action, description, created_at, updated_at)
VALUES
  (gen_random_uuid(), 'module', 'read', 'View module definitions', now(), now()),
  (gen_random_uuid(), 'module', 'create', 'Create new modules', now(), now()),
  (gen_random_uuid(), 'module', 'publish', 'Publish module versions', now(), now()),
  (gen_random_uuid(), 'session', 'create', 'Start coaching sessions', now(), now()),
  (gen_random_uuid(), 'session', 'read', 'View own session history', now(), now()),
  (gen_random_uuid(), 'feedback', 'read', 'View AI feedback reports', now(), now()),
  (gen_random_uuid(), 'knowledge_base', 'manage', 'Upload and manage KB', now(), now()),
  (gen_random_uuid(), 'user', 'invite', 'Invite users to tenant', now(), now()),
  (gen_random_uuid(), 'tenant', 'configure', 'Modify tenant settings', now(), now()),
  (gen_random_uuid(), 'report', 'read', 'View analytics dashboards', now(), now())
ON CONFLICT (resource, action) DO NOTHING;

-- Roles
INSERT INTO roles (id, name, scope, description, is_system, created_at, updated_at)
VALUES
  (gen_random_uuid(), 'superadmin', 'global', 
   'Platform-wide administrator. Bypasses all tenant checks.', true, now(), now()),
  (gen_random_uuid(), 'tenant_admin', 'tenant',
   'Full control within their tenant. Cannot access other tenants.', true, now(), now()),
  (gen_random_uuid(), 'program_owner', 'tenant',
   'Can create and publish modules within their tenant.', true, now(), now()),
  (gen_random_uuid(), 'learner', 'tenant',
   'Can start sessions, view feedback, earn achievements.', true, now(), now())
ON CONFLICT (name) DO NOTHING;

-- RolePermissions (grant permissions to roles)
-- Example: learner role
INSERT INTO role_permissions (role_id, permission_id, created_at, updated_at)
SELECT r.id, p.id, now(), now()
FROM roles r
CROSS JOIN permissions p
WHERE r.name = 'learner'
  AND p.resource IN ('session', 'feedback')
  AND p.action IN ('create', 'read')
ON CONFLICT (role_id, permission_id) DO NOTHING;
```

**Estimated seed count:**
- Permissions: ~30 rows (10 resources × ~3 actions each)
- Roles: 4 rows (superadmin, tenant_admin, program_owner, learner)
- RolePermissions: ~60 rows (varies by role definition)

### 6.2 System Configuration

Global platform settings stored in a dedicated config table (to be added):

```sql
-- Platform config (single-row table or JSONB in a settings row)
INSERT INTO platform_config (key, value, description)
VALUES
  ('default_ollama_model', 'qwen3:4b', 'Default LLM model for coaching'),
  ('rag_similarity_threshold', '0.65', 'Minimum cosine similarity for RAG retrieval'),
  ('max_feedback_tokens', '2048', 'Max tokens in a feedback response'),
  ('session_timeout_minutes', '30', 'Inactive session auto-abandon threshold'),
  ('notification_retention_days', '90', 'Days to keep read notifications')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

**Note:** Platform config table does not yet exist in current ORM models.
Add to Batch 1 or create as a standalone migration before seed phase.

### 6.3 Default Achievements

Global achievements seeded at platform launch (tenant_id = NULL):

```sql
INSERT INTO achievements (id, key, name, description, icon, points, criteria, tenant_id, is_active, created_at, updated_at)
VALUES
  (gen_random_uuid(), 'first_session', 'First Steps',
   'Complete your first coaching session', 'Award', 10,
   '{"type": "session_count", "threshold": 1}'::jsonb, NULL, true, now(), now()),
  (gen_random_uuid(), 'score_90_plus', 'Excellence',
   'Achieve a score of 90 or higher', 'Star', 50,
   '{"type": "score_threshold", "threshold": 90}'::jsonb, NULL, true, now(), now()),
  (gen_random_uuid(), 'seven_day_streak', 'Consistency Champion',
   'Maintain a 7-day learning streak', 'Flame', 100,
   '{"type": "streak_days", "threshold": 7}'::jsonb, NULL, true, now(), now())
ON CONFLICT (key) WHERE tenant_id IS NULL DO NOTHING;
```

**Estimated seed count:** 8-12 global achievements.

### 6.4 Sample Modules (Optional for Demo)

Pre-built SBI Feedback and GROW Coaching modules seeded for demo tenants:

```sql
-- Coaching module (global, tenant_id = NULL)
INSERT INTO coaching_modules (id, key, name, icon, blurb, tenant_id, status, created_at, updated_at, version)
VALUES
  (gen_random_uuid(), 'sbi_feedback', 'SBI Feedback',
   'MessageSquare', 'Practice structured feedback using Situation-Behaviour-Impact',
   NULL, 'published', now(), now(), 1)
ON CONFLICT (key) WHERE tenant_id IS NULL DO NOTHING;

-- Module version + framework steps + prompts + persona + rubric
-- (Full seed SQL ~200 lines — separate file: seeds/sbi_module.sql)
```

**Decision:** Demo module seeds are OPTIONAL for MVP. Can be added in a
post-launch data migration or loaded via the admin UI instead of hardcoding
in migrations.

### 6.5 Seed Execution Strategy

**Idempotency:** All seed INSERTs use `ON CONFLICT ... DO NOTHING` or
`ON CONFLICT ... DO UPDATE` to be safe for re-runs.

**Ordering:**
1. Extensions (Phase 1)
2. Tables + Indexes (Phases 2-9)
3. Seed Permissions (Phase 10.1)
4. Seed Roles (Phase 10.2)
5. Seed RolePermissions (Phase 10.3)
6. Seed Achievements (Phase 10.4)
7. Seed Platform Config (Phase 10.5)
8. [Optional] Seed Demo Modules (Phase 10.6)

**Migration file structure:**
```
alembic/versions/
  001_create_extensions.py
  002_create_base_tables.py
  003_create_module_tables.py
  ...
  010_create_indexes.py
  011_enable_rls_policies.py
  012_seed_roles_permissions.py      # Phase 10.1-10.3
  013_seed_achievements.py           # Phase 10.4
  014_seed_platform_config.py        # Phase 10.5
  [015_seed_demo_modules.py]         # Optional Phase 10.6
```

---

## Section 7: Migration Risk Assessment

### 7.1 Blocking Migrations (Cannot Be Rolled Back Easily)

**RISK LEVEL: HIGH**

#### 7.1.1 HNSW Index Creation
**Table:** `knowledge_chunks.embedding`
**Risk:** Index creation on large tables locks the table for writes.
At 100k+ vectors, build time is 10-30 minutes. If the build fails mid-way
(OOM, disk full, interrupted connection), the entire index build must restart.

**Mitigation:**
- Create the HNSW index with `CONCURRENTLY` (if supported in pgvector >= 0.5.1):
  ```sql
  CREATE INDEX CONCURRENTLY idx_kb_chunks_embedding
  ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
  ```
- Run during a maintenance window, not during live traffic.
- Verify available disk space: `df -h` must show > 30GB free before starting.
- Monitor build progress: `SELECT * FROM pg_stat_progress_create_index;`

**Rollback:** Drop the index. No data loss, but retrieval queries will be
extremely slow (sequential scan on millions of rows) until the index is rebuilt.

---

#### 7.1.2 Row Level Security Activation
**Tables:** All 18 tenant-scoped tables
**Risk:** Once RLS is enabled, any connection that does NOT set
`app.current_tenant_id` will see ZERO rows (empty result set) even if the
table contains data. This breaks background jobs, admin tools, and any direct
`psql` queries until the session variable is set.

**Mitigation:**
- Test RLS policies on a staging DB clone before production.
- Create the `service_account` role (with `row_security = off`) BEFORE
  enabling RLS on any table.
- Document the session variable requirement in README and wiki.
- Add connection pool middleware that auto-sets the variable for all requests.

**Rollback:** `ALTER TABLE <table> DISABLE ROW LEVEL SECURITY;` is instant and
non-destructive. Data is not affected.

---

#### 7.1.3 feedback.py vs session.py ORM Conflict
**Risk:** CRITICAL — BLOCKING MIGRATION GENERATION
Two files define conflicting `FeedbackReport` models:
- `app/models/session.py` — canonical, used in imports
- `app/models/feedback.py` — legacy/test artifact with incompatible schema

Alembic autogenerate will fail with a "duplicate table" error.

**Resolution Required BEFORE Phase 2B:**
1. Delete or rename `app/models/feedback.py` to `feedback_legacy.py.bak`
2. Update `app/models/__init__.py` to remove `from .feedback import *`
3. Grep codebase for `from app.models.feedback import` and replace with
   `from app.models.session import FeedbackReport, Rubric`
4. Re-run Alembic autogenerate

**Rollback:** Restore the backup file. This is a code-level issue, not a DB issue.

---

### 7.2 Large Table Risks (Slow Operations at Scale)

**RISK LEVEL: MEDIUM**

#### 7.2.1 knowledge_chunks Table Growth
**Projected size:** 100k users × 10 KB sources × 50 chunks/source = 50M rows
**Index size:** HNSW ~20GB, B-tree indexes ~5GB, total ~25GB+ for this table alone.

**Risks:**
- Initial seed or bulk KB upload can take hours.
- Partition strategy (by tenant_id or created_at) not yet defined in ORM.
- Vacuuming a 50M-row table with frequent updates is slow.

**Mitigation:**
- Plan for monthly partitioning by `created_at` in a future migration
  (not MVP, defer to v1.1).
- Use `VACUUM ANALYZE` on a schedule, not after every insert batch.
- Monitor `pg_stat_user_tables` for bloat: if `n_dead_tup` > 10% of `n_live_tup`,
  run manual VACUUM.

**Rollback:** Table can be TRUNCATEd or DROPped without affecting other tables
(knowledge is user-uploaded, not system-critical). Data loss is acceptable
in non-production.

---

#### 7.2.2 analytics_events Table Growth
**Projected size:** 100k users × 50 events/day = 5M events/day = 150M rows/month.

**Risks:**
- Without partitioning, the table becomes unqueryable after 6 months.
- Foreign keys to `users` and `tenants` with `SET NULL` mean orphaned rows
  accumulate forever.

**Mitigation:**
- Implement monthly range partitioning by `occurred_at` in the initial migration:
  ```sql
  CREATE TABLE analytics_events (...)
  PARTITION BY RANGE (occurred_at);

  CREATE TABLE analytics_events_2026_06
  PARTITION OF analytics_events
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
  ```
- Automate partition creation via a scheduled SQL job (pg_cron or app-level).
- Set up a retention policy: drop partitions older than 12 months.

**Rollback:** Partitions can be detached (`ALTER TABLE ... DETACH PARTITION`)
without affecting the parent table or other partitions. Data in detached
partitions remains queryable as standalone tables.

---

### 7.3 Rollback Concerns (Hard to Reverse)

**RISK LEVEL: MEDIUM**

#### 7.3.1 Soft-Delete to Hard-Delete Conversion
**Scenario:** A bug in the soft-delete logic causes the app to hard-delete
(actual DELETE) instead of setting `deleted_at`. All deleted rows are
permanently lost.

**Mitigation:**
- Repository methods must NEVER call `session.delete(obj)` directly.
- All delete operations go through a `soft_delete(obj)` helper that sets
  `deleted_at = now()` and flushes.
- Hard-delete is only allowed via an explicit admin endpoint with double
  confirmation and audit logging.

**Rollback:** Impossible. Hard-deleted rows cannot be recovered without a
database backup. Set up daily automated backups to S3/GCS.

---

#### 7.3.2 HNSW Index Corruption
**Scenario:** Power loss or OOM during HNSW index build leaves the index
in an inconsistent state. Queries return incorrect results (low recall)
or crash the database.

**Mitigation:**
- Build HNSW indexes ONLY during maintenance windows with stable power/network.
- Use `CONCURRENTLY` where supported to avoid locking the table.
- After build completes, run validation queries:
  ```sql
  SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NOT NULL;
  -- Should match the number of indexed rows
  ```
- If validation fails, drop and rebuild the index.

**Rollback:** Drop the index: `DROP INDEX idx_kb_chunks_embedding;`
Rebuilding from scratch is required.

---

### 7.4 Risk Summary Table

| Risk                          | Severity | Impact                     | Mitigation                          |
|-------------------------------|----------|----------------------------|-------------------------------------|
| HNSW index build failure      | HIGH     | RAG retrieval broken       | Use CONCURRENTLY, maintenance window|
| RLS breaks background jobs    | HIGH     | Jobs see empty result sets | Service role with RLS bypassed      |
| feedback.py ORM conflict      | CRITICAL | Migration generation fails | Delete/rename feedback.py first     |
| knowledge_chunks table growth | MEDIUM   | Slow queries at scale      | Partition by created_at (future)    |
| analytics_events growth       | MEDIUM   | Unqueryable after 6 months | Partition by occurred_at (MVP)      |
| Soft-delete bug → hard-delete | MEDIUM   | Permanent data loss        | Repo layer enforces soft-delete     |
| HNSW index corruption         | MEDIUM   | Incorrect search results   | Rebuild from scratch                |

---

### 7.5 Pre-Flight Checklist

Before running migrations in production:

- [ ] Backup the database (pg_dump or snapshot)
- [ ] Verify pgvector extension version >= 0.5.0
- [ ] Verify available disk space >= 50GB free
- [ ] Resolve feedback.py ORM conflict
- [ ] Test RLS policies on staging clone
- [ ] Create service_account role with RLS bypass
- [ ] Schedule maintenance window (2-4 hours for full migration + index builds)
- [ ] Prepare rollback plan (documented SQL to disable RLS, drop indexes)
- [ ] Set up monitoring: pg_stat_progress_create_index, pg_stat_activity
- [ ] Notify team of migration window (no deploys during this period)

---

## Appendix A: Complete ORM Model Inventory

| # | Model Class           | Table Name                | Batch | Base Classes                                    | FK Count |
|---|----------------------|---------------------------|-------|--------------------------------------------------|----------|
| 1 | Tenant               | tenants                   | 1     | BusinessBase, Base                               | 0        |
| 2 | TenantSettings       | tenant_settings           | 1     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
| 3 | Permission           | permissions               | 1     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 0        |
| 4 | Role                 | roles                     | 1     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 0        |
| 5 | RolePermission       | role_permissions          | 1     | TimestampMixin, Base                             | 2        |
| 6 | User                 | users                     | 1     | BusinessBase, Base                               | 0        |
| 7 | UserRole             | user_roles                | 1     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 4        |
| 8 | UserTenant           | user_tenants              | 1     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 2        |
| 9 | RefreshToken         | refresh_tokens            | 1     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
|10 | CoachingModule       | coaching_modules          | 2     | BusinessBase, OptimisticLockMixin, Base          | 2        |
|11 | ModuleVersion        | module_versions           | 2     | UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base | 2 |
|12 | ModuleFrameworkStep  | module_framework_steps    | 2     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
|13 | ModulePromptTemplate | module_prompt_templates   | 2     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
|14 | ModulePersona        | module_personas           | 2     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
|15 | Rubric               | rubrics                   | 2     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
|16 | ModuleKnowledgeBase  | module_knowledge_bases    | 2     | (join table)                                     | 2        |
|17 | KnowledgeBase        | knowledge_bases           | 3     | BusinessBase, OptimisticLockMixin, Base          | 3        |
|18 | KnowledgeSource      | knowledge_sources         | 3     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 2        |
|19 | KnowledgeChunk       | knowledge_chunks          | 3     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 3        |
|20 | CoachingSession      | coaching_sessions         | 3     | BusinessBase, OptimisticLockMixin, Base          | 4        |
|21 | ConversationMessage  | conversation_messages     | 3     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
|22 | RoleplaySession      | roleplay_sessions         | 3     | BusinessBase, OptimisticLockMixin, Base          | 5        |
|23 | RoleplayMessage      | roleplay_messages         | 3     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
|24 | FeedbackReport       | feedback_reports          | 3     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 5        |
|25 | UserProgress         | user_progress             | 4     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 3        |
|26 | Achievement          | achievements              | 4     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 1        |
|27 | UserAchievement      | user_achievements         | 4     | UUIDPrimaryKeyMixin, Base                        | 3        |
|28 | Notification         | notifications             | 4     | UUIDPrimaryKeyMixin, TimestampMixin, Base        | 2        |
|29 | AnalyticsEvent       | analytics_events          | 4     | UUIDPrimaryKeyMixin, Base                        | 2        |
|30 | AuditLog             | audit_logs                | 4     | UUIDPrimaryKeyMixin, Base                        | 2        |
|31 | APIUsageLog          | api_usage_logs            | 4     | UUIDPrimaryKeyMixin, Base                        | 0 (loose)|
|32 | AIGeneration         | ai_generations            | 4     | UUIDPrimaryKeyMixin, Base                        | 2        |

**Total:** 32 tables, ~75 foreign keys, ~110 indexes (estimated)

---

## Appendix B: Action Items Before Phase 2B

The following must be resolved before migration files are generated:

1. **BLOCKING:** Delete or archive `app/models/feedback.py` — it defines
   `FeedbackReport` and `Rubric` in conflict with `app/models/session.py`.

2. **RECOMMENDED:** Add a `platform_config` table (simple key/value store
   for seed configuration values referenced in Section 6.2).

3. **RECOMMENDED:** Decide whether `ModuleKnowledgeBase` join table should
   have a `TimestampMixin` for audit trail (currently has no timestamps —
   a `weight` column for KB ranking but no `created_at`).

4. **REQUIRED:** Verify `pgvector >= 0.5.0` is installed in the target
   PostgreSQL environment before writing the vector migration.

5. **REQUIRED:** Confirm analytics_events + api_usage_logs + audit_logs
   will use table partitioning (range by date). If yes, the Alembic migration
   for those tables must use `PARTITION BY RANGE (created_at)` — cannot be
   added to a non-partitioned table without a full table rebuild later.

---

*End of Migration Architecture Review*
*Awaiting approval to proceed to Phase 2B: Migration File Generation*
