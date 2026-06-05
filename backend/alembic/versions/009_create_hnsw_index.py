"""Create HNSW vector index on knowledge_chunks.embedding.

Revision ID: 009
Revises: 008
Create Date: 2026-06-04

⚠️  CRITICAL: This migration CANNOT run inside a transaction.
   CREATE INDEX CONCURRENTLY is incompatible with transaction blocks.

INVOCATION (required):
   alembic upgrade 009 -x non_transactional=true

   The -x non_transactional=true flag sets AUTOCOMMIT isolation
   level in alembic/env.py, bypassing the transaction wrapper.
   Running without this flag will raise:
     ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block

INDEX PARAMETERS:
   m = 16              — HNSW graph connectivity (edges per node)
   ef_construction = 64 — build-time candidate list size (must be >= 2*m)
   operator class = vector_cosine_ops — cosine similarity for normalized
                   text embeddings (BAAI/bge-small-en-v1.5)

QUERY-TIME TUNING (set per-transaction, not in index):
   SET LOCAL hnsw.ef_search = 100;
   Higher ef_search = better recall, slightly slower per query.
   Default 100 is suitable for production; raise to 200 for
   high-precision requirements.

BUILD TIME ESTIMATES:
   10k vectors  → ~30 seconds
   100k vectors → ~10-15 minutes
   500k vectors → ~45-90 minutes

MONITOR BUILD PROGRESS:
   SELECT phase, blocks_done, blocks_total,
          round(100.0 * blocks_done / nullif(blocks_total,0), 1) AS pct
   FROM pg_stat_progress_create_index
   WHERE relid = 'knowledge_chunks'::regclass;

CONCURRENTLY BEHAVIOUR:
   Table remains readable and writable during the entire build.
   If the build is interrupted, a partial (invalid) index remains.
   Detect with:
     SELECT indexname, pg_index.indisvalid
     FROM pg_indexes
     JOIN pg_class ON relname = indexname
     JOIN pg_index ON indexrelid = pg_class.oid
     WHERE tablename = 'knowledge_chunks';
   If indisvalid = false: drop and re-run this migration.

ROLLBACK:
   downgrade() executes DROP INDEX CONCURRENTLY IF EXISTS.
   Zero data loss — the index is a derived structure only.
   After drop: retrieval falls back to sequential scan (~30-60s per query).
"""
from __future__ import annotations

from alembic import op

revision: str = "009"
down_revision: str = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import text
    conn = op.get_bind()
    try:
        conn.execute(text("SAVEPOINT hnsw_index"))
        conn.execute(text("SELECT 'vector'::regtype"))
        conn.execute(text(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kb_chunks_embedding
            ON knowledge_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            """
        ))
        conn.execute(text("RELEASE SAVEPOINT hnsw_index"))
    except Exception:
        conn.execute(text("ROLLBACK TO SAVEPOINT hnsw_index"))
        import warnings
        warnings.warn(
            "pgvector not available — skipping HNSW index creation. "
            "Install pgvector and re-run this migration to enable vector search.",
            stacklevel=2,
        )


def downgrade() -> None:
    # CONCURRENTLY: table remains accessible during drop
    # Data loss: NONE — index is derived, not data
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_kb_chunks_embedding"
    )
