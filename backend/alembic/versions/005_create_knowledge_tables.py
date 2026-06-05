"""Create knowledge base domain tables.

Revision ID: 005
Revises: 004
Create Date: 2026-06-04

Tables created (Tier 3 — FK deps: tenants, users, coaching_modules):
  knowledge_bases
  module_knowledge_bases
  knowledge_sources
  knowledge_chunks

NOTE: HNSW index on knowledge_chunks.embedding is created separately
in migration 010_create_hnsw_index.py (non-transactional, CONCURRENTLY).
The vector(384) column is created here but is NULL until the embedding
worker populates it.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "005"
down_revision: str = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # knowledge_bases
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "knowledge_bases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("scope", sa.String(20), nullable=False,
                  comment="tenant | module"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("module_id", UUID(as_uuid=True), nullable=True,
                  comment="Set only when scope='module'"),
        sa.Column("chunk_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0"),
                  comment="Denormalized counter updated by ingestion service"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("scope IN ('tenant', 'module')",
                           name="ck_kb_scope"),
        sa.CheckConstraint(
            "(scope = 'tenant' AND module_id IS NULL) OR "
            "(scope = 'module' AND module_id IS NOT NULL)",
            name="ck_kb_scope_module_consistency",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_knowledge_bases_tenant"),
        sa.ForeignKeyConstraint(["module_id"], ["coaching_modules.id"],
                                ondelete="CASCADE",
                                name="fk_knowledge_bases_module"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"],
                                ondelete="SET NULL",
                                name="fk_knowledge_bases_created_by"),
    )
    op.create_index(
        "idx_kb_tenant_scope", "knowledge_bases", ["tenant_id", "scope"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_kb_module", "knowledge_bases", ["module_id"],
        postgresql_where=sa.text("deleted_at IS NULL AND module_id IS NOT NULL"),
    )

    # ──────────────────────────────────────────────────────────────────────────
    # module_knowledge_bases  (M:M join: coaching_modules ↔ knowledge_bases)
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "module_knowledge_bases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("module_id", UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", UUID(as_uuid=True), nullable=False),
        sa.Column("weight", sa.Numeric(4, 2), nullable=False,
                  server_default=sa.text("1.0"),
                  comment="Retrieval score multiplier"),
        sa.Column("is_primary", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("module_id", "knowledge_base_id",
                            name="uq_module_knowledge_base"),
        sa.ForeignKeyConstraint(["module_id"], ["coaching_modules.id"],
                                ondelete="CASCADE",
                                name="fk_mkb_module"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"],
                                ondelete="CASCADE",
                                name="fk_mkb_knowledge_base"),
    )
    op.create_index("idx_mkb_module", "module_knowledge_bases", ["module_id"])
    op.create_index("idx_mkb_kb", "module_knowledge_bases", ["knowledge_base_id"])

    # ──────────────────────────────────────────────────────────────────────────
    # knowledge_sources
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "knowledge_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kb_id", UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(20), nullable=False,
                  comment="paste | upload | url"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True,
                  comment="Server-side path — never expose to clients"),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'pending'"),
                  comment="pending | processing | completed | failed"),
        sa.Column("chunk_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crawl_frequency", sa.String(20), nullable=True,
                  comment="daily | weekly | monthly; null for non-URL sources"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("type IN ('paste', 'upload', 'url')",
                           name="ck_kb_source_type"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_kb_source_status",
        ),
        sa.CheckConstraint(
            "crawl_frequency IN ('daily', 'weekly', 'monthly') "
            "OR crawl_frequency IS NULL",
            name="ck_kb_source_crawl_frequency",
        ),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"],
                                ondelete="CASCADE",
                                name="fk_knowledge_sources_kb"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"],
                                ondelete="SET NULL",
                                name="fk_knowledge_sources_created_by"),
    )
    op.create_index("idx_kb_sources_kb", "knowledge_sources", ["kb_id"])
    op.create_index(
        "idx_kb_sources_kb_status", "knowledge_sources", ["kb_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ──────────────────────────────────────────────────────────────────────────
    # knowledge_chunks
    # Includes vector(384) column for pgvector embeddings.
    # HNSW index created separately in migration 010 (non-transactional).
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kb_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False,
                  comment="DENORMALIZED — required for HNSW pre-filtering"),
        sa.Column("chunk_index", sa.Integer(), nullable=False,
                  comment="0-based position within the source document"),
        sa.Column("content", sa.Text(), nullable=False),
        # vector(384) — pgvector type; NULL until embedding worker runs
        sa.Column("embedding", sa.Text(), nullable=True,
                  comment="384-dim vector(384); populated by embedding worker"),
        sa.Column("metadata", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb"),
                  comment="title, source_url, page_number, section, char_start, char_end"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("source_id", "chunk_index",
                            name="uq_chunk_source_index"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"],
                                ondelete="CASCADE",
                                name="fk_chunks_kb"),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"],
                                ondelete="CASCADE",
                                name="fk_chunks_source"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_chunks_tenant"),
    )
    # Drop the Text placeholder and re-add as proper vector(384) type
    # Gracefully skip if pgvector extension is not installed
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN embedding")
    from sqlalchemy import text
    conn = op.get_bind()
    try:
        conn.execute(text("SAVEPOINT vector_col"))
        conn.execute(text("SELECT 'vector'::regtype"))
        conn.execute(text(
            "ALTER TABLE knowledge_chunks "
            "ADD COLUMN embedding vector(384) NULL"
        ))
        conn.execute(text("RELEASE SAVEPOINT vector_col"))
    except Exception:
        conn.execute(text("ROLLBACK TO SAVEPOINT vector_col"))
        # pgvector not available — use FLOAT[] as fallback
        conn.execute(text(
            "ALTER TABLE knowledge_chunks "
            "ADD COLUMN embedding FLOAT[] NULL"
        ))

    # B-tree indexes for pre-filtering before HNSW scan
    op.create_index("idx_kb_chunks_tenant", "knowledge_chunks", ["tenant_id"])
    op.create_index("idx_kb_chunks_kb", "knowledge_chunks", ["kb_id"])
    op.create_index("idx_kb_chunks_source", "knowledge_chunks", ["source_id"])
    op.create_index("idx_kb_chunks_tenant_kb", "knowledge_chunks",
                    ["tenant_id", "kb_id"])
    op.create_index(
        "idx_kb_chunks_embedded", "knowledge_chunks", ["kb_id", "tenant_id"],
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )
    # NOTE: HNSW vector index created in migration 010_create_hnsw_index.py


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_sources")
    op.drop_table("module_knowledge_bases")
    op.drop_table("knowledge_bases")
