"""Create PostgreSQL extensions required by the platform.

Revision ID: 001
Revises: (none — first migration)
Create Date: 2026-06-04

Extensions:
  pgcrypto  — gen_random_uuid() for UUID server_default on every PK
  uuid-ossp — uuid_generate_v4() fallback for external tooling
  vector    — pgvector 0.5+ for knowledge_chunks.embedding (HNSW)
  pg_trgm   — trigram indexes for future text search

NOTE: This migration runs in standard transactional mode.
Extensions are idempotent (IF NOT EXISTS).
"""
from __future__ import annotations

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgcrypto: gen_random_uuid() — required for all UUID PKs server_default
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # uuid-ossp: uuid_generate_v4() — compatibility fallback
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # vector: pgvector — required for knowledge_chunks.embedding column + HNSW
    # Skip gracefully if pgvector is not installed on this server
    from sqlalchemy import text
    conn = op.get_bind()
    try:
        conn.execute(text('SAVEPOINT vector_install'))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
        conn.execute(text('RELEASE SAVEPOINT vector_install'))
    except Exception:
        conn.execute(text('ROLLBACK TO SAVEPOINT vector_install'))
        import warnings
        warnings.warn(
            "pgvector extension not available — skipping. "
            "RAG/embedding features will be disabled until pgvector is installed.",
            stacklevel=2,
        )

    # pg_trgm: trigram similarity — for future full-text search on module names,
    # KB content, and user search. Low cost, high future value.
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')


def downgrade() -> None:
    # NOTE: Dropping extensions may fail if other objects depend on them.
    # Only drop in a full schema teardown (dev/test), never in production.
    op.execute('DROP EXTENSION IF EXISTS "pg_trgm"')
    op.execute('DROP EXTENSION IF EXISTS "vector"')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
    op.execute('DROP EXTENSION IF EXISTS "pgcrypto"')
