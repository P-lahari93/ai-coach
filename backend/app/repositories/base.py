"""
Generic async base repository.

Architecture decisions:
────────────────────────
  ModelT   — the SQLAlchemy ORM model class (must inherit Base)
  CreateT  — Pydantic schema whose model_dump() maps to column names
  UpdateT  — Pydantic schema with all Optional fields for partial updates

  Page[T] — typed pagination envelope returned by list_paginated().
  All list methods return Page[T] rather than bare lists so callers
  always have total-count context for UI pagination.

  Soft-delete contract:
    - Models with a `deleted_at` column are treated as soft-deletable.
    - By default, all reads filter WHERE deleted_at IS NULL.
    - Pass include_deleted=True only in admin/audit contexts.
    - The repository checks for the attribute at runtime rather than
      requiring a mixin type parameter; this keeps the generic
      signature simple.

  Optimistic locking contract:
    - Models with a `version` column support OCC (OptimisticLockMixin).
    - update() and soft_delete() accept an optional expected_version.
    - When expected_version is provided:
        WHERE id = :id AND version = :expected_version
      If rowcount == 0, OptimisticLockError is raised.
    - When expected_version is None, the version check is skipped
      (used for non-concurrent operations like admin patches).

  Transaction contract:
    - NEVER call session.commit() or session.rollback() here.
    - The session is owned by the caller (get_db FastAPI dependency).
    - session.flush() may be called to materialise PKs / FK checks
      within the same transaction, but the caller must commit.

  Tenant filtering:
    - list_paginated() accepts an optional tenant_id parameter.
    - When provided, adds AND tenant_id = :tid to the WHERE clause.
    - This is belt-and-suspenders over the DB-level RLS policies.
    - Models without a tenant_id column silently ignore this filter
      (checked via hasattr at runtime).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base
from app.repositories.exceptions import (
    DuplicateError,
    NotFoundError,
    OptimisticLockError,
)

# ── Generic type variables ────────────────────────────────────────────────────

ModelT = TypeVar("ModelT", bound=Base)
CreateT = TypeVar("CreateT")
UpdateT = TypeVar("UpdateT")


# ── Module-level helpers ──────────────────────────────────────────────────────

def _dump(data: Any, *, exclude_unset: bool = False) -> dict[str, Any]:
    """
    Extract a plain dict from a Pydantic v2 schema (model_dump) or
    a Pydantic v1 / dataclass (dict).

    Single canonical conversion used by create() and update() so the
    compat shim is maintained in exactly one place.
    """
    if hasattr(data, "model_dump"):
        return data.model_dump(exclude_unset=exclude_unset)  # Pydantic v2
    return data.dict(exclude_unset=exclude_unset)  # Pydantic v1 fallback


# ── Pagination container ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Page(Generic[ModelT]):
    """
    Typed pagination envelope.

    Attributes:
        items:     the rows for the current page
        total:     total rows matching the filter (pre-pagination)
        page:      current page number (1-based)
        page_size: rows requested per page
        pages:     total number of pages (ceil(total / page_size))
        has_next:  True if there is a page after this one
        has_prev:  True if there is a page before this one (page > 1)
    """

    items: list[ModelT]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        if self.page_size == 0:
            return 0
        return max(1, math.ceil(self.total / self.page_size))

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1


# ── Base repository ───────────────────────────────────────────────────────────

class BaseRepository(Generic[ModelT, CreateT, UpdateT]):
    """
    Async SQLAlchemy 2.0 base repository.

    Subclasses must set the class-level attribute:
        model: type[ModelT]

    Example:
        class UserRepository(BaseRepository[User, UserCreate, UserUpdate]):
            model = User

    All public methods are async and return typed values.
    None is returned (not raised) when a row does not exist;
    use get_or_raise() when existence is required.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _soft_delete_filter(self, stmt: Any, *, include_deleted: bool) -> Any:
        """
        Append WHERE deleted_at IS NULL when the model supports soft-delete
        and include_deleted is False.
        """
        if not include_deleted and hasattr(self.model, "deleted_at"):
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    def _tenant_filter(self, stmt: Any, tenant_id: UUID | None) -> Any:
        """
        Append WHERE tenant_id = :tid when tenant_id is provided and
        the model has a tenant_id column.
        """
        if tenant_id is not None and hasattr(self.model, "tenant_id"):
            stmt = stmt.where(self.model.tenant_id == tenant_id)  # type: ignore[attr-defined]
        return stmt

    def _version_check(self, stmt: Any, expected_version: int | None) -> Any:
        """
        Append AND version = :v when expected_version is provided and
        the model has a version column (OptimisticLockMixin).
        """
        if expected_version is not None and hasattr(self.model, "version"):
            stmt = stmt.where(self.model.version == expected_version)  # type: ignore[attr-defined]
        return stmt

    def _offset(self, page: int, page_size: int) -> int:
        """Convert 1-based page + page_size to SQL OFFSET."""
        return max(0, (page - 1)) * page_size

    async def _raise_update_failure(
        self,
        id: UUID,
        expected_version: int | None,
    ) -> None:
        """
        Called after a version-gated UPDATE returns rowcount == 0.

        Issues a single SELECT to disambiguate between three failure modes:
          1. Row does not exist at all        → NotFoundError
          2. Row exists but is soft-deleted   → NotFoundError
          3. Version mismatch (OCC conflict)  → OptimisticLockError

        Always raises — never returns.

        Usage (subclasses):
            result = await self._session.execute(update_stmt)
            if result.rowcount == 0:
                await self._raise_update_failure(id, expected_version)
        """
        exists = await self.exists(id, include_deleted=True)
        if not exists:
            raise NotFoundError(self.model.__name__, id)
        if expected_version is not None and hasattr(self.model, "version"):
            raise OptimisticLockError(self.model.__name__, id, expected_version)
        # Row exists and is soft-deleted (deleted_at IS NOT NULL)
        raise NotFoundError(self.model.__name__, id)

    # ── Core CRUD ─────────────────────────────────────────────────────────────

    async def get(
        self,
        id: UUID,
        *,
        include_deleted: bool = False,
    ) -> ModelT | None:
        """
        Fetch a single row by primary key.

        Returns None if the row does not exist or has been soft-deleted
        (unless include_deleted=True).
        """
        stmt = select(self.model).where(self.model.id == id)  # type: ignore[attr-defined]
        stmt = self._soft_delete_filter(stmt, include_deleted=include_deleted)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_raise(
        self,
        id: UUID,
        *,
        include_deleted: bool = False,
    ) -> ModelT:
        """
        Fetch a single row by primary key.

        Raises NotFoundError if the row does not exist or has been
        soft-deleted (unless include_deleted=True).
        """
        obj = await self.get(id, include_deleted=include_deleted)
        if obj is None:
            raise NotFoundError(self.model.__name__, id)
        return obj

    async def create(self, data: CreateT) -> ModelT:
        """
        Insert a new row from a Pydantic schema.

        model_dump() is used to extract field values. exclude_unset=False
        so that server_default columns receive their Python-side defaults.

        Raises DuplicateError if a unique constraint is violated.

        Transaction note: IntegrityError is caught and re-raised as
        DuplicateError. No rollback is performed here — the session
        owner (get_db) handles rollback on exception.
        """
        try:
            payload: dict[str, Any] = _dump(data, exclude_unset=False)
            obj = self.model(**payload)
            self._session.add(obj)
            await self._session.flush()   # materialise PK / catch constraints
            await self._session.refresh(obj)
            return obj
        except IntegrityError as exc:
            raise DuplicateError(
                entity=self.model.__name__,
                field="unknown",
                value="duplicate",
            ) from exc

    async def update(
        self,
        id: UUID,
        data: UpdateT,
        *,
        expected_version: int | None = None,
    ) -> ModelT:
        """
        Apply a partial update from a Pydantic schema.

        Only fields explicitly set in the schema (exclude_unset=True) are
        written, so passing UserUpdate(full_name="Alice") will not
        overwrite email or any other unchanged field.

        If expected_version is provided and the model has a version column,
        the UPDATE adds AND version = :v and increments version + 1.
        Raises OptimisticLockError when rowcount == 0.

        Raises NotFoundError if the row does not exist.
        """
        payload: dict[str, Any] = _dump(data, exclude_unset=True)
        if not payload:
            # Nothing to write — return the current state
            return await self.get_or_raise(id)

        # Bump version when model supports optimistic locking
        if hasattr(self.model, "version") and expected_version is not None:
            payload["version"] = expected_version + 1

        stmt = (
            update(self.model)
            .where(self.model.id == id)  # type: ignore[attr-defined]
            .values(**payload)
            .returning(self.model)
        )
        stmt = self._version_check(stmt, expected_version)

        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            if expected_version is not None and hasattr(self.model, "version"):
                raise OptimisticLockError(
                    entity=self.model.__name__,
                    id=id,
                    expected=expected_version,
                )
            raise NotFoundError(self.model.__name__, id)

        return row

    async def soft_delete(
        self,
        id: UUID,
        *,
        expected_version: int | None = None,
    ) -> bool:
        """
        Set deleted_at = now() on the row.

        Returns True when the row was found and marked deleted.
        Raises NotFoundError when the row does not exist or is already
        soft-deleted.
        Raises OptimisticLockError on version mismatch.

        Only valid for models with a deleted_at column.

        Transaction note: no rollback here — the session owner handles it.
        """
        if not hasattr(self.model, "deleted_at"):
            raise TypeError(
                f"{self.model.__name__} does not support soft-delete "
                f"(no deleted_at column)."
            )

        now = datetime.now(timezone.utc)
        values: dict[str, Any] = {"deleted_at": now}

        if hasattr(self.model, "version") and expected_version is not None:
            values["version"] = expected_version + 1

        stmt = (
            update(self.model)
            .where(self.model.id == id)  # type: ignore[attr-defined]
            .where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
            .values(**values)
        )
        stmt = self._version_check(stmt, expected_version)

        result = await self._session.execute(stmt)

        if result.rowcount == 0:
            await self._raise_update_failure(id, expected_version)

        return True

    async def hard_delete(self, id: UUID) -> bool:
        """
        Physically delete the row from the database.

        Returns True when the row was found and deleted.
        Returns False when the row does not exist.

        Reserved for admin operations and test teardown.
        Service layer must explicitly choose this over soft_delete().
        """
        stmt = sa_delete(self.model).where(  # type: ignore[attr-defined]
            self.model.id == id  # type: ignore[attr-defined]
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    # ── Existence check ───────────────────────────────────────────────────────

    async def exists(
        self,
        id: UUID,
        *,
        include_deleted: bool = False,
    ) -> bool:
        """
        Return True if a row with this id exists (and is not soft-deleted
        unless include_deleted=True).

        Uses SELECT 1 for efficiency — no row data transferred.
        """
        stmt = (
            select(literal(1))
            .select_from(self.model)
            .where(self.model.id == id)  # type: ignore[attr-defined]
            .limit(1)
        )
        stmt = self._soft_delete_filter(stmt, include_deleted=include_deleted)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ── Pagination ────────────────────────────────────────────────────────────

    async def list_paginated(
        self,
        *,
        tenant_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
        include_deleted: bool = False,
    ) -> "Page[ModelT]":
        """
        Return a paginated slice of rows.

        Applies soft-delete filter and optional tenant_id filter.
        Orders by created_at DESC when the model has that column,
        otherwise by id for stable ordering.

        Parameters:
            tenant_id:       restrict to a specific tenant (optional)
            page:            1-based page number
            page_size:       rows per page (max enforced by callers)
            include_deleted: include soft-deleted rows when True
        """
        # Count query
        count_stmt = select(func.count()).select_from(self.model)
        count_stmt = self._soft_delete_filter(
            count_stmt, include_deleted=include_deleted
        )
        count_stmt = self._tenant_filter(count_stmt, tenant_id)
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        # Data query
        data_stmt = select(self.model)
        data_stmt = self._soft_delete_filter(
            data_stmt, include_deleted=include_deleted
        )
        data_stmt = self._tenant_filter(data_stmt, tenant_id)

        # Stable ordering
        if hasattr(self.model, "created_at"):
            data_stmt = data_stmt.order_by(
                self.model.created_at.desc()  # type: ignore[attr-defined]
            )
        else:
            data_stmt = data_stmt.order_by(
                self.model.id.asc()  # type: ignore[attr-defined]
            )

        data_stmt = data_stmt.offset(self._offset(page, page_size)).limit(
            page_size
        )

        result = await self._session.execute(data_stmt)
        items = list(result.scalars().all())

        return Page(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
