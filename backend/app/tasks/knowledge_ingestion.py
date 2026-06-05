from __future__ import annotations
import logging
from uuid import UUID

from sqlalchemy import update

from app.core.exceptions import IngestionError
from app.database.unit_of_work import UnitOfWork
from app.models.knowledge import KnowledgeSource
from app.rag.chunking_service import ChunkingService
from app.rag.document_loader import DocumentLoader
from app.rag.ingestion_service import IngestionService
from app.rag.text_cleaner import TextCleaner
from app.tasks.embedding_generation import generate_embeddings_for_source

logger = logging.getLogger(__name__)


async def run_ingestion(
    source_id: UUID,
    kb_id: UUID,
    tenant_id: UUID,
    source_type: str,
    title: str,
    content: str | None = None,
    file_path: str | None = None,
    mime_type: str | None = None,
    url: str | None = None,
) -> None:
    """Run the full ingestion pipeline and trigger embedding generation."""
    ingestion_service = IngestionService(
        document_loader=DocumentLoader(),
        text_cleaner=TextCleaner(),
        chunking_service=ChunkingService(),
    )

    async with UnitOfWork() as uow:
        try:
            chunk_count = 0
            if source_type == "paste" and content:
                chunk_count = await ingestion_service.ingest_text(
                    kb_id=kb_id, source_id=source_id,
                    tenant_id=tenant_id, title=title,
                    content=content, uow=uow,
                )
            elif source_type == "upload" and file_path and mime_type:
                chunk_count = await ingestion_service.ingest_file(
                    kb_id=kb_id, source_id=source_id,
                    tenant_id=tenant_id, title=title,
                    file_path=file_path, mime_type=mime_type, uow=uow,
                )
            elif source_type == "url" and url:
                chunk_count = await ingestion_service.ingest_url(
                    kb_id=kb_id, source_id=source_id,
                    tenant_id=tenant_id, title=title,
                    url=url, uow=uow,
                )

            await uow.session.execute(
                update(KnowledgeSource)
                .where(KnowledgeSource.id == source_id)
                .values(status="completed", chunk_count=chunk_count)
            )
            await uow.commit()
            logger.info("Ingestion complete: %d chunks for source %s", chunk_count, source_id)

        except IngestionError as exc:
            await uow.session.execute(
                update(KnowledgeSource)
                .where(KnowledgeSource.id == source_id)
                .values(status="failed", error_message=str(exc))
            )
            await uow.commit()
            logger.error("Ingestion failed for source %s: %s", source_id, exc)
            return

    await generate_embeddings_for_source(source_id, kb_id, tenant_id)
