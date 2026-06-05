from __future__ import annotations
import logging
from uuid import UUID

from app.core.config import settings
from app.database.unit_of_work import UnitOfWork
from app.rag.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


async def generate_embeddings_for_source(
    source_id: UUID,
    kb_id: UUID,
    tenant_id: UUID,
) -> None:
    """Generate embeddings for all unembedded chunks of a source."""
    try:
        embedding_service = EmbeddingService()
    except Exception as exc:
        logger.error("Failed to init EmbeddingService: %s", exc)
        return

    async with UnitOfWork() as uow:
        page = await uow.knowledge_chunks.list_by_source(
            source_id=source_id,
            embedded_only=False,
            page=1,
            page_size=10000,
        )
        chunks_to_embed = [c for c in page.items if c.embedding is None]

        if not chunks_to_embed:
            return

        batch_size = settings.EMBEDDING_BATCH_SIZE
        for i in range(0, len(chunks_to_embed), batch_size):
            batch = chunks_to_embed[i : i + batch_size]
            texts = [c.content for c in batch]
            try:
                embeddings = await embedding_service.embed_batch(texts)
                for chunk, embedding in zip(batch, embeddings):
                    await uow.knowledge_chunks.set_embedding(chunk.id, embedding)
            except Exception as exc:
                logger.error("Embedding batch failed: %s", exc)
                continue

        await uow.commit()
        logger.info("Embedded %d chunks for source %s", len(chunks_to_embed), source_id)
