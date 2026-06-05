"""
CoachingModuleRepository — async SQLAlchemy 2.0 implementation.

Covers:
  Module CRUD with soft-delete + optimistic locking
  Tenant-aware listing (NULL tenant_id = global, visible to all tenants)
  Key-based lookup (slug, e.g. 'sbi_feedback')
  Status-filtered listing (draft / published / archived)
  Full-text search on name
  Status transitions: publish_module(), archive_module()
  KB link resolution for RAG session startup

Model: CoachingModule (BusinessBase + OptimisticLockMixin)
  Soft-delete:        yes (deleted_at from BusinessBase)
  Optimistic lock:    yes (version from OptimisticLockMixin)
  Tenant filtering:   NULL = global (visible to every tenant)
                      tenant_id set = tenant-scoped private module
  Indexes used:
    idx_modules_tenant_status  — (tenant_id, status) WHERE deleted_at IS NULL
    idx_modules_key_active     — (key) WHERE deleted_at IS NULL
    uq_module_key_global       — UNIQUE (key) WHERE tenant_id IS NULL
    uq_module_key_tenant       — UNIQUE (key, tenant_id) WHERE tenant_id IS NOT NULL

Transaction contract:
  No commit() or rollback() calls here.
  The session owner (get_db) handles commit/rollback.
  flush() is used only to materialise PKs or detect constraint violations
  within the caller's transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.module import (
    CoachingModule,
    ModuleFrameworkStep,
    ModuleKnowledgeBase,
    ModulePersona,
    ModulePromptTemplate,
    ModuleVersion,
    Rubric,
)
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    OptimisticLockError,
)


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class CoachingModuleCreate:
    """Data required to create a new CoachingModule row."""

    key: str
    name: str
    status: str = "draft"
    tenant_id: Optional[UUID] = None
    created_by: Optional[UUID] = None
    icon: Optional[str] = None
    blurb: Optional[str] = None
    gamification_overrides: Optional[dict] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        d: dict = {
            "key": self.key,
            "name": self.name,
            "status": self.status,
            "tenant_id": self.tenant_id,
            "created_by": self.created_by,
            "icon": self.icon,
            "blurb": self.blurb,
            "gamification_overrides": self.gamification_overrides or {},
        }
        return d


@dataclass
class CoachingModuleUpdate:
    """Partial update for a CoachingModule."""

    name: Optional[str] = None
    icon: Optional[str] = None
    blurb: Optional[str] = None
    gamification_overrides: Optional[dict] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result: dict = {}
        if self.name is not None:
            result["name"] = self.name
        if self.icon is not None:
            result["icon"] = self.icon
        if self.blurb is not None:
            result["blurb"] = self.blurb
        if self.gamification_overrides is not None:
            result["gamification_overrides"] = self.gamification_overrides
        return result


# ── Repository ────────────────────────────────────────────────────────────────

class CoachingModuleRepository(
    BaseRepository[CoachingModule, CoachingModuleCreate, CoachingModuleUpdate]
):
    """
    All database operations for the CoachingModule aggregate.

    Tenant visibility rule:
        A tenant can see both their own modules (tenant_id = :tid) AND
        global platform modules (tenant_id IS NULL). Every list/search
        method applies this OR condition automatically.

        Private tenant modules (tenant_id = :tid) are only visible to
        that tenant. Global modules are visible to every tenant.

    Status lifecycle:
        draft → published  (via publish_module())
        published → archived  (via archive_module())
        archived → published  (service layer re-publishes if needed)

    Optimistic locking:
        publish_module() and archive_module() require expected_version.
        update() also accepts expected_version for concurrent draft edits.
    """

    model = CoachingModule

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Tenant visibility helper ──────────────────────────────────────────────

    def _tenant_visibility_filter(self, stmt, tenant_id: UUID | None):
        """
        Apply the global-or-tenant visibility rule:
          WHERE (tenant_id IS NULL OR tenant_id = :tid)

        When tenant_id is None (superadmin / platform context),
        no tenant filter is applied — all modules are visible.
        """
        if tenant_id is not None:
            stmt = stmt.where(
                CoachingModule.tenant_id.is_(None)
                | (CoachingModule.tenant_id == tenant_id)
            )
        return stmt

    # ── Key-based lookup ──────────────────────────────────────────────────────

    async def get_by_key(
        self,
        key: str,
        *,
        tenant_id: UUID | None = None,
    ) -> CoachingModule | None:
        """
        Fetch a module by its machine-readable key slug.

        Applies the global-or-tenant visibility rule:
          - Returns the tenant-specific module if one exists for tenant_id.
          - Falls back to the global module (tenant_id IS NULL) if no
            tenant-specific one exists.

        Uses the idx_modules_key_active partial index.

        Returns None when no matching module exists for the given scope.
        """
        stmt = (
            select(CoachingModule)
            .where(CoachingModule.key == key)
            .where(CoachingModule.deleted_at.is_(None))
        )
        stmt = self._tenant_visibility_filter(stmt, tenant_id)
        # Prefer tenant-specific over global: sort tenant_id NULLs last
        stmt = stmt.order_by(CoachingModule.tenant_id.desc().nulls_last())
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_by_key_or_raise(
        self,
        key: str,
        *,
        tenant_id: UUID | None = None,
    ) -> CoachingModule:
        """
        Fetch a module by key. Raises NotFoundError when not found.
        """
        module = await self.get_by_key(key, tenant_id=tenant_id)
        if module is None:
            raise NotFoundError("CoachingModule", key)
        return module

    # ── Eager-load variants ───────────────────────────────────────────────────

    async def get_with_current_version(
        self, module_id: UUID
    ) -> CoachingModule | None:
        """
        Load a module with its current version's full definition.

        Eagerly loads:
          versions (filtered to is_current=True)
            → framework_steps (ordered by step_order)
            → prompt_templates
            → personas
            → rubric

        Used by the session startup engine. All required data for an
        AI coaching session is fetched in a fixed number of round-trips.

        Note: SQLAlchemy selectinload loads ALL versions, then Python-
        filters by is_current. The uq_module_one_current_version partial
        index guarantees at most one is_current row per module.
        """
        stmt = (
            select(CoachingModule)
            .where(CoachingModule.id == module_id)
            .where(CoachingModule.deleted_at.is_(None))
            .options(
                selectinload(CoachingModule.versions).options(
                    selectinload(ModuleVersion.framework_steps),
                    selectinload(ModuleVersion.prompt_templates),
                    selectinload(ModuleVersion.personas),
                    selectinload(ModuleVersion.rubric),
                ),
                selectinload(CoachingModule.knowledge_base_links),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[CoachingModule]:
        """
        List all modules visible to a tenant (global + tenant-owned).

        Applies the global-or-tenant visibility rule.
        Optionally filters by status (draft | published | archived).
        Ordered by name ascending.

        Uses idx_modules_tenant_status partial index when status is
        provided with a specific tenant_id.
        """
        base = (
            select(CoachingModule)
            .where(CoachingModule.deleted_at.is_(None))
        )
        base = self._tenant_visibility_filter(base, tenant_id)
        if status is not None:
            base = base.where(CoachingModule.status == status)

        count_stmt = select(func.count()).select_from(
            base.subquery()
        )
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        data_stmt = (
            base
            .order_by(CoachingModule.name.asc())
            .offset(self._offset(page, page_size))
            .limit(page_size)
        )
        result = await self._session.execute(data_stmt)
        items = list(result.scalars().all())

        return Page(items=items, total=total, page=page, page_size=page_size)

    async def list_published(
        self,
        *,
        tenant_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[CoachingModule]:
        """
        List only published modules visible to a tenant.

        Convenience wrapper around list_by_tenant(status='published').
        When tenant_id is None, returns all published modules platform-wide.
        """
        if tenant_id is not None:
            return await self.list_by_tenant(
                tenant_id,
                status="published",
                page=page,
                page_size=page_size,
            )

        # Superadmin / platform path — no tenant filter
        count_stmt = (
            select(func.count())
            .select_from(CoachingModule)
            .where(CoachingModule.deleted_at.is_(None))
            .where(CoachingModule.status == "published")
        )
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(CoachingModule)
            .where(CoachingModule.deleted_at.is_(None))
            .where(CoachingModule.status == "published")
            .order_by(CoachingModule.name.asc())
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

    # ── Search ────────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        tenant_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[CoachingModule]:
        """
        Search modules by name (case-insensitive ILIKE).

        Applies the global-or-tenant visibility rule so learners only see
        modules available to their tenant. Superadmins can pass
        tenant_id=None to search all modules.

        Parameters:
            query:     substring matched against module name
            tenant_id: restrict to tenant-visible modules (None = all)
            status:    optional status filter
            page / page_size: pagination
        """
        pattern = f"%{query.strip()}%"

        base_filter = (
            CoachingModule.name.ilike(pattern)
            & CoachingModule.deleted_at.is_(None)
        )

        count_stmt = (
            select(func.count())
            .select_from(CoachingModule)
            .where(base_filter)
        )
        data_stmt = (
            select(CoachingModule)
            .where(base_filter)
        )

        # Apply tenant visibility
        if tenant_id is not None:
            visibility = (
                CoachingModule.tenant_id.is_(None)
                | (CoachingModule.tenant_id == tenant_id)
            )
            count_stmt = count_stmt.where(visibility)
            data_stmt = data_stmt.where(visibility)

        if status is not None:
            count_stmt = count_stmt.where(CoachingModule.status == status)
            data_stmt = data_stmt.where(CoachingModule.status == status)

        total: int = (await self._session.execute(count_stmt)).scalar_one()

        data_stmt = (
            data_stmt
            .order_by(CoachingModule.name.asc())
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

    # ── Status transitions ────────────────────────────────────────────────────

    async def publish_module(
        self,
        module_id: UUID,
        *,
        expected_version: int,
    ) -> CoachingModule:
        """
        Transition a module's status from draft (or archived) to published.

        Version-gated: expected_version must match the current version
        column to prevent two concurrent publish requests from racing.

        The version column is incremented atomically with the status change
        so the next caller will receive OptimisticLockError if they hold
        an outdated version number.

        Raises:
            NotFoundError        — module does not exist or is soft-deleted
            OptimisticLockError  — version mismatch (concurrent edit)
            ConflictError        — module is already published
        """
        stmt = (
            update(CoachingModule)
            .where(CoachingModule.id == module_id)
            .where(CoachingModule.deleted_at.is_(None))
            .where(CoachingModule.version == expected_version)
            .where(CoachingModule.status != "published")
            .values(
                status="published",
                version=CoachingModule.version + 1,
            )
            .returning(CoachingModule)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            # Disambiguate failure reason
            check = await self.get(module_id, include_deleted=False)
            if check is None:
                await self._raise_update_failure(module_id, expected_version)
            if check.status == "published":  # type: ignore[union-attr]
                raise ConflictError(
                    f"CoachingModule '{module_id}' is already published."
                )
            # Status was fine but version mismatch
            raise OptimisticLockError("CoachingModule", module_id, expected_version)

        return row

    async def archive_module(
        self,
        module_id: UUID,
        *,
        expected_version: int,
    ) -> CoachingModule:
        """
        Transition a module's status to archived.

        Version-gated to prevent concurrent archive operations racing.
        Archived modules cannot be started as new sessions; existing
        sessions continue to reference the pinned module version.

        Raises:
            NotFoundError        — module does not exist or is soft-deleted
            OptimisticLockError  — version mismatch (concurrent edit)
            ConflictError        — module is already archived
        """
        stmt = (
            update(CoachingModule)
            .where(CoachingModule.id == module_id)
            .where(CoachingModule.deleted_at.is_(None))
            .where(CoachingModule.version == expected_version)
            .where(CoachingModule.status != "archived")
            .values(
                status="archived",
                version=CoachingModule.version + 1,
            )
            .returning(CoachingModule)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            check = await self.get(module_id, include_deleted=False)
            if check is None:
                await self._raise_update_failure(module_id, expected_version)
            if check.status == "archived":  # type: ignore[union-attr]
                raise ConflictError(
                    f"CoachingModule '{module_id}' is already archived."
                )
            raise OptimisticLockError("CoachingModule", module_id, expected_version)

        return row

    # ── KB link resolution ────────────────────────────────────────────────────

    async def get_kb_links(
        self, module_id: UUID
    ) -> list[ModuleKnowledgeBase]:
        """
        Return the knowledge base links for a module, ordered by weight
        descending (highest-weighted KB first) then is_primary descending.

        Used by the RAG retrieval service to resolve which KB collections
        to query at session startup.
        """
        stmt = (
            select(ModuleKnowledgeBase)
            .where(ModuleKnowledgeBase.module_id == module_id)
            .order_by(
                ModuleKnowledgeBase.weight.desc(),
                ModuleKnowledgeBase.is_primary.desc(),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Override create for descriptive DuplicateError ───────────────────────

    async def create(  # type: ignore[override]
        self, data: CoachingModuleCreate
    ) -> CoachingModule:
        """
        Insert a new CoachingModule.

        Maps the NULL-safe partial unique index violation on (key, tenant_id)
        to a descriptive DuplicateError.

        Transaction note: no rollback here — session owner handles it.
        """
        try:
            module = CoachingModule(
                key=data.key,
                name=data.name,
                status=data.status,
                tenant_id=data.tenant_id,
                created_by=data.created_by,
                icon=data.icon,
                blurb=data.blurb,
                gamification_overrides=data.gamification_overrides or {},
            )
            self._session.add(module)
            await self._session.flush()
            await self._session.refresh(module)
            return module
        except IntegrityError as exc:
            raise DuplicateError(
                entity="CoachingModule",
                field="key",
                value=data.key,
            ) from exc
