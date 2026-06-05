# Migration Blocking Issues Analysis
## Priority 1-3 Investigation Before Migration Generation

**Date:** 2026-06-04  
**Status:** Pre-Migration Analysis Complete — Awaiting Resolution Approval  
**Scope:** Resolve all identified blocking issues before Phase 2B migration generation

---

## Section 1: Feedback/Rubric Conflict Analysis

### 1.1 Duplicate Model Definitions — CONFIRMED

**Finding:** Two separate files define `FeedbackReport` and `Rubric` classes with the **same table names** but **different schemas**.

| Model          | File                           | Table Name         | Status      |
|----------------|--------------------------------|--------------------|-------------|
| FeedbackReport | `app/models/session.py`        | `feedback_reports` | **CANONICAL** |
| FeedbackReport | `app/models/feedback.py`       | `feedback_reports` | **DUPLICATE** |
| Rubric         | `app/models/module.py`         | `rubrics`          | **CANONICAL** |
| Rubric         | `app/models/feedback.py`       | `rubrics`          | **DUPLICATE** |

### 1.2 Schema Differences Analysis

#### FeedbackReport Schema Comparison

**session.py (Canonical):**
```python
class FeedbackReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "feedback_reports"
    
    # XOR constraint: exactly one of session_id OR roleplay_id
    session_id: Mapped[Optional[uuid.UUID]]      # FK to coaching_sessions
    roleplay_id: Mapped[Optional[uuid.UUID]]     # FK to roleplay_sessions
    user_id: Mapped[uuid.UUID]                   # NOT NULL
    tenant_id: Mapped[Optional[uuid.UUID]]
    rubric_id: Mapped[Optional[uuid.UUID]]       # FK to rubrics table
    
    # UUID columns use as_uuid=True (Python uuid.UUID objects)
    # Proper CHECK constraint for XOR enforcement
    # Proper relationship back_populates to CoachingSession/RoleplaySession
```

**feedback.py (Duplicate):**
```python
class FeedbackReport(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "feedback_reports"
    
    coaching_session_id: Mapped[str | None]     # Different column name!
    user_id: Mapped[str]                        # NOT NULL
    module_id: Mapped[str]                      # NOT NULL (denormalized)
    module_version_id: Mapped[str]              # NOT NULL (denormalized)
    tenant_id: Mapped[str | None]
    
    # UUID columns use as_uuid=False (string UUIDs)
    # Missing XOR constraint (only nullable coaching_session_id)
    # No roleplay_id column at all
    # use_alter=True on FK (deferred constraint creation)
    # Different schema structure entirely
```

**Critical Differences:**
1. **Column names differ:** `session_id` vs `coaching_session_id`
2. **UUID representation:** `as_uuid=True` vs `as_uuid=False` (Python uuid vs string)
3. **Roleplay support:** session.py has `roleplay_id`, feedback.py does not
4. **Denormalization:** feedback.py has `module_id` + `module_version_id`, session.py does not
5. **Constraint enforcement:** session.py has XOR CHECK, feedback.py does not
6. **Additional fields:** feedback.py has fields like `raw_ai_response`, `user_rating`, `user_notes` not in session.py

#### Rubric Schema Comparison

**module.py (Canonical):**
```python
class Rubric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rubrics"
    
    module_version_id: Mapped[uuid.UUID]  # FK to module_versions (NOT NULL)
    dimensions: Mapped[list]              # JSONB, NOT NULL
    content_version: Mapped[int]          # version tracking
    
    # 1:1 relationship with ModuleVersion
    # Unique constraint on module_version_id
```

**feedback.py (Duplicate):**
```python
class Rubric(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rubrics"
    
    name: Mapped[str]                     # String name (NOT NULL)
    description: Mapped[str | None]
    version: Mapped[int]                  # version counter
    is_active: Mapped[bool]
    tenant_id: Mapped[str | None]         # FK to tenants
    created_by: Mapped[str | None]        # FK to users
    dimensions: Mapped[list]              # JSONB, NOT NULL
    change_notes: Mapped[str | None]
    
    # Reusable standalone rubric design
    # NOT tied to a specific module_version
```

**Critical Differences:**
1. **Design philosophy:** module.py = 1:1 with ModuleVersion (immutable snapshot), feedback.py = reusable standalone rubric
2. **Required fields:** module.py has `module_version_id` (NOT NULL), feedback.py has `name` + tenant scoping
3. **Ownership model:** feedback.py has `tenant_id` + `created_by`, module.py does not
4. **Lifecycle:** module.py rubric is version-pinned, feedback.py rubric is shared/editable

### 1.3 Import Chain Analysis

#### Is feedback.py currently imported anywhere?

**Direct imports from `app.models.feedback`:** NONE FOUND  
`grep "from app.models.feedback import"` → 0 matches

**Is feedback.py in `__init__.py`?** NO  
The `app/models/__init__.py` imports `FeedbackReport` from `app.models.session` and `Rubric` from `app.models.module`. `feedback.py` is completely absent from the registry.

**Does Alembic see it?** CONDITIONALLY YES — **This is the risk.**  
`alembic/env.py` does:
```python
from app.database.base import Base
import app.models  # noqa: F401
```
The `import app.models` runs `__init__.py` which does NOT import `feedback.py`.  
**Therefore, as long as `feedback.py` is never imported, it is invisible to Alembic.**

However: if any test, script, or future developer does `import app.models.feedback`,  
SQLAlchemy will attempt to register the duplicate table names against `Base.metadata`  
and raise a `SAWarning` or `InvalidRequestError: Table 'feedback_reports' is already defined`.

**Current Blast Radius:** LOW — `feedback.py` is an orphaned file. It is imported by nothing.  
**Future Risk:** HIGH — it silently waits to cause a hard-to-diagnose mapper conflict.

### 1.4 Base Class Mismatch

`feedback.py` imports from `app.database.base`:
```python
from app.database.base import Base, TimestampMixin, UUIDMixin
```

`app/database/base.py` is a re-export shim where `UUIDMixin = UUIDPrimaryKeyMixin`.  
Both paths ultimately point to the same `Base(DeclarativeBase)`.  
**This means if `feedback.py` IS imported, the conflict is real and immediate** —  
both files register against the exact same `Base.metadata`, and the duplicate  
`__tablename__ = "feedback_reports"` and `__tablename__ = "rubrics"` will collide.

---

## Section 2: Recommended Resolution

### 2.1 Canonical Versions — Confirmed

**FeedbackReport: `app/models/session.py` is the canonical version.**  
Reasons:
- Already registered in `__init__.py`
- Has the XOR constraint enforcing data integrity
- Supports both CoachingSession AND RoleplaySession (matches PRD)
- Uses `as_uuid=True` (correct for modern SQLAlchemy 2.0)
- Has proper `back_populates` wiring to session models
- Validated in the prior validation report

**Rubric: `app/models/module.py` is the canonical version.**  
Reasons:
- Already registered in `__init__.py`
- Correct 1:1 design with ModuleVersion (immutable snapshot pattern)
- Properly tied to the version-pinned module system
- Has `OptimisticLockMixin`-aware parent (ModuleVersion)
- Validated in the prior validation report

### 2.2 fields in feedback.py NOT in session.py FeedbackReport

Some fields in the `feedback.py` `FeedbackReport` are genuinely useful and not
yet in `session.py`. Evaluate each before deleting:

| Column (feedback.py only) | Type    | Recommendation                                         |
|---------------------------|---------|--------------------------------------------------------|
| `raw_ai_response`         | Text    | **ADD to session.py FeedbackReport** — needed for AI debugging |
| `user_rating`             | Integer | **ADD to session.py FeedbackReport** — 1-5 star reaction |
| `user_notes`              | Text    | **ADD to session.py FeedbackReport** — learner annotation |
| `prompt_tokens`           | Integer | **SKIP** — already covered by `ai_generations` table  |
| `completion_tokens`       | Integer | **SKIP** — already covered by `ai_generations` table  |
| `summary`                 | Text    | **EVALUATE** — session.py has `feedback_text` (same purpose). SKIP if `feedback_text` is sufficient |
| `score_breakdown`         | JSONB   | **SKIP** — session.py has `scores` JSONB (same purpose, better schema) |
| `next_steps`              | Text    | **ADD to session.py FeedbackReport** — actionable next step |
| `module_id`               | UUID    | **SKIP** — derivable from the linked session; denormalization not needed |
| `module_version_id`       | UUID    | **SKIP** — derivable from the linked session |
| `coaching_session_id`     | UUID    | **SKIP** — this is `session_id` in canonical version (just renamed) |

**Columns to add to session.py `FeedbackReport` before deleting feedback.py:**
1. `raw_ai_response` — Text, nullable
2. `user_rating` — Integer, nullable, CHECK 1-5
3. `user_notes` — Text, nullable
4. `next_steps` — Text, nullable

### 2.3 Fields in feedback.py Rubric NOT in module.py Rubric

| Column (feedback.py Rubric) | Recommendation |
|------------------------------|----------------|
| `name`                       | **SKIP** — Rubric is identified by module_version, not standalone name |
| `description`                | **ADD** — useful for admin UI; add to module.py Rubric as nullable |
| `is_active`                  | **SKIP** — lifecycle managed via ModuleVersion.is_current |
| `tenant_id`                  | **SKIP** — tenant scoping inherited from parent ModuleVersion |
| `created_by`                 | **SKIP** — inherited from ModuleVersion.published_by |
| `change_notes`               | **ADD** — already exists as `content_version` with doc intent; add `change_notes` as Text nullable |

**Columns to add to module.py `Rubric` before deleting feedback.py:**
1. `description` — Text, nullable (admin UI display)
2. `change_notes` — Text, nullable (wording change rationale)

### 2.4 Exact Resolution Steps

**Step 1: Amend `app/models/session.py` FeedbackReport**
Add these 4 columns (before deleting feedback.py):
```python
raw_ai_response: Mapped[Optional[str]] = mapped_column(
    Text, nullable=True,
    comment="Raw LLM output for debugging and re-processing"
)
user_rating: Mapped[Optional[int]] = mapped_column(
    Integer, nullable=True,
    comment="1-5 star rating from learner"
)
user_notes: Mapped[Optional[str]] = mapped_column(
    Text, nullable=True,
    comment="Free-text learner annotation on the feedback"
)
next_steps: Mapped[Optional[str]] = mapped_column(
    Text, nullable=True,
    comment="Actionable next steps from the AI coach"
)
```
Also add CHECK constraint:
```python
CheckConstraint("user_rating BETWEEN 1 AND 5 OR user_rating IS NULL",
                name="ck_feedback_user_rating"),
```

**Step 2: Amend `app/models/module.py` Rubric**
Add these 2 columns:
```python
description: Mapped[Optional[str]] = mapped_column(
    Text, nullable=True,
    comment="Human-readable rubric description for admin UI"
)
change_notes: Mapped[Optional[str]] = mapped_column(
    Text, nullable=True,
    comment="Notes on what changed in this content_version"
)
```

**Step 3: Delete `app/models/feedback.py`**
The file has zero consumers. Deleting it is safe right now.
No `__init__.py` changes needed — `feedback.py` was never registered.

**Step 4: Verify the `module.py` TYPE_CHECKING import in `session.py`**

`session.py` currently has this in its `TYPE_CHECKING` block:
```python
from app.models.module import (
    CoachingModule,
    ModuleVersion,
    ModulePersona,
    Rubric,              # <-- This imports module.py's Rubric
)
```
This is correct. `Rubric` in `session.py` refers to `module.py`'s Rubric.  
No change needed.

**Step 5: Verify no import-path changes are required**
Since `feedback.py` is imported nowhere, no import paths need updating.
The sole risk was future accidental import — deleting the file eliminates it.

### 2.5 Import Path Summary

| Import Path                                | Currently Used? | After Deletion |
|--------------------------------------------|-----------------|----------------|
| `from app.models.feedback import FeedbackReport` | NOWHERE | File deleted — any future use becomes ImportError (desired) |
| `from app.models.feedback import Rubric`   | NOWHERE         | Same             |
| `from app.models.session import FeedbackReport` | `__init__.py` ✓ | No change        |
| `from app.models.module import Rubric`     | `__init__.py` ✓ | No change        |

---

## Section 3: Partitioning Recommendation

### 3.1 Tables Under Review

Two tables were flagged in the migration architecture review:
- `analytics_events`
- `api_usage_logs`
- (`audit_logs` was also flagged as high-volume — included here)

### 3.2 Volume Projections

| Table             | Write Rate              | Projected 12-Month Size | Partition Column  |
|-------------------|-------------------------|-------------------------|-------------------|
| `analytics_events`| ~5M rows/day (100k users)| ~1.8B rows             | `occurred_at`     |
| `api_usage_logs`  | ~2M rows/day (sampled)  | ~730M rows              | `created_at`      |
| `audit_logs`      | ~100k rows/day          | ~36M rows               | `created_at`      |

At these volumes, **without partitioning**:
- Indexes exceed available memory and degrade to page scans
- VACUUM takes hours and blocks writes
- `pg_stat_user_tables` shows massive bloat within 3-6 months
- `created_at`-range queries (monthly reports) scan the full table

### 3.3 Partitioning Strategy

**Recommended:** Monthly range partitioning on the timestamp column.  
**Reason:** All read queries on these tables are time-bounded (last N days, monthly reports). Older partitions can be **dropped** in O(1) time — no row-level DELETE needed for retention policies.

#### 3.3.1 PostgreSQL Range Partitioning — How it Works for ORM

SQLAlchemy ORM does **NOT** understand partitioned tables natively.  
`Base.metadata.create_all()` will create the parent table only.  
Child partitions must be created via raw DDL in the migration script.

**Key constraint:** Alembic autogenerate does NOT track child partition creation.  
Child partition DDL must be in **explicit** (non-autogenerated) migration files.

#### 3.3.2 analytics_events Partitioning Plan

```sql
-- Parent table (ORM generates this)
CREATE TABLE analytics_events (
  id uuid NOT NULL,
  user_id uuid,
  tenant_id uuid,
  event_type varchar(100) NOT NULL,
  ...
  occurred_at timestamptz NOT NULL
) PARTITION BY RANGE (occurred_at);

-- Initial child partitions (migration creates these)
CREATE TABLE analytics_events_2026_06
  PARTITION OF analytics_events
  FOR VALUES FROM ('2026-06-01 00:00:00+00') TO ('2026-07-01 00:00:00+00');

CREATE TABLE analytics_events_2026_07
  PARTITION OF analytics_events
  FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00');

-- ... repeat for 12 months forward

-- Default partition catches out-of-range inserts (prevents silent data loss)
CREATE TABLE analytics_events_default
  PARTITION OF analytics_events DEFAULT;
```

**Retention:** Drop partitions older than 13 months via scheduled job:
```sql
-- pg_cron job (runs 1st of each month)
DROP TABLE IF EXISTS analytics_events_2025_05;  -- 13 months ago
```

#### 3.3.3 api_usage_logs Partitioning Plan

Same monthly range pattern on `created_at`:
```sql
CREATE TABLE api_usage_logs (...) PARTITION BY RANGE (created_at);

CREATE TABLE api_usage_logs_2026_06
  PARTITION OF api_usage_logs
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
```

#### 3.3.4 audit_logs Partitioning Plan

`audit_logs` grows more slowly (~36M rows/year) and has **compliance retention requirements** (minimum 7 years in most jurisdictions). Partitioning still helps but **partitions must NOT be dropped**. They should be archived to cold storage instead.

```sql
CREATE TABLE audit_logs (...) PARTITION BY RANGE (created_at);

-- Monthly partitions, retained indefinitely
CREATE TABLE audit_logs_2026_06
  PARTITION OF audit_logs
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
```

### 3.4 Migration Impact of Adding Partitioning Now vs. Later

**THIS IS THE CRITICAL DECISION:**

| Scenario | If decided NOW (in first migration) | If decided LATER (post-launch) |
|----------|-------------------------------------|--------------------------------|
| Implementation effort | Medium — 1 explicit migration file | VERY HIGH — requires full table rebuild |
| Data migration | None (fresh tables) | Must dump → drop → recreate → reload |
| Downtime risk | None | 2-12 hour downtime per table |
| Index rebuild | One-time at setup | Full rebuild after migration |
| ORM compatibility | Requires understanding | Same |

**Converting a non-partitioned table to a partitioned one requires:**
```sql
-- This cannot be done in-place in PostgreSQL
ALTER TABLE analytics_events RENAME TO analytics_events_old;
CREATE TABLE analytics_events (...) PARTITION BY RANGE (occurred_at);
INSERT INTO analytics_events SELECT * FROM analytics_events_old;
DROP TABLE analytics_events_old;
VACUUM ANALYZE analytics_events;
```
For a table with 100M+ rows, this takes **hours** and requires **double the disk space** during the transition.

### 3.5 MVP Recommendation

**Immediate (in first migration):**

| Table             | Recommendation | Rationale                                              |
|-------------------|----------------|--------------------------------------------------------|
| `analytics_events`| **PARTITION NOW** | High-velocity event log. Will be unmanageable in 6 months without it. Cost of doing it now: low. Cost later: very high |
| `api_usage_logs`  | **PARTITION NOW** | Same rationale. Also, async writes already planned — adding partitions adds negligible complexity |
| `audit_logs`      | **PARTITION NOW** | Compliance requires long retention; partitions make archival trivial |

**ORM Impact:**
No ORM code changes needed. The `__tablename__` stays the same.
SQLAlchemy inserts go to the parent table; PostgreSQL routes them to the correct partition automatically.
Query patterns are unchanged. The optimizer uses partition pruning automatically when a WHERE clause includes the partition key.

**Alembic Migration Strategy:**
```
Create ONE explicit (non-autogenerated) migration file per partitioned table:
  - Creates parent table with PARTITION BY RANGE
  - Creates 13 forward-looking monthly child partitions
  - Creates a DEFAULT partition for safety
  - Documents the pg_cron partition creation job SQL as a comment
```

**Partition Automation (v1.0):**
A simple background job (or pg_cron) must create next-month's partition before the month rolls over. This is a 4-line SQL job and must be set up during deployment, not deferred.

```sql
-- pg_cron job to auto-create next month's partition
-- Run on the 25th of each month
SELECT cron.schedule(
  'create-analytics-partition',
  '0 0 25 * *',
  $$
  DO $$DECLARE
    next_month date := date_trunc('month', now()) + interval '1 month';
    next_next_month date := next_month + interval '1 month';
  BEGIN
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS analytics_events_%s '
      'PARTITION OF analytics_events '
      'FOR VALUES FROM (%L) TO (%L)',
      to_char(next_month, 'YYYY_MM'), next_month, next_next_month
    );
  END$$
  $$
);
```

---

## Section 4: HNSW Migration Plan

### 4.1 Why HNSW is a Special Case

Standard B-tree indexes in PostgreSQL can be created inside a transaction and
rolled back if the migration fails. **HNSW indexes cannot be created inside
a transaction** — pgvector HNSW index builds use special lock modes and do not
support transactional rollback.

SQLAlchemy / Alembic creates index DDL inside `BEGIN ... COMMIT` blocks by default.  
HNSW creation must be done with `op.execute()` outside of a transaction, in an
explicit migration context configured with `transaction_per_migration = false`.

### 4.2 Migration-Safe Approach

#### 4.2.1 Separate HNSW into its own migration file

The HNSW index must live in its own isolated migration file that:
1. Does NOT run in a transaction (`connection.execution_options(isolation_level="AUTOCOMMIT")`)
2. Uses `CREATE INDEX CONCURRENTLY` to avoid table-level write locks
3. Has an explicit `downgrade()` that simply drops the index

```python
# alembic/versions/010_create_hnsw_index.py

"""Create HNSW vector index on knowledge_chunks.embedding

Revision ID: 010
Down revision: 009_create_knowledge_tables
"""

from alembic import op


# CRITICAL: This migration CANNOT run inside a transaction.
# CONCURRENTLY is not supported within an explicit transaction block.
# Alembic must be configured to run this in autocommit mode.


def upgrade() -> None:
    # Execute in autocommit mode — no surrounding transaction
    # The connection.execution_options() call below achieves this
    # when using the non-transactional migration pattern.
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kb_chunks_embedding
        ON knowledge_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_kb_chunks_embedding"
    )
```

#### 4.2.2 Alembic env.py configuration for non-transactional migration

The migration script above will silently fail inside Alembic's default
transaction wrapper. The env.py must be updated to run this specific migration
outside a transaction:

```python
# In alembic/env.py, update run_migrations_online():

def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_sync_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Check if this specific migration needs non-transactional mode
        # (set via x flag: alembic upgrade head -x non_transactional=true)
        non_transactional = context.get_x_argument(
            as_dictionary=True
        ).get("non_transactional", "false") == "true"

        if non_transactional:
            connection = connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                transaction_per_migration=False,
            )
            with context.begin_transaction():
                context.run_migrations()
        else:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
```

**Invocation:**
```bash
# All normal migrations (transactional)
alembic upgrade head

# HNSW index only (non-transactional)
alembic upgrade 010_create_hnsw_index -x non_transactional=true
```

#### 4.2.3 CONCURRENTLY Strategy

`CREATE INDEX CONCURRENTLY` allows reads and writes to continue during the
entire index build. The trade-off:
- Build takes 1.5-2× longer than non-CONCURRENTLY
- Does NOT block INSERT/UPDATE/DELETE on `knowledge_chunks`
- **Cannot be run inside a transaction block** (hence the AUTOCOMMIT requirement)

**Build Progress Monitoring:**
```sql
-- Monitor HNSW index creation progress in real-time
SELECT phase, blocks_done, blocks_total,
       round(100.0 * blocks_done / nullif(blocks_total, 0), 1) AS pct_done
FROM pg_stat_progress_create_index
WHERE relid = 'knowledge_chunks'::regclass;
```

**Pre-build Validation:**
```sql
-- Count rows that WILL be indexed (have embeddings)
SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NOT NULL;

-- Verify vector dimension consistency
SELECT DISTINCT array_length(embedding::float[], 1) as dims
FROM knowledge_chunks
WHERE embedding IS NOT NULL;
-- Must return exactly: 384
-- If any other value appears, the embedding worker has a bug
```

#### 4.2.4 Index Parameters — Final Recommendation

```sql
CREATE INDEX CONCURRENTLY idx_kb_chunks_embedding
ON knowledge_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

| Parameter          | Value | Rationale                                                  |
|--------------------|-------|-------------------------------------------------------------|
| `m`                | 16    | 16 edges per node. Good recall/memory balance for MVP.     |
| `ef_construction`  | 64    | 4× m. Minimum rule is 2×m; 4× gives better recall at build. |
| `ef_search`        | 100   | Set at query time via `SET LOCAL hnsw.ef_search = 100`. Not an index parameter. |
| Operator class     | `vector_cosine_ops` | Cosine similarity. Correct for normalized text embeddings (BAAI/bge-small-en-v1.5) |

**When to increase `m` to 32:**  
Scale to 500k+ vectors and see recall < 0.85 in A/B tests.
Increasing `m` doubles index memory (from ~2GB to ~4GB at 100k vectors) and
requires rebuilding the index from scratch — plan for it in v1.1, not MVP.

### 4.3 Rollback Strategy

#### 4.3.1 Rollback During Build (interrupted mid-way)

`CONCURRENTLY` leaves a partial (invalid) index if interrupted:
```sql
-- Check for invalid indexes
SELECT indexname, indisvalid
FROM pg_indexes
JOIN pg_index ON indexrelid = (SELECT oid FROM pg_class WHERE relname = 'knowledge_chunks')
WHERE NOT indisvalid;
```

If `indisvalid = false`, the index exists but is broken. **Drop and restart:**
```sql
DROP INDEX CONCURRENTLY IF EXISTS idx_kb_chunks_embedding;
-- Then re-run the migration
```

#### 4.3.2 Rollback After Successful Build

```sql
DROP INDEX CONCURRENTLY IF EXISTS idx_kb_chunks_embedding;
```
**Impact:** Retrieval queries fall back to sequential scan. At 100k+ vectors,
response time degrades from ~20ms to 30-60 seconds per query.  
**Recovery time:** Rebuild requires 10-30 minutes.  
**Data loss:** Zero. The index is a derived structure, not data.

#### 4.3.3 Rollback Alembic Downgrade Command

```bash
alembic downgrade -1 -x non_transactional=true
```
This runs `downgrade()` which executes `DROP INDEX CONCURRENTLY IF EXISTS`.

### 4.4 Build Time Estimates

| Vector Count  | Build Time (m=16, ef=64) | Index Size (approx) |
|---------------|--------------------------|----------------------|
| 10k vectors   | ~30 seconds              | ~200MB               |
| 100k vectors  | ~8-15 minutes            | ~2GB                 |
| 500k vectors  | ~45-90 minutes           | ~10GB                |
| 1M vectors    | ~2-3 hours               | ~20GB                |

**Maintenance window recommendation:** Schedule the HNSW migration during a
period with no active KB ingestion. If the embedding worker is running and
adding new vectors while the index is being built, it extends build time and
may reduce final recall (newly added vectors after build start are missing from
the HNSW graph until the index is rebuilt or VACUUM is run with
`maintenance_work_mem` set high enough for an append operation).

### 4.5 Post-Build Verification

```sql
-- 1. Index exists and is valid
SELECT indexname, indisvalid, indexdef
FROM pg_indexes
WHERE tablename = 'knowledge_chunks'
  AND indexname = 'idx_kb_chunks_embedding';

-- 2. Test retrieval speed (should be < 50ms for k=10)
EXPLAIN (ANALYZE, BUFFERS) 
SELECT id, content, 1 - (embedding <=> '[...]'::vector) AS sim
FROM knowledge_chunks
WHERE tenant_id = '<some-tenant-uuid>'
  AND embedding IS NOT NULL
ORDER BY embedding <=> '[...]'::vector
LIMIT 10;

-- 3. Check index is being used (Seq Scan = HNSW not used, bad)
-- Look for "Index Scan using idx_kb_chunks_embedding"
```

---

## Section 5: Final Go/No-Go Decision

### 5.1 Issue Resolution Tracker

| Priority | Issue | Status | Action Required | Blocking? |
|----------|-------|--------|-----------------|-----------|
| P1 | `feedback.py` defines duplicate `FeedbackReport` (__tablename__ = "feedback_reports") | **IDENTIFIED** | Delete `feedback.py` | YES |
| P1 | `feedback.py` defines duplicate `Rubric` (__tablename__ = "rubrics") | **IDENTIFIED** | Delete `feedback.py` | YES |
| P1 | 4 columns from `feedback.py` FeedbackReport not in `session.py` | **IDENTIFIED** | Add to `session.py` first | YES |
| P1 | 2 columns from `feedback.py` Rubric not in `module.py` | **IDENTIFIED** | Add to `module.py` first | LOW |
| P2 | analytics_events needs partitioning | **ANALYSED** | Confirm and implement in first migration | YES |
| P2 | api_usage_logs needs partitioning | **ANALYSED** | Confirm and implement in first migration | YES |
| P2 | audit_logs needs partitioning | **ANALYSED** | Confirm and implement in first migration | YES |
| P3 | HNSW index build must be non-transactional | **ANALYSED** | Separate migration + AUTOCOMMIT mode | YES |
| P3 | HNSW `CONCURRENTLY` requires env.py update | **ANALYSED** | Update alembic/env.py before generation | YES |

### 5.2 Current Go/No-Go Status

**VERDICT: NO-GO for migration generation until the following are confirmed:**

---

### 5.3 Required Approvals

**Please confirm each of the following before proceeding to Phase 2B:**

#### Approval A — feedback.py Deletion
Confirm that `app/models/feedback.py` should be **deleted**.
The file is an orphaned duplicate, imported by nothing.
The 4 valuable fields (`raw_ai_response`, `user_rating`, `user_notes`, `next_steps`)
will be **merged into `session.py` FeedbackReport** before deletion.
The 2 Rubric fields (`description`, `change_notes`) will be **merged into `module.py` Rubric** before deletion.

> **Awaiting approval: ☐ YES, delete feedback.py after merging fields | ☐ NO, keep it (explain why)**

---

#### Approval B — Partitioning Decision
Confirm whether to implement table partitioning in the initial migration.

Option B1 (Recommended):
**Implement monthly range partitioning NOW** for `analytics_events`, `api_usage_logs`, and `audit_logs`.
No data loss risk (fresh tables). Prevents a costly retroactive migration later.

Option B2:
**Skip partitioning for MVP**. Accept the risk of a disruptive retroactive migration within 6-12 months.

> **Awaiting approval: ☐ B1 — Partition now | ☐ B2 — Skip for MVP**

---

#### Approval C — HNSW Index Migration Pattern
Confirm the HNSW migration isolation strategy.

Recommended approach:
- Isolate HNSW creation in its own migration file (`010_create_hnsw_index.py`)
- Update `alembic/env.py` to support `-x non_transactional=true` flag
- Build using `CREATE INDEX CONCURRENTLY`
- Run this migration step manually during a maintenance window, not as part of `alembic upgrade head`

> **Awaiting approval: ☐ YES, use isolation strategy above | ☐ Alternative: run HNSW inline with table creation (blocking, not recommended)**

---

#### Approval D — Extra Columns in session.py FeedbackReport
Confirm which fields to merge from `feedback.py` into canonical `FeedbackReport`:

| Field            | Include? |
|------------------|----------|
| `raw_ai_response`| ☐ YES / ☐ NO |
| `user_rating`    | ☐ YES / ☐ NO |
| `user_notes`     | ☐ YES / ☐ NO |
| `next_steps`     | ☐ YES / ☐ NO |

---

#### Approval E — Extra Columns in module.py Rubric
Confirm which fields to merge from `feedback.py` into canonical `Rubric`:

| Field          | Include? |
|----------------|----------|
| `description`  | ☐ YES / ☐ NO |
| `change_notes` | ☐ YES / ☐ NO |

---

### 5.4 Once All Approvals Received — Execution Plan

```
Step 1: Merge approved fields into session.py FeedbackReport
Step 2: Merge approved fields into module.py Rubric
Step 3: Delete app/models/feedback.py
Step 4: Update alembic/env.py with non_transactional flag support
Step 5: Confirm partitioning decision in analytics/audit ORM models
Step 6: → PROCEED TO PHASE 2B: MIGRATION FILE GENERATION
```

---

*End of Blocking Issues Analysis*  
*Awaiting approval on Sections A, B, C, D, E above*
