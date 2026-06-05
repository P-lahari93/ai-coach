# FILE: app/services/module/module_service.py
"""
CoachingModuleService — module catalog CRUD, publishing, archiving.

Responsibilities:
- List modules available to a tenant (global + tenant-owned)
- CRUD operations on draft modules
- Status transitions: draft → published, published → archived
- Version management delegation to ModuleVersionRepository
"""
from __future__ import annotations

from uuid import UUID

from app.core.exceptions import NotFoundError
from app.database.unit_of_work import UnitOfWork
from app.models.module import CoachingModule, ModuleVersion
from app.repositories.base import Page
from app.repositories.module.coaching_module_repository import (
    CoachingModuleCreate,
    CoachingModuleUpdate,
)


class CoachingModuleService:
    """
    Coaching module lifecycle management.

    Each method opens its own UnitOfWork.
    """

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_modules(
        self,
        tenant_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[CoachingModule]:
        """
        List modules visible to a tenant (global + tenant-owned).

        When tenant_id is None (superadmin context), all modules are
        returned regardless of tenant scope.

        status can be: "draft", "published", "archived", or None (all).
        """
        async with UnitOfWork() as uow:
            if tenant_id is not None:
                return await uow.coaching_modules.list_by_tenant(
                    tenant_id, status=status, page=page, page_size=page_size
                )
            # Superadmin: list all (no tenant filter)
            return await uow.coaching_modules.list_paginated(
                page=page, page_size=page_size
            )

    async def list_published(
        self,
        tenant_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[CoachingModule]:
        """
        List only published modules visible to a tenant.

        Used for the learner module catalog.
        """
        async with UnitOfWork() as uow:
            return await uow.coaching_modules.list_published(
                tenant_id=tenant_id, page=page, page_size=page_size
            )

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_module(self, module_id: UUID) -> CoachingModule:
        """
        Fetch a module by id (no relations loaded).

        Raises:
            NotFoundError — module not found or is soft-deleted
        """
        async with UnitOfWork() as uow:
            module = await uow.coaching_modules.get(module_id)
            if module is None:
                raise NotFoundError("CoachingModule", module_id)
            return module

    async def get_module_by_key(
        self, key: str, tenant_id: UUID | None = None
    ) -> CoachingModule:
        """
        Fetch a module by its machine-readable key slug.

        Applies the global-or-tenant visibility rule:
          - Returns tenant-specific module if one exists.
          - Falls back to global module (tenant_id=NULL).

        Raises:
            NotFoundError — module not found for the given scope
        """
        async with UnitOfWork() as uow:
            module = await uow.coaching_modules.get_by_key(
                key, tenant_id=tenant_id
            )
            if module is None:
                raise NotFoundError("CoachingModule", key)
            return module

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create_module(
        self,
        key: str,
        name: str,
        tenant_id: UUID | None = None,
        created_by: UUID | None = None,
        **kwargs,
    ) -> CoachingModule:
        """
        Create a new draft module.

        kwargs can include: icon, blurb, gamification_overrides.

        Raises:
            ConflictError — key already exists for this tenant scope
        """
        async with UnitOfWork() as uow:
            module = await uow.coaching_modules.create(
                CoachingModuleCreate(
                    key=key,
                    name=name,
                    status="draft",
                    tenant_id=tenant_id,
                    created_by=created_by,
                    icon=kwargs.get("icon"),
                    blurb=kwargs.get("blurb"),
                    gamification_overrides=kwargs.get("gamification_overrides"),
                )
            )
            await uow.commit()
            return module

    async def update_module(
        self, module_id: UUID, **kwargs
    ) -> CoachingModule:
        """
        Apply a partial update to a module.

        Accepts: name, icon, blurb, gamification_overrides.

        Raises:
            NotFoundError — module not found
        """
        async with UnitOfWork() as uow:
            module = await uow.coaching_modules.update(
                module_id,
                CoachingModuleUpdate(
                    name=kwargs.get("name"),
                    icon=kwargs.get("icon"),
                    blurb=kwargs.get("blurb"),
                    gamification_overrides=kwargs.get("gamification_overrides"),
                ),
            )
            await uow.commit()
            return module

    async def delete_module(self, module_id: UUID) -> None:
        """
        Soft-delete a module.

        Raises:
            NotFoundError — module not found
        """
        async with UnitOfWork() as uow:
            await uow.coaching_modules.soft_delete(module_id)
            await uow.commit()

    # ── Status transitions ────────────────────────────────────────────────────

    async def publish_module(
        self, module_id: UUID, published_by: UUID
    ) -> CoachingModule:
        """
        Transition a module's status from draft to published.

        Version-gated to prevent concurrent publish attempts.

        Raises:
            NotFoundError       — module not found
            OptimisticLockError — concurrent edit
            ConflictError       — module is already published
        """
        async with UnitOfWork() as uow:
            module = await uow.coaching_modules.get(module_id)
            if module is None:
                raise NotFoundError("CoachingModule", module_id)

            module = await uow.coaching_modules.publish_module(
                module_id, expected_version=module.version
            )
            await uow.commit()
            return module

    async def archive_module(self, module_id: UUID) -> CoachingModule:
        """
        Transition a module's status to archived.

        Archived modules cannot be started as new sessions; existing
        sessions continue to reference the pinned version.

        Raises:
            NotFoundError       — module not found
            OptimisticLockError — concurrent edit
            ConflictError       — module is already archived
        """
        async with UnitOfWork() as uow:
            module = await uow.coaching_modules.get(module_id)
            if module is None:
                raise NotFoundError("CoachingModule", module_id)

            module = await uow.coaching_modules.archive_module(
                module_id, expected_version=module.version
            )
            await uow.commit()
            return module

    # ── Version management ────────────────────────────────────────────────────

    async def get_current_version(self, module_id: UUID) -> ModuleVersion:
        """
        Fetch the current (is_current=True) version for a module.

        Raises:
            NotFoundError — module has no current version
        """
        async with UnitOfWork() as uow:
            version = await uow.module_versions.get_current_version(module_id)
            if version is None:
                raise NotFoundError("ModuleVersion", f"current for {module_id}")
            return version

    async def get_version_full(self, version_id: UUID) -> ModuleVersion:
        """
        Load a specific version with full definition (steps, templates,
        personas, rubric) eagerly loaded.

        Used by session startup and scoring engine.

        Raises:
            NotFoundError — version not found
        """
        async with UnitOfWork() as uow:
            version = await uow.module_versions.get_version_with_definition(
                version_id
            )
            if version is None:
                raise NotFoundError("ModuleVersion", version_id)
            return version
