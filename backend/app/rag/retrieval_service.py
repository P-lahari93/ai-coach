# FILE: app/rag/retrieval_service.py
"""
RetrievalService — orchestrate RAG knowledge retrieval.

Responsibilities:
  1. Resolve KB IDs for retrieval scope (module + tenant)
  2. Generate query embedding
  3. Execute similarity search via KnowledgeChunkRepository
  4. Return ranked ChunkSearchResult list

The service does NOT format citations — that's handled by CitationService.
This service focuses purely on the retrieval mechanics.
"""
from __future__ import annotations

from uuid import UUID

from app.core.config import settings
from app.database.unit_of_work import UnitOfWork
from app.repositories.knowledge.knowledge_chunk_repository import ChunkSearchResult
from app.rag.embedding_service import EmbeddingService


class RetrievalService:
    """Orchestrate RAG knowledge retrieval."""

    def __init__(
        self,
        embedding_service: EmbeddingService,
    ) -> None:
        """
        Initialize retrieval service.

        Args:
            embedding_service: service for generating query embeddings
        """
        self._embedding_service = embedding_service

    async def retrieve(
        self,
        query: str,
        tenant_id: UUID,
        module_id: UUID | None,
        top_k: int | None = None,
        score_threshold: float | None = None,
        uow: UnitOfWork | None = None,
    ) -> list[ChunkSearchResult]:
        """
        Retrieve relevant knowledge chunks for a query.

        Args:
            query: text query to search for
            tenant_id: tenant scope for multi-tenant isolation
            module_id: optional module for module-specific KB weighting
            top_k: max results to return (default from settings)
            score_threshold: min similarity score (default from settings)
            uow: optional unit of work; if None, creates a new one

        Returns:
            list of ChunkSearchResult ordered by similarity descending

        Raises:
            ValueError: when query is empty
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        
        top_k = top_k or settings.RAG_TOP_K
        score_threshold = score_threshold or settings.RAG_SCORE_THRESHOLD
        
        # Generate query embedding
        query_embedding = await self._embedding_service.embed_query(query)
        
        # Use provided UoW or create a new one
        should_close = uow is None
        if uow is None:
            uow = UnitOfWork()
            await uow.__aenter__()
        
        try:
            # Resolve KB IDs for retrieval scope
            if module_id is not None:
                kb_ids = await uow.knowledge_bases.get_kb_ids_for_retrieval(
                    module_id=module_id,
                    tenant_id=tenant_id,
                )
            else:
                # Tenant-wide search (no module context)
                tenant_kbs = await uow.knowledge_bases.list_by_tenant(
                    tenant_id=tenant_id,
                    page=1,
                    page_size=100,  # reasonable limit for tenant-wide KBs
                )
                kb_ids = [kb.id for kb in tenant_kbs.items]
            
            if not kb_ids:
                # No knowledge bases found for this scope
                return []
            
            # Execute similarity search
            results = await uow.knowledge_chunks.similarity_search(
                query_embedding=query_embedding,
                tenant_id=tenant_id,
                kb_ids=kb_ids,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            
            return results
            
        finally:
            if should_close:
                await uow.__aexit__(None, None, None)
