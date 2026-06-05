"""
PermissionRepository — async SQLAlchemy 2.0 implementation.

Covers:
  Permission lookup by id, by resource+action, and by resource
  Full table load (small reference table, suitable for full load)
  Permission existence check by resource+action pair
  Bulk upsert for seeding/migration scenarios

Model characteristics:
  Permission has NO soft-delete (no deleted_at column).
  Permission has NO optimistic lock (no version column).
  Permission has NO tenant_id (platform-wide reference data).
  Hard-delete is the only deletion path; service layer guards this.

Transaction contract:
  No commit() or rollback() calls here.
  flush() is used after INSERT to materialise the PK.
  The session owner (get_db) handles commit/rollback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Permission
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import DuplicateError, NotFoundError


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class PermissionCreate:
    """Data required to create a new Permission row."""

    resource: str
    action: str
    description: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "resource": self.resource,
            "action": self.action,
            "description": self.description,
        }


@dataclass
class PermissionUpdate:
    """Partial update for a Permission (description only)."""

    description: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result = {}
        if self.description is not None:
            result["description"] = self.description
        return result


# ── Repository ────────────────────────────────────────────────────────────────

class PermissionRepository(
    BaseRepository[Permission, PermissionCreate, PermissionUpdate]
):
    """
    All database operations for the Permission reference table.

    Permissions are platform-wide reference data: small, stable, and
    loaded in full for RBAC checks. The table contains ~30 rows at launch.

    BaseRepository provides: get, get_or_raise, create, update,
    hard_delete, exists, list_paginated.

    This repository extends with:
      - Lookup by (resource, action) pair
      - Lookup by resource (returns all actions for that resource)
      - Full table load (sorted, for admin UIs)
      - Existence check by resource+action without loading the row
      - Idempotent seed upsert for deployment automation
    """

    model = Permission

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Lookups ───────────────────────────────────────────────────────────────

    async def get_by_resource_action(
        self,
        resource: str,
        action: str,
    ) -> Permission | None:
        """
        Fetch a Permission by its unique (resource, action) pair.

        Uses the uq_permissions_resource_action index for an O(1) lookup.
        Returns None when not found.
        """
        stmt = select(Permission).where(
            Permission.resource == resource,
            Permission.action == action,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_resource_action_or_raise(
        self,
        resource: str,
        action: str,
    ) -> Permission:
        """
        Fetch a Permission by its (resource, action) pair.

        Raises NotFoundError when not found.
        """
        perm = await self.get_by_resource_action(resource, action)
        if perm is None:
            raise NotFoundError("Permission", f"{resource}:{action}")
        return perm

    async def exists_by_resource_action(
        self,
        resource: str,
        action: str,
    ) -> bool:
        """
        Return True if a permission with the given (resource, action) exists.

        Uses SELECT 1 — no row data transferred.
        """
        from sqlalchemy import literal

        stmt = (
            select(literal(1))
            .select_from(Permission)
            .where(
                Permission.resource == resource,
                Permission.action == action,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ── Resource-scoped listing ───────────────────────────────────────────────

    async def list_by_resource(self, resource: str) -> list[Permission]:
        """
        Return all permissions for a given resource, ordered by action.

        Example:
            list_by_resource("module")
            → [module:archive, module:create, module:delete,
               module:publish, module:read, module:update]
        """
        stmt = (
            select(Permission)
            .where(Permission.resource == resource)
            .order_by(Permission.action.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_resources(self) -> list[str]:
        """
        Return the distinct set of resource names, sorted alphabetically.

        Used to build resource group headings in the admin permission editor.
        """
        stmt = (
            select(Permission.resource)
            .distinct()
            .order_by(Permission.resource.asc())
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    # ── Full table load ───────────────────────────────────────────────────────

    async def list_all(self) -> list[Permission]:
        """
        Return all permissions ordered by (resource, action).

        Permissions are a small, stable reference table (~30 rows).
        Full load is acceptable and is the standard pattern for
        populating admin permission pickers and RBAC bootstrap.
        """
        stmt = select(Permission).order_by(
            Permission.resource.asc(),
            Permission.action.asc(),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self) -> int:
        """Return the total number of permission rows in the table."""
        stmt = select(func.count()).select_from(Permission)
        return (await self._session.execute(stmt)).scalar_one()

    # ── Override create for descriptive DuplicateError ───────────────────────

    async def create(  # type: ignore[override]
        self, data: PermissionCreate
    ) -> Permission:
        """
        Insert a new Permission.

        Maps the uq_permissions_resource_action IntegrityError to
        a descriptive DuplicateError(field='resource:action').

        Transaction note: no rollback here — session owner handles it.
        """
        try:
            perm = Permission(
                resource=data.resource,
                action=data.action,
                description=data.description,
            )
            self._session.add(perm)
            await self._session.flush()
            await self._session.refresh(perm)
            return perm
        except IntegrityError as exc:
            raise DuplicateError(
                entity="Permission",
                field="resource:action",
                value=f"{data.resource}:{data.action}",
            ) from exc

    # ── Seed upsert ───────────────────────────────────────────────────────────

    async def upsert_many(
        self,
        permissions: list[PermissionCreate],
    ) -> int:
        """
        Idempotent bulk upsert for deployment seeds and migrations.

        Uses INSERT ... ON CONFLICT (resource, action) DO UPDATE SET
        description = EXCLUDED.description so that description text can
        be updated on re-seed without duplicating rows.

        Returns the number of rows inserted or updated.
        Safe to call multiple times (idempotent).

        Transaction note: no rollback here — session owner handles it.
        """
        if not permissions:
            return 0

        rows = [
            {
                "resource": p.resource,
                "action": p.action,
                "description": p.description,
            }
            for p in permissions
        ]

        stmt = (
            pg_insert(Permission)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["resource", "action"],
                set_={"description": pg_insert(Permission).excluded.description},
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount
