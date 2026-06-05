"""
KnowledgeBaseRepository — async SQLAlchemy 2.0 implementation.

Covers:
  KnowledgeBase CRUD with soft-delete + optimistic locking
  Tenant-scoped listing (scope: 'tenant' | 'module')
  Module-specific KB listing
  Name-based lookup within a tenant
  KnowledgeSource management (create, list active, count)
  Denormalized chunk_count increment

Model: KnowledgeBase (BusinessBase + OptimisticLockMixin)
  Soft-delete:      yes (deleted_at from BusinessBase)
  Optimistic lock:  yes (version from OptimisticLockMixin)
  Tenant filtering: yes (tenant_id NOT NULL — every KB belongs to a tenant)

Model: KnowledgeSource (UUIDPrimaryKeyMixin + TimestampMixin)
  Soft-delete:      yes (deleted_at column present)
  No optimistic lock, no tenant_id (scoped through parent KB)

Transaction contract:
  No commit() or rollback() calls here.
  flush() is used to materialise PKs and detect constraint violations.
  The session owner (get_db) handles commit/rollback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeBase, KnowledgeSource
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import DuplicateError, NotFoundError


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class KnowledgeBaseCreate:
    """Data required to create a new KnowledgeBase row."""

    tenant_id: UUID
    scope: str                       # 'tenant' | 'module'
    name: str
    description: Optional[str] = None
    module_id: Optional[UUID] = None  # required when scope='module'
    created_by: Optional[UUID] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "tenant_id": self.tenant_id,
            "scope": self.scope,
            "name": self.name,
            "description": self.description,
            "module_id": self.module_id,
            "created_by": self.created_by,
            "chunk_count": 0,
        }


@dataclass
class KnowledgeBaseUpdate:
    """Partial update for a KnowledgeBase."""

    name: Optional[str] = None
    description: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result: dict = {}
        if self.name is not None:
            result["name"] = self.name
        if self.description is not None:
            result["description"] = self.description
        return result


@dataclass
class KnowledgeSourceCreate:
    """Data required to create a new KnowledgeSource row."""

    kb_id: UUID
    type: str                        # 'paste' | 'upload' | 'url'
    title: str
    created_by: Optional[UUID] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    crawl_frequency: Optional[str] = None


# ── Repository ────────────────────────────────────────────────────────────────

class KnowledgeBaseRepository(
    BaseRepository[KnowledgeBase, KnowledgeBaseCreate, KnowledgeBaseUpdate]
):
    """
    All database operations for KnowledgeBase and KnowledgeSource.

    KnowledgeSource is managed here (not in a separate repository)
    because sources are always created, listed, and deleted in the
    context of a parent KnowledgeBase — they have no independent access
    pattern.

    BaseRepository provides: get, get_or_raise, create, update,
    soft_delete, hard_delete, exists, list_paginated.
    """

    model = KnowledgeBase

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── ID-based lookup ───────────────────────────────────────────────────────

    async def get_by_id(
        self,
        kb_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> KnowledgeBase | None:
        """
        Fetch a KnowledgeBase by id.

        When tenant_id is provided, adds AND tenant_id = :tid as a
        belt-and-suspenders guard over RLS.
        Returns None if soft-deleted or not found.
        """
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .where(KnowledgeBase.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(KnowledgeBase.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Name-based lookup ─────────────────────────────────────────────────────

    async def get_by_name(
        self,
        name: str,
        *,
        tenant_id: UUID,
        scope: str | None = None,
    ) -> KnowledgeBase | None:
        """
        Fetch a KnowledgeBase by name within a tenant.

        Case-sensitive match. When scope is provided, adds
        AND scope = :scope to narrow the result (useful when a tenant
        could have a tenant-KB and a module-KB with the same name).
        Returns None when not found or soft-deleted.
        """
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == tenant_id)
            .where(KnowledgeBase.name == name)
            .where(KnowledgeBase.deleted_at.is_(None))
        )
        if scope is not None:
            stmt = stmt.where(KnowledgeBase.scope == scope)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Tenant-scoped listing ─────────────────────────────────────────────────

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        scope: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[KnowledgeBase]:
        """
        List KnowledgeBases for a tenant, ordered by name ascending.

        When scope is provided ('tenant' or 'module'), restricts results
        to that scope only. Otherwise returns all scopes.

        Uses idx_kb_tenant_scope partial index.
        """
        base = (
            select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == tenant_id)
            .where(KnowledgeBase.deleted_at.is_(None))
        )
        if scope is not None:
            base = base.where(KnowledgeBase.scope == scope)

        count_stmt = select(func.count()).select_from(base.subquery())
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        data_stmt = (
            base
            .order_by(KnowledgeBase.name.asc())
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

    # ── Module-specific KB listing ────────────────────────────────────────────

    async def list_by_module(
        self,
        module_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> list[KnowledgeBase]:
        """
        Return all KnowledgeBases attached to a specific module
        (scope='module').

        When tenant_id is provided, further restricts to that tenant
        (belt-and-suspenders over RLS).

        Uses idx_kb_module partial index.
        Returns results ordered by name ascending.
        """
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.module_id == module_id)
            .where(KnowledgeBase.scope == "module")
            .where(KnowledgeBase.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(KnowledgeBase.tenant_id == tenant_id)
        stmt = stmt.order_by(KnowledgeBase.name.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_kb_ids_for_retrieval(
        self,
        module_id: UUID,
        tenant_id: UUID,
    ) -> list[UUID]:
        """
        Resolve the ordered list of KB ids to query for RAG retrieval.

        Returns:
          1. Module-specific KBs (scope='module', module_id matches)
             — these are ordered first (higher specificity)
          2. Tenant-wide KBs (scope='tenant') for the same tenant
             — appended as fallback context

        The order determines retrieval priority. The chunk repository's
        similarity_search() accepts this list and applies it as
        AND kb_id = ANY(:kb_ids).
        """
        module_stmt = (
            select(KnowledgeBase.id)
            .where(KnowledgeBase.module_id == module_id)
            .where(KnowledgeBase.scope == "module")
            .where(KnowledgeBase.tenant_id == tenant_id)
            .where(KnowledgeBase.deleted_at.is_(None))
        )
        tenant_stmt = (
            select(KnowledgeBase.id)
            .where(KnowledgeBase.scope == "tenant")
            .where(KnowledgeBase.tenant_id == tenant_id)
            .where(KnowledgeBase.deleted_at.is_(None))
        )
        module_ids = list(
            (await self._session.execute(module_stmt)).scalars().all()
        )
        tenant_ids = list(
            (await self._session.execute(tenant_stmt)).scalars().all()
        )
        # Module-specific first, then tenant-wide (deduped)
        seen: set[UUID] = set(module_ids)
        combined = list(module_ids)
        for tid in tenant_ids:
            if tid not in seen:
                combined.append(tid)
        return combined

    # ── Source management ─────────────────────────────────────────────────────

    async def create_source(
        self, data: KnowledgeSourceCreate
    ) -> KnowledgeSource:
        """
        Create a new KnowledgeSource within a KnowledgeBase.

        The new source always starts with status='pending'.
        The ingestion worker will update it to 'processing' then
        'completed' or 'failed'.

        Transaction note: no rollback here — session owner handles it.
        """
        try:
            source = KnowledgeSource(
                kb_id=data.kb_id,
                type=data.type,
                title=data.title,
                created_by=data.created_by,
                url=data.url,
                file_path=data.file_path,
                file_size_bytes=data.file_size_bytes,
                mime_type=data.mime_type,
                crawl_frequency=data.crawl_frequency,
                status="pending",
                chunk_count=0,
            )
            self._session.add(source)
            await self._session.flush()
            await self._session.refresh(source)
            return source
        except IntegrityError as exc:
            raise DuplicateError(
                entity="KnowledgeSource",
                field="kb_id",
                value=str(data.kb_id),
            ) from exc

    async def get_active_sources(
        self,
        kb_id: UUID,
        *,
        status: str | None = None,
    ) -> list[KnowledgeSource]:
        """
        Return non-deleted KnowledgeSources for a KB.

        When status is provided (e.g. 'completed'), filters to that
        status only. Without status, returns all non-deleted sources.

        Uses idx_kb_sources_kb_status partial index when status is given.
        Orders by created_at descending (most recently added first).
        """
        stmt = (
            select(KnowledgeSource)
            .where(KnowledgeSource.kb_id == kb_id)
            .where(KnowledgeSource.deleted_at.is_(None))
        )
        if status is not None:
            stmt = stmt.where(KnowledgeSource.status == status)
        stmt = stmt.order_by(KnowledgeSource.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_source_count(
        self,
        kb_id: UUID,
        *,
        status: str | None = None,
    ) -> int:
        """
        Count non-deleted sources for a KB.

        When status is provided, counts only sources with that status.
        """
        stmt = (
            select(func.count())
            .select_from(KnowledgeSource)
            .where(KnowledgeSource.kb_id == kb_id)
            .where(KnowledgeSource.deleted_at.is_(None))
        )
        if status is not None:
            stmt = stmt.where(KnowledgeSource.status == status)
        return (await self._session.execute(stmt)).scalar_one()

    # ── Chunk count management ────────────────────────────────────────────────

    async def increment_chunk_count(
        self,
        kb_id: UUID,
        delta: int,
    ) -> None:
        """
        Atomically add delta to KnowledgeBase.chunk_count.

        Called by the ingestion service after processing a source.
        Uses a single atomic UPDATE (no read-modify-write) to avoid
        race conditions when multiple sources complete simultaneously.

        delta may be negative to subtract (e.g. when a source is deleted).
        """
        stmt = (
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(chunk_count=KnowledgeBase.chunk_count + delta)
        )
        await self._session.execute(stmt)

    # ── Source soft-delete ────────────────────────────────────────────────────

    async def soft_delete_source(self, source_id: UUID) -> bool:
        """
        Soft-delete a KnowledgeSource by setting deleted_at = now().

        Returns True when the source was found and marked deleted.
        Returns False when already deleted or not found.

        The associated KnowledgeChunk rows are NOT immediately deleted —
        a background job handles chunk cleanup after soft-delete.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(KnowledgeSource)
            .where(KnowledgeSource.id == source_id)
            .where(KnowledgeSource.deleted_at.is_(None))
            .values(deleted_at=now)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    # ── Override create for descriptive DuplicateError ───────────────────────

    async def create(  # type: ignore[override]
        self, data: KnowledgeBaseCreate
    ) -> KnowledgeBase:
        """
        Insert a new KnowledgeBase.

        Transaction note: no rollback here — session owner handles it.
        """
        try:
            kb = KnowledgeBase(
                tenant_id=data.tenant_id,
                scope=data.scope,
                name=data.name,
                description=data.description,
                module_id=data.module_id,
                created_by=data.created_by,
                chunk_count=0,
            )
            self._session.add(kb)
            await self._session.flush()
            await self._session.refresh(kb)
            return kb
        except IntegrityError as exc:
            raise DuplicateError(
                entity="KnowledgeBase",
                field="name",
                value=data.name,
            ) from exc
