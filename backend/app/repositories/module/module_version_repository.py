"""
ModuleVersionRepository — async SQLAlchemy 2.0 implementation.

Covers:
  Version history listing for a module
  Current version retrieval (plain and with full definition)
  New draft version creation
  Atomic is_current swap (set_current_version)
  Full definition load for session startup (framework steps, templates,
  personas, rubric)

Model: ModuleVersion (UUIDPrimaryKeyMixin + TimestampMixin + OptimisticLockMixin)
  Soft-delete:      NO — versions are immutable audit records; once created
                    they are never soft-deleted (only CASCADE-deleted when
                    the parent CoachingModule is hard-deleted).
  Optimistic lock:  YES — version column (from OptimisticLockMixin)
  Tenant filtering: N/A — ModuleVersion has no tenant_id column.
                    Tenant isolation is enforced through the parent
                    CoachingModule. Callers must verify the parent
                    module is accessible before querying versions.

Immutability contract:
  Once a ModuleVersion has published_at set and is_current = True,
  its rows in module_framework_steps, module_prompt_templates,
  module_personas, and rubrics are NEVER updated. Any content change
  must call create_new_version() to produce a new draft version row.

  The set_current_version() method atomically:
    1. Clears is_current on the previously current version.
    2. Sets is_current on the new version.
    3. Sets published_at and published_by on the new version.
  This is a two-UPDATE sequence within the caller's transaction,
  protected by the version column on the new version row.

Transaction contract:
  No commit() or rollback() calls here.
  flush() is used to materialise PKs and detect constraint violations.
  The session owner (get_db) handles commit/rollback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.module import (
    ModuleFrameworkStep,
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
class ModuleVersionCreate:
    """
    Data required to create a new ModuleVersion draft row.

    A newly created version always has is_current=False and
    published_at=None. Use set_current_version() to promote it.
    """

    module_id: UUID
    version_number: int
    framework_name: str
    intake_schema: list = field(default_factory=list)
    scoring_rubric: dict = field(default_factory=dict)

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "module_id": self.module_id,
            "version_number": self.version_number,
            "framework_name": self.framework_name,
            "intake_schema": self.intake_schema,
            "scoring_rubric": self.scoring_rubric,
            "is_current": False,
        }


@dataclass
class ModuleVersionUpdate:
    """
    Partial update for a ModuleVersion (draft only — framework content).

    Only draft versions (published_at IS NULL) should be updated.
    The service layer must enforce this constraint before calling.
    """

    framework_name: Optional[str] = None
    intake_schema: Optional[list] = None
    scoring_rubric: Optional[dict] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result: dict = {}
        if self.framework_name is not None:
            result["framework_name"] = self.framework_name
        if self.intake_schema is not None:
            result["intake_schema"] = self.intake_schema
        if self.scoring_rubric is not None:
            result["scoring_rubric"] = self.scoring_rubric
        return result


# ── Repository ────────────────────────────────────────────────────────────────

class ModuleVersionRepository(
    BaseRepository[ModuleVersion, ModuleVersionCreate, ModuleVersionUpdate]
):
    """
    All database operations for the ModuleVersion entity.

    Versions are identified by (module_id, version_number).
    The is_current flag is managed atomically by set_current_version().

    BaseRepository provides: get, get_or_raise, create, update,
    hard_delete, exists, list_paginated.

    This repository extends with:
      - Current version retrieval (plain and full definition)
      - Version history listing
      - New draft version creation
      - Atomic current version promotion
    """

    model = ModuleVersion

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Eager-load option builder ─────────────────────────────────────────────

    @staticmethod
    def _full_definition_options():
        """
        Selectin chain for loading the complete module definition:
          framework_steps → ordered by step_order
          prompt_templates
          personas
          rubric

        Used for session startup, scoring engine, and module preview.
        """
        return (
            selectinload(ModuleVersion.framework_steps),
            selectinload(ModuleVersion.prompt_templates),
            selectinload(ModuleVersion.personas),
            selectinload(ModuleVersion.rubric),
        )

    # ── Current version retrieval ─────────────────────────────────────────────

    async def get_current_version(
        self, module_id: UUID
    ) -> ModuleVersion | None:
        """
        Return the current (is_current=True) version for a module.

        Uses the idx_module_versions_current composite index.
        Returns None when the module has no current version (e.g. all
        versions are drafts, or the module has no versions yet).
        """
        stmt = (
            select(ModuleVersion)
            .where(ModuleVersion.module_id == module_id)
            .where(ModuleVersion.is_current.is_(True))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_current_version_with_definition(
        self, module_id: UUID
    ) -> ModuleVersion | None:
        """
        Return the current version with full coaching definition loaded.

        Eagerly loads framework_steps, prompt_templates, personas, and
        rubric in a single round-trip (selectin strategy — one extra
        query per relationship, all executed concurrently).

        Used by:
          - CoachingSession startup (needs templates + rubric)
          - RoleplaySession startup (needs personas + templates)
          - Scoring engine (needs rubric + scoring_rubric)
        """
        stmt = (
            select(ModuleVersion)
            .where(ModuleVersion.module_id == module_id)
            .where(ModuleVersion.is_current.is_(True))
            .options(*self._full_definition_options())
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_version_by_number(
        self,
        module_id: UUID,
        version_number: int,
    ) -> ModuleVersion | None:
        """
        Fetch a specific version by its (module_id, version_number) pair.

        Uses the uq_module_version_number unique constraint.
        """
        stmt = (
            select(ModuleVersion)
            .where(ModuleVersion.module_id == module_id)
            .where(ModuleVersion.version_number == version_number)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_version_with_definition(
        self,
        version_id: UUID,
    ) -> ModuleVersion | None:
        """
        Load a specific version by id with full definition eagerly loaded.

        Used when a session is pinned to a specific past version and needs
        to re-load that version's definition for scoring.
        """
        stmt = (
            select(ModuleVersion)
            .where(ModuleVersion.id == version_id)
            .options(*self._full_definition_options())
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Version history ───────────────────────────────────────────────────────

    async def version_history(
        self,
        module_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[ModuleVersion]:
        """
        Return the version history for a module, ordered by version_number
        descending (most recent first).

        Used by the admin module editor to display the version timeline
        and allow rolling back to a previous version.
        """
        count_stmt = (
            select(func.count())
            .select_from(ModuleVersion)
            .where(ModuleVersion.module_id == module_id)
        )
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(ModuleVersion)
            .where(ModuleVersion.module_id == module_id)
            .order_by(ModuleVersion.version_number.desc())
            .offset(self._offset(page, page_size))
            .limit(page_size)
        )
        result = await self._session.execute(data_stmt)
        items = list(result.scalars().all())

        return Page(items=items, total=total, page=page, page_size=page_size)

    async def count_versions(self, module_id: UUID) -> int:
        """Return total version count for a module (including drafts)."""
        stmt = (
            select(func.count())
            .select_from(ModuleVersion)
            .where(ModuleVersion.module_id == module_id)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def next_version_number(self, module_id: UUID) -> int:
        """
        Compute the next version_number for a module.

        Returns MAX(version_number) + 1, or 1 if no versions exist yet.
        Used by create_new_version() to assign a monotonically increasing
        version number.
        """
        stmt = (
            select(func.max(ModuleVersion.version_number))
            .where(ModuleVersion.module_id == module_id)
        )
        result = await self._session.execute(stmt)
        current_max: int | None = result.scalar_one_or_none()
        return (current_max or 0) + 1

    # ── Draft creation ────────────────────────────────────────────────────────

    async def create_new_version(
        self,
        module_id: UUID,
        framework_name: str,
        intake_schema: list,
        scoring_rubric: dict,
    ) -> ModuleVersion:
        """
        Create a new draft ModuleVersion for a module.

        The version_number is computed as MAX(version_number) + 1
        within the same transaction to ensure monotonicity.

        The new version is always created with:
          is_current = False
          published_at = None

        Use set_current_version() to promote this draft to the active
        version after its child rows (steps, templates, personas, rubric)
        have been created.

        Raises DuplicateError on version_number collision (should not
        occur in normal operation — indicates a race condition).

        Transaction note: no rollback here — session owner handles it.
        """
        version_number = await self.next_version_number(module_id)

        try:
            version = ModuleVersion(
                module_id=module_id,
                version_number=version_number,
                framework_name=framework_name,
                intake_schema=intake_schema,
                scoring_rubric=scoring_rubric,
                is_current=False,
            )
            self._session.add(version)
            await self._session.flush()
            await self._session.refresh(version)
            return version
        except IntegrityError as exc:
            raise DuplicateError(
                entity="ModuleVersion",
                field="(module_id, version_number)",
                value=f"{module_id}:{version_number}",
            ) from exc

    # ── Atomic current version promotion ──────────────────────────────────────

    async def set_current_version(
        self,
        version_id: UUID,
        *,
        published_by: UUID,
        expected_version: int,
    ) -> ModuleVersion:
        """
        Atomically promote a draft version to the active (is_current) version.

        Executes two UPDATE statements within the caller's transaction:

          Step 1 — Clear previous current version:
            UPDATE module_versions
            SET is_current = false
            WHERE module_id = (SELECT module_id FROM module_versions WHERE id = :vid)
              AND is_current = true
              AND id != :vid

          Step 2 — Set new current version:
            UPDATE module_versions
            SET is_current = true,
                published_at = now(),
                published_by = :uid,
                version = version + 1
            WHERE id = :vid
              AND version = :expected_version
            RETURNING *

        The partial unique index uq_module_one_current_version enforces
        that only one version per module can have is_current=True at the
        DB level, providing a safety net if Step 1 somehow fails.

        The version column bump on Step 2 ensures concurrent publish
        attempts on the same draft are serialised — only the first one
        wins; subsequent attempts get OptimisticLockError.

        Raises:
            NotFoundError        — version_id does not exist
            OptimisticLockError  — version mismatch (concurrent publish)
            ConflictError        — version is already current
        """
        # Fetch the version to get its module_id
        version = await self.get(version_id)
        if version is None:
            raise NotFoundError("ModuleVersion", version_id)

        if version.is_current:
            raise ConflictError(
                f"ModuleVersion '{version_id}' is already the current version."
            )

        module_id = version.module_id

        # Step 1: Clear current flag on whichever version currently holds it
        clear_stmt = (
            update(ModuleVersion)
            .where(ModuleVersion.module_id == module_id)
            .where(ModuleVersion.is_current.is_(True))
            .where(ModuleVersion.id != version_id)
            .values(is_current=False)
        )
        await self._session.execute(clear_stmt)

        # Step 2: Promote the target version — version-gated
        now = datetime.now(timezone.utc)
        promote_stmt = (
            update(ModuleVersion)
            .where(ModuleVersion.id == version_id)
            .where(ModuleVersion.version == expected_version)
            .values(
                is_current=True,
                published_at=now,
                published_by=published_by,
                version=ModuleVersion.version + 1,
            )
            .returning(ModuleVersion)
        )
        result = await self._session.execute(promote_stmt)
        promoted = result.scalar_one_or_none()

        if promoted is None:
            # Version mismatch — a concurrent publish beat us
            raise OptimisticLockError(
                "ModuleVersion", version_id, expected_version
            )

        return promoted

    async def clear_current_flag(self, module_id: UUID) -> int:
        """
        Clear is_current on all versions for a module.

        Used during module archiving or emergency reset.
        Returns the count of rows updated.

        Normally called by the service layer before set_current_version()
        when replacing the current version with an older one.
        """
        stmt = (
            update(ModuleVersion)
            .where(ModuleVersion.module_id == module_id)
            .where(ModuleVersion.is_current.is_(True))
            .values(is_current=False)
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    # ── Override create for descriptive DuplicateError ───────────────────────

    async def create(  # type: ignore[override]
        self, data: ModuleVersionCreate
    ) -> ModuleVersion:
        """
        Insert a new ModuleVersion from a data carrier.

        Maps the uq_module_version_number IntegrityError to DuplicateError.
        Prefer create_new_version() which auto-assigns version_number.

        Transaction note: no rollback here — session owner handles it.
        """
        try:
            version = ModuleVersion(
                module_id=data.module_id,
                version_number=data.version_number,
                framework_name=data.framework_name,
                intake_schema=data.intake_schema,
                scoring_rubric=data.scoring_rubric,
                is_current=False,
            )
            self._session.add(version)
            await self._session.flush()
            await self._session.refresh(version)
            return version
        except IntegrityError as exc:
            raise DuplicateError(
                entity="ModuleVersion",
                field="(module_id, version_number)",
                value=f"{data.module_id}:{data.version_number}",
            ) from exc
