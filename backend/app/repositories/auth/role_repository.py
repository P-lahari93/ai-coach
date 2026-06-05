"""
RoleRepository — async SQLAlchemy 2.0 implementation.

Covers:
  Role CRUD (no soft-delete — Role has no deleted_at column)
  Permission listing and role-permission management
  UserRole assignment and revocation with NULL-safe tenant scoping
  User permission resolution (flat set for RBAC checks)

Transaction contract:
  No commit() or rollback() calls here.

Soft-delete:
  Role, Permission, and RolePermission do NOT have deleted_at columns.
  Hard-delete is the only deletion mechanism.
  System roles (is_system=True) must NOT be deleted — this invariant
  is enforced by the service layer, not the repository.

Optimistic locking:
  Role has a version column. update() forwards expected_version.
  RolePermission and UserRole have no version column.

Tenant filtering:
  UserRole has a nullable tenant_id.
  get_user_roles() filters by (tenant_id = :tid OR tenant_id IS NULL)
  to return both tenant-scoped and global role assignments.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import (
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import (
    DuplicateError,
    NotFoundError,
)


# ── Pydantic-compatible data carriers ────────────────────────────────────────

@dataclass
class RoleCreate:
    """Data required to create a new Role."""

    name: str
    scope: str = "tenant"          # global | tenant
    description: Optional[str] = None
    is_system: bool = False

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "name": self.name,
            "scope": self.scope,
            "description": self.description,
            "is_system": self.is_system,
        }


@dataclass
class RoleUpdate:
    """Partial update for a Role."""

    name: Optional[str] = None
    description: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result = {}
        if self.name is not None:
            result["name"] = self.name
        if self.description is not None:
            result["description"] = self.description
        return result


# ── Repository ────────────────────────────────────────────────────────────────


class RoleRepository(BaseRepository[Role, RoleCreate, RoleUpdate]):
    """
    All database operations for Role, Permission, and role assignment.

    BaseRepository provides: get, get_or_raise, create, update,
    hard_delete, exists, list_paginated.

    This repository extends with:
      - Role lookup by name
      - Role+permissions eager load
      - Permission listing (by resource, all)
      - role_permissions management
      - UserRole assignment and revocation
      - User permission resolution (flat set)
    """

    model = Role

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Role lookups ──────────────────────────────────────────────────────────

    async def get_by_name(self, name: str) -> Role | None:
        """
        Fetch a role by its unique name.

        Returns None when not found.
        """
        stmt = select(Role).where(Role.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name_or_raise(self, name: str) -> Role:
        """
        Fetch a role by name.

        Raises NotFoundError when not found.
        """
        role = await self.get_by_name(name)
        if role is None:
            raise NotFoundError("Role", name)
        return role

    async def get_with_permissions(self, role_id: UUID) -> Role | None:
        """
        Load a Role with its permissions eagerly loaded.

        Used by the RBAC check pipeline so that permission decisions
        are made without additional round-trips.
        """
        stmt = (
            select(Role)
            .where(Role.id == role_id)
            .options(selectinload(Role.permissions))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_system_roles(self) -> list[Role]:
        """
        Return all system roles (is_system=True), ordered by name.

        System roles are seeded at platform launch and cannot be deleted.
        """
        stmt = (
            select(Role)
            .where(Role.is_system.is_(True))
            .order_by(Role.name.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_scope(
        self,
        scope: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> Page[Role]:
        """
        Return roles filtered by scope ('global' | 'tenant').

        Returns a Page[Role] for consistency even though the result set
        is typically small (suitable for dropdown UIs).
        """
        count_stmt = (
            select(func.count())
            .select_from(Role)
            .where(Role.scope == scope)
        )
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(Role)
            .where(Role.scope == scope)
            .order_by(Role.name.asc())
            .offset(self._offset(page, page_size))
            .limit(page_size)
        )
        result = await self._session.execute(data_stmt)
        items = list(result.scalars().all())

        return Page(items=items, total=total, page=page, page_size=page_size)

    # ── Permission queries ────────────────────────────────────────────────────

    async def get_permission(self, permission_id: UUID) -> Permission | None:
        """Fetch a single Permission by id."""
        stmt = select(Permission).where(Permission.id == permission_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_permission_by_resource_action(
        self,
        resource: str,
        action: str,
    ) -> Permission | None:
        """
        Fetch a Permission by its (resource, action) pair.

        Uses the uq_permissions_resource_action unique index.
        """
        stmt = select(Permission).where(
            Permission.resource == resource,
            Permission.action == action,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_permissions_by_resource(
        self, resource: str
    ) -> list[Permission]:
        """
        Return all permissions for a given resource, ordered by action.

        e.g. list_permissions_by_resource("module") →
             [module:archive, module:create, module:delete, ...]
        """
        stmt = (
            select(Permission)
            .where(Permission.resource == resource)
            .order_by(Permission.action.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_permissions(self) -> list[Permission]:
        """
        Return all permissions ordered by (resource, action).

        Permissions are a small, stable reference table.
        Full load is acceptable and used to populate admin permission pickers.
        """
        stmt = select(Permission).order_by(
            Permission.resource.asc(),
            Permission.action.asc(),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Role-permission management ────────────────────────────────────────────

    async def assign_permission(
        self,
        role_id: UUID,
        permission_id: UUID,
    ) -> RolePermission:
        """
        Grant a permission to a role.

        Idempotent: if the assignment already exists, returns the
        existing row without raising an error. Uses INSERT ... ON CONFLICT
        DO NOTHING, then SELECTs the row.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(RolePermission)
            .values(role_id=role_id, permission_id=permission_id)
            .on_conflict_do_nothing(
                index_elements=["role_id", "permission_id"]
            )
        )
        await self._session.execute(stmt)

        # Fetch the row (exists whether we just inserted or it was already there)
        fetch_stmt = select(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
        )
        result = await self._session.execute(fetch_stmt)
        row = result.scalar_one_or_none()

        if row is None:
            # Should not happen — INSERT OR SELECT should always yield a row
            raise NotFoundError("RolePermission", f"{role_id}:{permission_id}")

        return row

    async def revoke_permission(
        self,
        role_id: UUID,
        permission_id: UUID,
    ) -> bool:
        """
        Remove a permission from a role.

        Returns True when the assignment existed and was removed.
        Returns False when no such assignment existed.
        """
        stmt = sa_delete(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    # ── UserRole assignment ───────────────────────────────────────────────────

    async def get_user_roles(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> list[UserRole]:
        """
        Return UserRole assignments for a user in a given tenant scope.

        When tenant_id is provided, returns:
          - Roles assigned globally (tenant_id IS NULL)
          - Roles assigned to the specific tenant (tenant_id = :tid)

        When tenant_id is None, returns all UserRole rows for the user
        regardless of tenant scope (used for admin account review).

        Eager-loads the Role + permissions for each UserRole to allow
        immediate RBAC checks without follow-up queries.
        """
        stmt = (
            select(UserRole)
            .where(UserRole.user_id == user_id)
            .options(
                selectinload(UserRole.role).selectinload(Role.permissions)
            )
        )

        if tenant_id is not None:
            stmt = stmt.where(
                (UserRole.tenant_id == tenant_id)
                | UserRole.tenant_id.is_(None)
            )

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def assign_role_to_user(
        self,
        user_id: UUID,
        role_id: UUID,
        *,
        tenant_id: UUID | None = None,
        granted_by: UUID | None = None,
    ) -> UserRole:
        """
        Assign a role to a user, optionally scoped to a tenant.

        tenant_id=None means a global role assignment (e.g. superadmin).

        The NULL-safe uniqueness is enforced by two partial indexes in
        the migration:
          uq_user_roles_global : UNIQUE (user_id, role_id) WHERE tenant_id IS NULL
          uq_user_roles_tenant : UNIQUE (user_id, role_id, tenant_id) WHERE tenant_id IS NOT NULL

        Raises DuplicateError if the assignment already exists.
        """
        try:
            user_role = UserRole(
                user_id=user_id,
                role_id=role_id,
                tenant_id=tenant_id,
                granted_by=granted_by,
            )
            self._session.add(user_role)
            await self._session.flush()
            await self._session.refresh(user_role)
            return user_role
        except IntegrityError as exc:
            raise DuplicateError(
                entity="UserRole",
                field="(user_id, role_id, tenant_id)",
                value=f"{user_id},{role_id},{tenant_id}",
            ) from exc

    async def revoke_role_from_user(
        self,
        user_id: UUID,
        role_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> bool:
        """
        Revoke a role from a user in a specific tenant scope.

        When tenant_id is None, removes the global (tenant_id IS NULL)
        assignment only — does NOT revoke all tenant-scoped assignments.

        Returns True when an assignment was removed.
        Returns False when no matching assignment existed.
        """
        stmt = sa_delete(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_id == role_id,
        )
        if tenant_id is not None:
            stmt = stmt.where(UserRole.tenant_id == tenant_id)
        else:
            stmt = stmt.where(UserRole.tenant_id.is_(None))

        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def revoke_all_roles_from_user(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID,
    ) -> int:
        """
        Remove all role assignments for a user within a specific tenant.

        Used when removing a user from a tenant entirely.
        Returns the count of revoked assignments.
        """
        stmt = sa_delete(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    # ── Permission resolution ─────────────────────────────────────────────────

    async def get_permissions_for_user(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> set[str]:
        """
        Resolve a flat set of 'resource:action' permission strings for a user.

        Queries across the full join path:
          user_roles → roles → role_permissions → permissions

        When tenant_id is provided, includes permissions from:
          - Global roles (user_roles.tenant_id IS NULL)
          - Tenant-scoped roles (user_roles.tenant_id = :tid)

        Used by the RBAC middleware to build the permission set for
        the current request without loading full ORM objects.

        Returns an empty set when the user has no role assignments.
        """
        # Select distinct resource + action columns — DISTINCT prevents duplicate
        # rows when a user holds the same permission via multiple roles.
        stmt = (
            select(Permission.resource, Permission.action)
            .distinct()
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )

        if tenant_id is not None:
            stmt = stmt.where(
                (UserRole.tenant_id == tenant_id)
                | UserRole.tenant_id.is_(None)
            )

        result = await self._session.execute(stmt)
        rows = result.all()

        return {f"{row.resource}:{row.action}" for row in rows}

    # ── Role creation with permission bootstrap ───────────────────────────────

    async def create_with_permissions(
        self,
        data: RoleCreate,
        permission_ids: list[UUID],
    ) -> Role:
        """
        Create a new role and assign a list of permissions atomically.

        Both the INSERT into roles and the INSERTs into role_permissions
        happen within the caller's transaction. If the role name is
        already taken, DuplicateError is raised and no permissions are
        written.

        Used by the tenant_admin flow when creating custom roles.
        """
        try:
            role = Role(
                name=data.name,
                scope=data.scope,
                description=data.description,
                is_system=data.is_system,
            )
            self._session.add(role)
            await self._session.flush()  # materialise role.id

            for perm_id in permission_ids:
                self._session.add(
                    RolePermission(
                        role_id=role.id,
                        permission_id=perm_id,
                    )
                )

            await self._session.flush()
            await self._session.refresh(role)
            return role

        except IntegrityError as exc:
            raise DuplicateError(
                entity="Role",
                field="name",
                value=data.name,
            ) from exc
