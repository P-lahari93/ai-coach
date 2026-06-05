# FILE: app/rag/ingestion_service.py
"""
IngestionService — orchestrate document ingestion pipeline.

Pipeline stages:
  1. Load document (DocumentLoader)
  2. Clean text (TextCleaner)
  3. Chunk text (ChunkingService)
  4. Store chunks in DB (embedding=None initially)

Background worker (separate process) handles embedding generation:
  - Polls for chunks with embedding IS NULL
  - Generates embeddings in batches
  - Updates chunk.embedding via repository

This separation keeps the ingestion API fast (no blocking on embedding).
"""
from __future__ import annotations

from uuid import UUID

from app.core.exceptions import IngestionError
from app.database.unit_of_work import UnitOfWork
from app.repositories.knowledge.knowledge_chunk_repository import KnowledgeChunkCreate
from app.rag.chunking_service import ChunkingService
from app.rag.document_loader import DocumentLoader
from app.rag.text_cleaner import TextCleaner


class IngestionService:
    """Orchestrate document ingestion into knowledge base."""

    def __init__(
        self,
        document_loader: DocumentLoader,
        text_cleaner: TextCleaner,
        chunking_service: ChunkingService,
    ) -> None:
        """
        Initialize ingestion service.

        Args:
            document_loader: service for loading documents
            text_cleaner: service for cleaning text
            chunking_service: service for chunking text
        """
        self._loader = document_loader
        self._cleaner = text_cleaner
        self._chunker = chunking_service

    async def ingest_text(
        self,
        kb_id: UUID,
        source_id: UUID,
        tenant_id: UUID,
        title: str,
        content: str,
        uow: UnitOfWork,
    ) -> int:
        """
        Ingest plain text content directly.

        Used for paste/manual entry sources.

        Args:
            kb_id: knowledge base UUID
            source_id: knowledge source UUID
            tenant_id: tenant UUID
            title: source title for metadata
            content: raw text content
            uow: unit of work for DB transaction

        Returns:
            number of chunks created

        Raises:
            IngestionError: when ingestion fails
        """
        try:
            # Clean text
            cleaned = self._cleaner.clean(content)
            
            # Chunk text
            chunks = await self._chunker.chunk_text(
                text=cleaned,
                source_title=title,
                source_url=None,
            )
            
            # Store chunks
            for chunk in chunks:
                chunk_data = KnowledgeChunkCreate(
                    kb_id=kb_id,
                    source_id=source_id,
                    tenant_id=tenant_id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    embedding=None,  # embedding worker will populate
                    metadata=chunk.metadata,
                )
                await uow.knowledge_chunks.create(chunk_data)
            
            # Update KB chunk count
            await uow.knowledge_bases.increment_chunk_count(kb_id, len(chunks))
            
            return len(chunks)
            
        except Exception as exc:
            raise IngestionError(
                f"Failed to ingest text for source {source_id}: {exc}"
            ) from exc

    async def ingest_file(
        self,
        kb_id: UUID,
        source_id: UUID,
        tenant_id: UUID,
        title: str,
        file_path: str,
        mime_type: str,
        uow: UnitOfWork,
    ) -> int:
        """
        Ingest a file from the filesystem.

        Used for uploaded document sources.

        Args:
            kb_id: knowledge base UUID
            source_id: knowledge source UUID
            tenant_id: tenant UUID
            title: source title for metadata
            file_path: absolute path to file
            mime_type: MIME type of file
            uow: unit of work for DB transaction

        Returns:
            number of chunks created

        Raises:
            IngestionError: when ingestion fails
        """
        try:
            # Load document
            content = await self._loader.load(file_path, mime_type)
            
            # Clean text
            cleaned = self._cleaner.clean(content)
            
            # Chunk text
            chunks = await self._chunker.chunk_text(
                text=cleaned,
                source_title=title,
                source_url=None,
            )
            
            # Store chunks
            for chunk in chunks:
                chunk_data = KnowledgeChunkCreate(
                    kb_id=kb_id,
                    source_id=source_id,
                    tenant_id=tenant_id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    embedding=None,
                    metadata=chunk.metadata,
                )
                await uow.knowledge_chunks.create(chunk_data)
            
            # Update KB chunk count
            await uow.knowledge_bases.increment_chunk_count(kb_id, len(chunks))
            
            return len(chunks)
            
        except Exception as exc:
            raise IngestionError(
                f"Failed to ingest file {file_path} for source {source_id}: {exc}"
            ) from exc

    async def ingest_url(
        self,
        kb_id: UUID,
        source_id: UUID,
        tenant_id: UUID,
        title: str,
        url: str,
        uow: UnitOfWork,
    ) -> int:
        """
        Ingest content from a URL.

        Used for web page sources.

        Args:
            kb_id: knowledge base UUID
            source_id: knowledge source UUID
            tenant_id: tenant UUID
            title: source title for metadata
            url: URL to fetch
            uow: unit of work for DB transaction

        Returns:
            number of chunks created

        Raises:
            IngestionError: when ingestion fails
        """
        try:
            # Load URL
            content, detected_mime = await self._loader.load_from_url(url)
            
            # Clean text
            cleaned = self._cleaner.clean(content)
            
            # Chunk text
            chunks = await self._chunker.chunk_text(
                text=cleaned,
                source_title=title,
                source_url=url,  # include URL in metadata for citations
            )
            
            # Store chunks
            for chunk in chunks:
                chunk_data = KnowledgeChunkCreate(
                    kb_id=kb_id,
                    source_id=source_id,
                    tenant_id=tenant_id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    embedding=None,
                    metadata=chunk.metadata,
                )
                await uow.knowledge_chunks.create(chunk_data)
            
            # Update KB chunk count
            await uow.knowledge_bases.increment_chunk_count(kb_id, len(chunks))
            
            return len(chunks)
            
        except Exception as exc:
            raise IngestionError(
                f"Failed to ingest URL {url} for source {source_id}: {exc}"
            ) from exc
