"""
KnowledgeChunkRepository — async SQLAlchemy 2.0 implementation.

Covers:
  Chunk CRUD (no soft-delete — chunks are physically deleted)
  Source-scoped and KB-scoped listing
  Physical deletion by source_id (ingestion cleanup)
  pgvector HNSW cosine similarity search (RAG core query)

pgvector design:
  embedding column: vector(384) — BAAI/bge-small-en-v1.5 output
  Similarity operator: <=> (cosine distance, lower = more similar)
  Similarity score: 1 - (embedding <=> :vec) ∈ [0.0, 1.0], higher = better
  HNSW index: idx_kb_chunks_embedding (created in migration 009)
  ef_search: set per-transaction via SET LOCAL hnsw.ef_search = :n

  Pre-filter pattern (critical for multi-tenant performance):
    WHERE tenant_id = :tid          ← B-tree scan (idx_kb_chunks_tenant)
    AND kb_id = ANY(:kb_ids)        ← B-tree scan (idx_kb_chunks_kb)
    AND embedding IS NOT NULL       ← partial index idx_kb_chunks_embedded
  Then ORDER BY embedding <=> :vec  ← HNSW scan on the filtered set

  Without the pre-filter, HNSW would scan all tenants' vectors.

ChunkSearchResult:
  A plain dataclass returned by all similarity_search variants.
  Contains the chunk and its float similarity score [0.0, 1.0].
  Never returns raw ORM rows with distance columns — callers always
  receive typed ChunkSearchResult objects.

Transaction contract:
  No commit() or rollback() calls here.
  The session owner (get_db) handles commit/rollback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import DuplicateError, NotFoundError


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ChunkSearchResult:
    """
    Typed result for a similarity search query.

    Attributes:
        chunk:      the KnowledgeChunk ORM object
        similarity: cosine similarity score ∈ [0.0, 1.0]
                    1.0 = identical vector, 0.0 = orthogonal
    """

    chunk: KnowledgeChunk
    similarity: float


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class KnowledgeChunkCreate:
    """Data required to create a new KnowledgeChunk row."""

    kb_id: UUID
    source_id: UUID
    tenant_id: UUID                  # DENORMALIZED — must match parent KB
    chunk_index: int
    content: str
    embedding: Optional[list] = None  # None until embedding worker runs
    metadata: Optional[dict] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "kb_id": self.kb_id,
            "source_id": self.source_id,
            "tenant_id": self.tenant_id,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "embedding": self.embedding,
            "metadata_": self.metadata or {},
        }


@dataclass
class KnowledgeChunkUpdate:
    """Update a chunk's embedding (only valid update path)."""

    embedding: Optional[list] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result: dict = {}
        if self.embedding is not None:
            result["embedding"] = self.embedding
        return result


# ── Repository ────────────────────────────────────────────────────────────────

class KnowledgeChunkRepository(
    BaseRepository[KnowledgeChunk, KnowledgeChunkCreate, KnowledgeChunkUpdate]
):
    """
    All database operations for KnowledgeChunk, including pgvector RAG search.

    Chunks have NO soft-delete — they are physically deleted when a source
    is removed. The repository exposes hard-delete paths only.

    The three similarity_search variants offer progressively broader scope:
      similarity_search()                — most specific (tenant + kb list)
      similarity_search_by_knowledge_base() — single KB
      similarity_search_by_tenant()     — all KBs in a tenant (broadest)

    All similarity searches:
      - Filter to embedded chunks only (embedding IS NOT NULL)
      - Apply a minimum score threshold (default 0.65, per PRD B.5)
      - Set SET LOCAL hnsw.ef_search for tunable recall
      - Return ChunkSearchResult list, ordered by similarity DESC
    """

    model = KnowledgeChunk

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── ID-based lookup ───────────────────────────────────────────────────────

    async def get_by_id(self, chunk_id: UUID) -> KnowledgeChunk | None:
        """Fetch a single KnowledgeChunk by primary key."""
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.id == chunk_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Source-scoped listing ─────────────────────────────────────────────────

    async def list_by_source(
        self,
        source_id: UUID,
        *,
        embedded_only: bool = False,
        page: int = 1,
        page_size: int = 100,
    ) -> Page[KnowledgeChunk]:
        """
        List chunks for a source, ordered by chunk_index ascending.

        When embedded_only=True, filters to chunks with embedding IS NOT NULL.
        Uses idx_kb_chunks_source index.
        """
        count_base = (
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.source_id == source_id)
        )
        data_base = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.source_id == source_id)
        )
        if embedded_only:
            count_base = count_base.where(
                KnowledgeChunk.embedding.is_not(None)
            )
            data_base = data_base.where(
                KnowledgeChunk.embedding.is_not(None)
            )

        total: int = (await self._session.execute(count_base)).scalar_one()
        data_stmt = (
            data_base
            .order_by(KnowledgeChunk.chunk_index.asc())
            .offset(self._offset(page, page_size))
            .limit(page_size)
        )
        result = await self._session.execute(data_stmt)
        return Page(
            items=list(result.scalars().all()),
            total=total,
            page=page,
            page_size=page_size,
        )

    # ── KB-scoped listing ─────────────────────────────────────────────────────

    async def list_by_knowledge_base(
        self,
        kb_id: UUID,
        *,
        embedded_only: bool = True,
        page: int = 1,
        page_size: int = 100,
    ) -> Page[KnowledgeChunk]:
        """
        List chunks for a knowledge base.

        Defaults to embedded_only=True since unembedded chunks are not
        useful for most callers. Use embedded_only=False to inspect
        ingestion pipeline state.

        Uses idx_kb_chunks_kb index.
        """
        count_base = (
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.kb_id == kb_id)
        )
        data_base = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.kb_id == kb_id)
        )
        if embedded_only:
            count_base = count_base.where(
                KnowledgeChunk.embedding.is_not(None)
            )
            data_base = data_base.where(
                KnowledgeChunk.embedding.is_not(None)
            )

        total: int = (await self._session.execute(count_base)).scalar_one()
        data_stmt = (
            data_base
            .order_by(KnowledgeChunk.chunk_index.asc())
            .offset(self._offset(page, page_size))
            .limit(page_size)
        )
        result = await self._session.execute(data_stmt)
        return Page(
            items=list(result.scalars().all()),
            total=total,
            page=page,
            page_size=page_size,
        )

    async def count_pending_embedding(self, kb_id: UUID) -> int:
        """
        Count chunks awaiting embedding (embedding IS NULL) in a KB.

        Used by the ingestion service to decide whether to trigger
        the embedding worker.
        """
        stmt = (
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.kb_id == kb_id)
            .where(KnowledgeChunk.embedding.is_(None))
        )
        return (await self._session.execute(stmt)).scalar_one()

    # ── Deletion ──────────────────────────────────────────────────────────────

    async def delete_by_source(self, source_id: UUID) -> int:
        """
        Hard-delete all KnowledgeChunks belonging to a source.

        Called when a KnowledgeSource is being fully removed (either
        via hard-delete or after the background cleanup job fires
        following a soft-delete of the source).

        Returns the count of deleted chunk rows.
        """
        stmt = sa_delete(KnowledgeChunk).where(
            KnowledgeChunk.source_id == source_id
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def delete_by_kb(self, kb_id: UUID) -> int:
        """
        Hard-delete all KnowledgeChunks belonging to a knowledge base.

        WARNING: for large KBs (100k+ chunks) this is a slow operation.
        Use a background job for bulk KB deletion (e.g. tenant offboarding).

        Returns the count of deleted chunk rows.
        """
        stmt = sa_delete(KnowledgeChunk).where(
            KnowledgeChunk.kb_id == kb_id
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    # ── Embedding update ──────────────────────────────────────────────────────

    async def set_embedding(
        self,
        chunk_id: UUID,
        embedding: list[float],
    ) -> bool:
        """
        Set the embedding vector on a chunk.

        Only updates chunks that do not yet have an embedding
        (embedding IS NULL) to prevent overwriting existing vectors
        unintentionally.

        Returns True when the embedding was stored.
        Returns False when the chunk already has an embedding or
        the chunk_id does not exist.
        """
        stmt = (
            update(KnowledgeChunk)
            .where(KnowledgeChunk.id == chunk_id)
            .where(KnowledgeChunk.embedding.is_(None))
            .values(embedding=embedding)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    # ── pgvector RAG similarity search ────────────────────────────────────────

    async def similarity_search(
        self,
        query_embedding: list[float],
        *,
        tenant_id: UUID,
        kb_ids: list[UUID],
        top_k: int = 6,
        score_threshold: float = 0.65,
        ef_search: int = 100,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[ChunkSearchResult]:
        """
        Core RAG retrieval query — cosine similarity search.

        Applies the pre-filter pattern for multi-tenant HNSW efficiency:
          1. WHERE tenant_id = :tid   (B-tree — eliminates other tenants)
          2. AND kb_id = ANY(:kb_ids) (B-tree — scopes to relevant KBs)
          3. AND embedding IS NOT NULL (partial index idx_kb_chunks_embedded)
          4. ORDER BY embedding <=> :vec (HNSW scan on filtered set)
          5. HAVING 1 - distance >= score_threshold

        Sets SET LOCAL hnsw.ef_search = :ef_search before executing so
        the recall/latency trade-off can be tuned per-request without
        affecting other concurrent queries.

        Parameters:
            query_embedding:  384-dim float list from the embedding model
            tenant_id:        restrict search to this tenant's chunks
            kb_ids:           restrict to these specific KBs (ordered list;
                              use KnowledgeBaseRepository.get_kb_ids_for_retrieval)
            top_k:            maximum results to return (default 6)
            score_threshold:  minimum cosine similarity (default 0.65)
            ef_search:        HNSW recall parameter (default 100; raise to 200
                              for higher precision at ~10% latency cost)
            metadata_filter:  optional dict of metadata_ JSONB key-value pairs
                              to filter results (e.g. {"section": "§3"})

        Returns a list of ChunkSearchResult ordered by similarity DESC.
        Returns empty list when no chunks meet the threshold.
        """
        if not kb_ids:
            return []

        # Set HNSW ef_search for this transaction
        await self._session.execute(
            text("SET LOCAL hnsw.ef_search = :ef"),
            {"ef": ef_search},
        )

        # Build the similarity expression: 1 - cosine_distance
        vec_literal = str(query_embedding)
        distance_expr = text(
            f"1 - (kc.embedding <=> '{vec_literal}'::vector)"
        )

        # Use raw SQL for the vector operation — SQLAlchemy ORM does not
        # natively compose pgvector <=> operator in a typed select().
        # The result rows are mapped back to KnowledgeChunk ORM objects.
        raw_sql = text("""
            SELECT kc.id,
                   1 - (kc.embedding <=> :query_vec ::vector) AS similarity
            FROM   knowledge_chunks kc
            WHERE  kc.tenant_id  = :tenant_id
              AND  kc.kb_id      = ANY(:kb_ids)
              AND  kc.embedding IS NOT NULL
              AND  1 - (kc.embedding <=> :query_vec ::vector) >= :threshold
            ORDER BY kc.embedding <=> :query_vec ::vector
            LIMIT  :top_k
        """)

        rows = (
            await self._session.execute(
                raw_sql,
                {
                    "query_vec": vec_literal,
                    "tenant_id": str(tenant_id),
                    "kb_ids": [str(k) for k in kb_ids],
                    "threshold": score_threshold,
                    "top_k": top_k,
                },
            )
        ).all()

        if not rows:
            return []

        # Load full ORM objects for the matching chunk ids
        chunk_ids = [r[0] for r in rows]
        similarity_map: dict[str, float] = {str(r[0]): float(r[1]) for r in rows}

        chunks_stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.id.in_(chunk_ids))
        )

        # Apply optional metadata JSONB filter
        if metadata_filter:
            for key, value in metadata_filter.items():
                chunks_stmt = chunks_stmt.where(
                    KnowledgeChunk.metadata_[key].as_string() == str(value)
                )

        chunk_result = await self._session.execute(chunks_stmt)
        chunks_by_id = {
            str(c.id): c for c in chunk_result.scalars().all()
        }

        # Reconstruct ordered results (SQL ORDER BY is canonical)
        results: list[ChunkSearchResult] = []
        for chunk_id_str, sim in sorted(
            similarity_map.items(), key=lambda x: x[1], reverse=True
        ):
            chunk = chunks_by_id.get(chunk_id_str)
            if chunk is not None:
                results.append(ChunkSearchResult(chunk=chunk, similarity=sim))

        return results

    async def similarity_search_by_tenant(
        self,
        query_embedding: list[float],
        *,
        tenant_id: UUID,
        top_k: int = 6,
        score_threshold: float = 0.65,
        ef_search: int = 100,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[ChunkSearchResult]:
        """
        Similarity search across ALL knowledge bases in a tenant.

        Broader than similarity_search() — does not restrict to specific
        kb_ids. Useful for global search UIs where the user wants results
        from any KB in the tenant.

        Uses the same pre-filter / HNSW pattern as similarity_search()
        but without the kb_id = ANY(:kb_ids) condition.
        """
        await self._session.execute(
            text("SET LOCAL hnsw.ef_search = :ef"),
            {"ef": ef_search},
        )

        vec_literal = str(query_embedding)
        raw_sql = text("""
            SELECT kc.id,
                   1 - (kc.embedding <=> :query_vec ::vector) AS similarity
            FROM   knowledge_chunks kc
            WHERE  kc.tenant_id  = :tenant_id
              AND  kc.embedding IS NOT NULL
              AND  1 - (kc.embedding <=> :query_vec ::vector) >= :threshold
            ORDER BY kc.embedding <=> :query_vec ::vector
            LIMIT  :top_k
        """)

        rows = (
            await self._session.execute(
                raw_sql,
                {
                    "query_vec": vec_literal,
                    "tenant_id": str(tenant_id),
                    "threshold": score_threshold,
                    "top_k": top_k,
                },
            )
        ).all()

        if not rows:
            return []

        chunk_ids = [r[0] for r in rows]
        similarity_map = {str(r[0]): float(r[1]) for r in rows}

        chunks_stmt = select(KnowledgeChunk).where(
            KnowledgeChunk.id.in_(chunk_ids)
        )
        if metadata_filter:
            for key, value in metadata_filter.items():
                chunks_stmt = chunks_stmt.where(
                    KnowledgeChunk.metadata_[key].as_string() == str(value)
                )

        chunk_result = await self._session.execute(chunks_stmt)
        chunks_by_id = {str(c.id): c for c in chunk_result.scalars().all()}

        return [
            ChunkSearchResult(chunk=chunks_by_id[cid], similarity=sim)
            for cid, sim in sorted(
                similarity_map.items(), key=lambda x: x[1], reverse=True
            )
            if cid in chunks_by_id
        ]

    async def similarity_search_by_knowledge_base(
        self,
        query_embedding: list[float],
        *,
        kb_id: UUID,
        tenant_id: UUID,
        top_k: int = 6,
        score_threshold: float = 0.65,
        ef_search: int = 100,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[ChunkSearchResult]:
        """
        Similarity search restricted to a single knowledge base.

        More specific than similarity_search_by_tenant().
        Used when a UI or API endpoint targets a specific KB for search.
        tenant_id is required as a belt-and-suspenders filter alongside
        the HNSW pre-filter.
        """
        return await self.similarity_search(
            query_embedding,
            tenant_id=tenant_id,
            kb_ids=[kb_id],
            top_k=top_k,
            score_threshold=score_threshold,
            ef_search=ef_search,
            metadata_filter=metadata_filter,
        )

    # ── Override create for descriptive DuplicateError ───────────────────────

    async def create(  # type: ignore[override]
        self, data: KnowledgeChunkCreate
    ) -> KnowledgeChunk:
        """
        Insert a new KnowledgeChunk.

        Maps the uq_chunk_source_index IntegrityError to DuplicateError.

        Transaction note: no rollback here — session owner handles it.
        """
        from sqlalchemy.exc import IntegrityError as _IntegrityError

        try:
            chunk = KnowledgeChunk(
                kb_id=data.kb_id,
                source_id=data.source_id,
                tenant_id=data.tenant_id,
                chunk_index=data.chunk_index,
                content=data.content,
                embedding=data.embedding,
                metadata_=data.metadata or {},
            )
            self._session.add(chunk)
            await self._session.flush()
            await self._session.refresh(chunk)
            return chunk
        except _IntegrityError as exc:
            raise DuplicateError(
                entity="KnowledgeChunk",
                field="(source_id, chunk_index)",
                value=f"{data.source_id}:{data.chunk_index}",
            ) from exc
