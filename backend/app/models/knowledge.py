"""
Knowledge Base domain models — RAG knowledge infrastructure.

Architecture decisions (per PRD Addendum Part B):
──────────────────────────────────────────────────

  KnowledgeBase
    Named collection of knowledge, scoped to a tenant (scope='tenant')
    or to a specific module (scope='module').
    The RAG retrieval service resolves:
        tenant_base ∪ module_base(s)
    at query time via ModuleKnowledgeBase join rows.
    Inherits OptimisticLockMixin — concurrent admin edits (e.g. two
    admins renaming the KB simultaneously) are detected safely.

  KnowledgeSource
    One ingestion source within a KB.
    Supported types: paste | upload | url
    Status lifecycle: pending → processing → completed | failed
    URL sources carry crawl_frequency for scheduled re-ingestion.
    file_path is server-side only — never returned raw to clients.

  KnowledgeChunk
    Atomic unit of retrieval. Each chunk carries:
      - content:   the raw text slice
      - embedding: 384-dim pgvector column (BAAI/bge-small-en-v1.5)
      - metadata_: JSONB bag (title, page_number, source_url, section)
      - tenant_id: DENORMALIZED — intentional design choice

    tenant_id denormalization rationale:
      The HNSW similarity query runs:
        WHERE tenant_id = :tid AND kb_id = ANY(:kb_ids)
        ORDER BY embedding <=> :query_vector
        LIMIT :k
      Filtering on the scalar tenant_id column BEFORE the vector scan
      keeps the HNSW search set small without a JOIN to knowledge_bases.
      This is the key performance optimisation for multi-tenant RAG.

pgvector design:
────────────────
  embedding: vector(384)
    Dimension 384 matches BAAI/bge-small-en-v1.5 output.
    Nullable — chunk row is inserted first (during chunking),
    embedding is populated by the async ingestion worker.
    NULL embedding = not-yet-embedded; excluded from retrieval queries.

  HNSW index (defined in Alembic migration, NOT here):
    CREATE INDEX idx_kb_chunks_embedding
    ON knowledge_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

    Tuning notes (FIX PERF-01 from validation report):
      m=16              — graph connectivity; increase to 32 for
                          better recall at cost of ~2x index memory
      ef_construction=64 — must be >= 2*m; higher = better recall
                          at build time but slower initial index
      ef_search         — set at query time via:
                          SET hnsw.ef_search = 100;
                          Higher = better recall, slightly slower
      Similarity threshold: 0.65 cosine similarity minimum
      (low-scoring chunks are DROPPED per PRD B.5 quality controls)

  Cosine similarity query pattern:
    SELECT id, content, metadata,
           1 - (embedding <=> :query_vec::vector) AS similarity
    FROM knowledge_chunks
    WHERE tenant_id = :tenant_id
      AND kb_id = ANY(:kb_ids)
      AND embedding IS NOT NULL
      AND 1 - (embedding <=> :query_vec::vector) > 0.65
    ORDER BY similarity DESC
    LIMIT 10;

Circular import strategy:
──────────────────────────
  ModuleKnowledgeBase (Batch 2) references KnowledgeBase.
  To resolve this without circular imports:
    - module.py uses TYPE_CHECKING import of KnowledgeBase
    - knowledge.py uses TYPE_CHECKING import of ModuleKnowledgeBase
  Both relationship back_populates resolve by string name at
  SQLAlchemy mapper-configuration time, never at module import time.

Fixes applied (from validation report):
  SA-01  — No lazy="dynamic"; large collections use lazy="write_only"
  DB-07  — Cascade note: tenant cascade on chunks is intentional but
            should be handled by a background job at scale, not relied
            on for bulk tenant offboarding (documented here)
  PERF-01 — HNSW ef_search tuning documented above
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# pgvector SQLAlchemy integration
# pgvector==0.3.6 declared in requirements.txt
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]

from app.models.base import (
    Base,
    BusinessBase,
    OptimisticLockMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.module import CoachingModule, ModuleKnowledgeBase


# ─────────────────────────────────────────────────────────────────────────────
# KnowledgeBase
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeBase(BusinessBase, OptimisticLockMixin, Base):
    """
    Named collection of documents/knowledge for RAG retrieval.

    scope:
        'tenant' — available to all modules within this tenant.
                   module_id must be NULL.
        'module' — attached to a single module for domain-specific
                   knowledge. module_id must be set.
        The XOR constraint between scope and module_id is enforced
        by a DB CHECK (see __table_args__).

    chunk_count:
        Denormalized counter updated by the ingestion service after
        each source is processed. Avoids COUNT(*) on knowledge_chunks
        for the knowledge base dashboard.

    Soft-delete cascade:
        When a KB is soft-deleted, its sources and chunks remain in the
        DB but are excluded from retrieval (the retrieval service always
        joins through KnowledgeBase WHERE deleted_at IS NULL).
        Hard delete cascades to sources → chunks → vectors.
        WARNING: hard-deleting a large KB at the DB level is a slow
        operation (CASCADE through potentially millions of vector rows).
        Use a background job for tenant offboarding. (Note: DB-07)
    """

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        Index(
            "idx_kb_tenant_scope",
            "tenant_id",
            "scope",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_kb_module",
            "module_id",
            postgresql_where=text("deleted_at IS NULL AND module_id IS NOT NULL"),
        ),
        CheckConstraint(
            "scope IN ('tenant', 'module')",
            name="ck_kb_scope",
        ),
        CheckConstraint(
            "(scope = 'tenant' AND module_id IS NULL) OR "
            "(scope = 'module' AND module_id IS NOT NULL)",
            name="ck_kb_scope_module_consistency",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="tenant | module",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    module_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_modules.id", ondelete="CASCADE"),
        nullable=True,
        comment="Set only when scope='module'",
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Denormalized counter updated by ingestion service",
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )
    module: Mapped[Optional[CoachingModule]] = relationship(
        "CoachingModule",
        foreign_keys=[module_id],
        lazy="select",
    )
    creator: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[created_by],
        lazy="select",
    )
    sources: Mapped[list[KnowledgeSource]] = relationship(
        "KnowledgeSource",
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        lazy="write_only",      # potentially many sources; load explicitly
    )
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        "KnowledgeChunk",
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        lazy="write_only",      # potentially millions of chunks; NEVER eager-load
    )
    module_links: Mapped[list[ModuleKnowledgeBase]] = relationship(
        "ModuleKnowledgeBase",
        back_populates="knowledge_base",
        lazy="write_only",
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeBase name={self.name!r} "
            f"scope={self.scope!r} tenant={self.tenant_id}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# KnowledgeSource
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    One ingestion source within a KnowledgeBase.

    type values (enforced by CHECK):
        paste   — raw text pasted directly by admin
        upload  — file uploaded (PDF, DOCX, PPTX, TXT, MD)
        url     — web page fetched + main-content extracted

    status lifecycle (enforced by CHECK):
        pending    → source row created, not yet queued
        processing → async ingestion worker has picked it up
        completed  → all chunks embedded and stored
        failed     → ingestion failed; see error_message

    file_path:
        Server-side absolute path to the stored upload file.
        NEVER returned to clients in API responses — use presigned
        URLs or a download endpoint instead.
        SEC-03 mitigation: the ingestion service validates the path
        before storage (no path traversal).

    crawl_frequency:
        Only meaningful when type='url'. Set to NULL for non-URL
        sources. Values: daily | weekly | monthly
        Drives the async re-crawl scheduler (v1.1 scope).

    mime_type:
        Detected during ingestion, not trusted from client upload.
        Examples: application/pdf, application/vnd.openxmlformats-
                  officedocument.wordprocessingml.document, text/plain

    chunk_count:
        Denormalized count updated after ingestion completes.
        Rolled up into KnowledgeBase.chunk_count by the ingestion
        service.
    """

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        Index(
            "idx_kb_sources_kb_status",
            "kb_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("idx_kb_sources_kb", "kb_id"),
        CheckConstraint(
            "type IN ('paste', 'upload', 'url')",
            name="ck_kb_source_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_kb_source_status",
        ),
        CheckConstraint(
            "crawl_frequency IN ('daily', 'weekly', 'monthly') "
            "OR crawl_frequency IS NULL",
            name="ck_kb_source_crawl_frequency",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    kb_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="paste | upload | url",
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Human-readable title shown in KB management UI",
    )
    url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Source URL; populated for type='url'",
    )
    file_path: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Server-side storage path — never expose to clients",
    )
    file_size_bytes: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )
    mime_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Detected during ingestion, e.g. application/pdf",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        comment="pending | processing | completed | failed",
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Populated on status='failed'; shown to admin",
    )
    last_crawled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),       # FIX CRITICAL-07: explicit type
        nullable=True,
        comment="Timestamp of most recent successful URL crawl",
    )
    crawl_frequency: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="daily | weekly | monthly; null for non-URL sources",
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Soft-delete; cascades to chunk deletion via background job",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    knowledge_base: Mapped[KnowledgeBase] = relationship(
        "KnowledgeBase",
        back_populates="sources",
    )
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        "KnowledgeChunk",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="write_only",      # never load all chunks of a source into memory
    )
    uploader: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[created_by],
        lazy="select",
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return (
            f"<KnowledgeSource type={self.type!r} "
            f"title={self.title!r} status={self.status!r}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# KnowledgeChunk
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Atomic text chunk — the unit of RAG retrieval.

    Lifecycle:
        1. Ingestion worker splits source text into overlapping chunks
           (chunk_index 0, 1, 2, ...) and inserts rows with
           embedding=NULL.
        2. Embedding worker picks up NULL-embedding chunks, runs
           BAAI/bge-small-en-v1.5, stores the 384-dim vector.
        3. Retrieval service queries using cosine similarity,
           filtered by tenant_id + kb_id.

    embedding column:
        Type: vector(384) from pgvector
        Dimension 384 = BAAI/bge-small-en-v1.5 output size
        NULL when chunk is awaiting embedding (step 1 above)
        Retrieval queries filter WHERE embedding IS NOT NULL

    tenant_id (DENORMALIZED):
        Copied from KnowledgeBase.tenant_id at insert time.
        Purpose: allows the HNSW pre-filter to run as:
            WHERE tenant_id = :tid  ← scalar index scan (B-tree)
            ORDER BY embedding <=> :vec  ← HNSW scan on filtered set
        Without this, the retrieval query would JOIN through
        knowledge_bases on every vector scan — defeating the purpose
        of the HNSW index.
        This column MUST be kept in sync with the parent KB's
        tenant_id (which never changes after creation).

    metadata_ JSONB documented keys:
        title        str  — source document title
        source_url   str  — original URL (type='url' sources)
        page_number  int  — for PDF/PPTX sources
        section      str  — heading context, e.g. "§3 Leadership"
        char_start   int  — character offset in original document
        char_end     int  — character offset in original document
        updated_at   str  — ISO-8601 timestamp of source last-updated

    HNSW index (migration):
        CREATE INDEX idx_kb_chunks_embedding
        ON knowledge_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);

        At query time, set:
            SET LOCAL hnsw.ef_search = 100;
        Higher ef_search = better recall, ~10-20% slower per query.
        100 is a good default for production; tune up to 200 for
        high-precision requirements.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "chunk_index",
            name="uq_chunk_source_index",
        ),
        # B-tree indexes for pre-filtering before HNSW scan
        Index("idx_kb_chunks_tenant", "tenant_id"),
        Index("idx_kb_chunks_kb", "kb_id"),
        Index("idx_kb_chunks_source", "source_id"),
        # Composite index for the most common retrieval filter:
        #   WHERE tenant_id = :tid AND kb_id = ANY(:kb_ids)
        Index("idx_kb_chunks_tenant_kb", "tenant_id", "kb_id"),
        # Partial index: only chunks that have been embedded are retrievable
        Index(
            "idx_kb_chunks_embedded",
            "kb_id",
            "tenant_id",
            postgresql_where=text("embedding IS NOT NULL"),
        ),
        # NOTE: HNSW vector index defined in Alembic migration, NOT here.
        # SQLAlchemy's Index() does not support custom access methods
        # like hnsw. Migration SQL:
        #   CREATE INDEX idx_kb_chunks_embedding
        #   ON knowledge_chunks
        #   USING hnsw (embedding vector_cosine_ops)
        #   WITH (m = 16, ef_construction = 64);
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    kb_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment=(
            "DENORMALIZED copy of KnowledgeBase.tenant_id. "
            "Required for efficient HNSW pre-filtering. "
            "Must be kept in sync at insert time."
        ),
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="0-based position within the source document",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Raw text of this chunk",
    )
    embedding: Mapped[Optional[list]] = mapped_column(
        Vector(384),        # pgvector type: 384-dim float32 vector
        nullable=True,
        comment=(
            "384-dim embedding from BAAI/bge-small-en-v1.5. "
            "NULL until async embedding worker processes this chunk."
        ),
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="title, source_url, page_number, section, char_start, char_end",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    knowledge_base: Mapped[KnowledgeBase] = relationship(
        "KnowledgeBase",
        back_populates="chunks",
    )
    source: Mapped[KnowledgeSource] = relationship(
        "KnowledgeSource",
        back_populates="chunks",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def is_embedded(self) -> bool:
        """True if this chunk has been embedded and is retrievable."""
        return self.embedding is not None

    @property
    def source_title(self) -> str:
        """Convenience accessor for the source title from metadata."""
        return self.metadata_.get("title", "Unknown source")

    def __repr__(self) -> str:
        return (
            f"<KnowledgeChunk source={self.source_id} "
            f"idx={self.chunk_index} embedded={self.is_embedded}>"
        )
