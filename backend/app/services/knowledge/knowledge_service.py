# FILE: app/services/knowledge/knowledge_service.py
"""
KnowledgeBaseService — KB CRUD, source management, retrieval resolution.

KnowledgeSourceService — source ingestion lifecycle (create, list, delete).
"""
from __future__ import annotations

from uuid import UUID

from app.core.exceptions import NotFoundError
from app.database.unit_of_work import UnitOfWork
from app.models.knowledge import KnowledgeBase, KnowledgeSource
from app.repositories.base import Page
from app.repositories.knowledge.knowledge_base_repository import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeSourceCreate,
)


class KnowledgeBaseService:
    """
    Knowledge base lifecycle management.

    Each method opens its own UnitOfWork.
    """

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_knowledge_bases(
        self, tenant_id: UUID, page: int = 1, page_size: int = 20
    ) -> Page[KnowledgeBase]:
        """
        List all knowledge bases for a tenant (both 'tenant' and 'module'
        scopes).

        Raises:
            None — empty page returned if no KBs exist
        """
        async with UnitOfWork() as uow:
            return await uow.knowledge_bases.list_by_tenant(
                tenant_id, page=page, page_size=page_size
            )

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_knowledge_base(
        self, kb_id: UUID, tenant_id: UUID | None = None
    ) -> KnowledgeBase:
        """
        Fetch a knowledge base by id.

        When tenant_id is provided, acts as a belt-and-suspenders guard
        over RLS.

        Raises:
            NotFoundError — KB not found or is soft-deleted
        """
        async with UnitOfWork() as uow:
            kb = await uow.knowledge_bases.get_by_id(kb_id, tenant_id=tenant_id)
            if kb is None:
                raise NotFoundError("KnowledgeBase", kb_id)
            return kb

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create_knowledge_base(
        self,
        name: str,
        tenant_id: UUID,
        scope: str = "tenant",
        description: str | None = None,
        created_by: UUID | None = None,
        module_id: UUID | None = None,
    ) -> KnowledgeBase:
        """
        Create a new knowledge base.

        scope: "tenant" | "module"
        When scope="module", module_id must be set.

        Raises:
            ValidationError — scope/module_id inconsistency (raised by repo)
            ConflictError   — name already exists for this tenant
        """
        async with UnitOfWork() as uow:
            kb = await uow.knowledge_bases.create(
                KnowledgeBaseCreate(
                    tenant_id=tenant_id,
                    scope=scope,
                    name=name,
                    description=description,
                    module_id=module_id,
                    created_by=created_by,
                )
            )
            await uow.commit()
            return kb

    async def update_knowledge_base(
        self,
        kb_id: UUID,
        name: str | None = None,
        description: str | None = None,
    ) -> KnowledgeBase:
        """
        Apply a partial update to a knowledge base.

        Raises:
            NotFoundError — KB not found
        """
        async with UnitOfWork() as uow:
            kb = await uow.knowledge_bases.update(
                kb_id,
                KnowledgeBaseUpdate(name=name, description=description),
            )
            await uow.commit()
            return kb

    async def delete_knowledge_base(self, kb_id: UUID) -> None:
        """
        Soft-delete a knowledge base.

        Raises:
            NotFoundError — KB not found
        """
        async with UnitOfWork() as uow:
            await uow.knowledge_bases.soft_delete(kb_id)
            await uow.commit()

    # ── Retrieval resolution ──────────────────────────────────────────────────

    async def get_kb_ids_for_module(
        self, module_id: UUID, tenant_id: UUID
    ) -> list[UUID]:
        """
        Resolve the ordered list of KB ids to query for RAG retrieval.

        Returns:
          1. Module-specific KBs (scope='module', module_id matches)
          2. Tenant-wide KBs (scope='tenant') for the same tenant

        This ordering prioritizes module-specific knowledge over
        tenant-wide knowledge.
        """
        async with UnitOfWork() as uow:
            return await uow.knowledge_bases.get_kb_ids_for_retrieval(
                module_id, tenant_id
            )


class KnowledgeSourceService:
    """
    Knowledge source ingestion lifecycle.

    Each method opens its own UnitOfWork.
    """

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_sources(
        self, kb_id: UUID, page: int = 1, page_size: int = 20
    ) -> Page[KnowledgeSource]:
        """
        List sources for a knowledge base.

        Returns a paginated result even though the result set is typically
        small for consistency with other list methods.

        Raises:
            None — empty page returned if no sources exist
        """
        async with UnitOfWork() as uow:
            sources = await uow.knowledge_bases.get_active_sources(kb_id)
            # Manual pagination since repository returns list, not Page
            start = (page - 1) * page_size
            end = start + page_size
            items = sources[start:end]
            return Page(
                items=items,
                total=len(sources),
                page=page,
                page_size=page_size,
            )

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_source(self, source_id: UUID) -> KnowledgeSource:
        """
        Fetch a knowledge source by id.

        Raises:
            NotFoundError — source not found
        """
        async with UnitOfWork() as uow:
            # Use a generic get via session (sources have no dedicated get method)
            source = await uow.session.get(KnowledgeSource, source_id)
            if source is None or source.is_deleted:
                raise NotFoundError("KnowledgeSource", source_id)
            return source

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_source_from_text(
        self,
        kb_id: UUID,
        title: str,
        content: str,
        tenant_id: UUID,
        created_by: UUID | None = None,
    ) -> KnowledgeSource:
        """
        Create a source from pasted text.

        The ingestion worker will pick up the source (status='pending')
        and process it asynchronously.

        Raises:
            NotFoundError — KB not found
        """
        async with UnitOfWork() as uow:
            kb = await uow.knowledge_bases.get_by_id(kb_id, tenant_id=tenant_id)
            if kb is None:
                raise NotFoundError("KnowledgeBase", kb_id)

            source = await uow.knowledge_bases.create_source(
                KnowledgeSourceCreate(
                    kb_id=kb_id,
                    type="paste",
                    title=title,
                    created_by=created_by,
                )
            )
            await uow.commit()
            return source

    async def create_source_from_file(
        self,
        kb_id: UUID,
        title: str,
        file_path: str,
        mime_type: str,
        file_size: int,
        tenant_id: UUID,
        created_by: UUID | None = None,
    ) -> KnowledgeSource:
        """
        Create a source from an uploaded file.

        file_path is the server-side storage path; never exposed to clients.

        Raises:
            NotFoundError — KB not found
        """
        async with UnitOfWork() as uow:
            kb = await uow.knowledge_bases.get_by_id(kb_id, tenant_id=tenant_id)
            if kb is None:
                raise NotFoundError("KnowledgeBase", kb_id)

            source = await uow.knowledge_bases.create_source(
                KnowledgeSourceCreate(
                    kb_id=kb_id,
                    type="upload",
                    title=title,
                    file_path=file_path,
                    mime_type=mime_type,
                    file_size_bytes=file_size,
                    created_by=created_by,
                )
            )
            await uow.commit()
            return source

    async def create_source_from_url(
        self,
        kb_id: UUID,
        url: str,
        title: str,
        tenant_id: UUID,
        created_by: UUID | None = None,
    ) -> KnowledgeSource:
        """
        Create a source from a URL.

        The ingestion worker will fetch + extract main content.

        Raises:
            NotFoundError — KB not found
        """
        async with UnitOfWork() as uow:
            kb = await uow.knowledge_bases.get_by_id(kb_id, tenant_id=tenant_id)
            if kb is None:
                raise NotFoundError("KnowledgeBase", kb_id)

            source = await uow.knowledge_bases.create_source(
                KnowledgeSourceCreate(
                    kb_id=kb_id,
                    type="url",
                    title=title,
                    url=url,
                    created_by=created_by,
                )
            )
            await uow.commit()
            return source

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete_source(self, source_id: UUID) -> None:
        """
        Soft-delete a knowledge source.

        The associated chunks are cleaned up by a background job
        (not cascade-deleted immediately).
        """
        async with UnitOfWork() as uow:
            deleted = await uow.knowledge_bases.soft_delete_source(source_id)
            if not deleted:
                raise NotFoundError("KnowledgeSource", source_id)
            await uow.commit()
