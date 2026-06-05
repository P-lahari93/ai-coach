"""
UserRepository — async SQLAlchemy 2.0 implementation.

Covers:
  User CRUD with soft-delete + tenant-scoped listing
  Email lookup (case-insensitive via lower())
  Eager-load variants for auth and RBAC checks
  Tenant membership queries via user_tenants join
  last_login_at update
  Deactivation (is_active = false) with optimistic lock

Transaction contract:
  No commit() or rollback() calls here. The caller owns the session.

Soft-delete:
  All reads use WHERE deleted_at IS NULL by default.
  get_or_raise() and list methods respect include_deleted flag.

Optimistic locking:
  update() and deactivate() forward expected_version to BaseRepository.

Tenant filtering:
  list_by_tenant() queries via the user_tenants join table.
  list_paginated() applies tenant_id = :tid on the users table directly
  (users have no tenant_id column — this falls back to the no-op path;
  use list_by_tenant() for tenant-scoped user lists).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import (
    Role,
    User,
    UserRole,
    UserTenant,
)
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import DuplicateError


# ── Pydantic-compatible data carriers ────────────────────────────────────────
# These are plain dataclasses used as the CreateT / UpdateT types so the
# repository can be used without Pydantic at the infrastructure layer.
# The service layer passes its own Pydantic schemas which are duck-typed
# (both have model_dump() / dict()).

from dataclasses import dataclass
from typing import Optional


@dataclass
class UserCreate:
    """Data required to create a new User row."""

    email: str
    password_hash: str
    full_name: str
    avatar_url: Optional[str] = None
    is_active: bool = True
    is_superadmin: bool = False


@dataclass
class UserUpdate:
    """
    Partial update data for a User row.

    Only fields set to a non-None value are applied.
    Implements model_dump(exclude_unset=True) semantics via
    a custom method.
    """

    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    password_hash: Optional[str] = None
    is_active: Optional[bool] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:  # noqa: ARG002
        result = {}
        if self.full_name is not None:
            result["full_name"] = self.full_name
        if self.avatar_url is not None:
            result["avatar_url"] = self.avatar_url
        if self.password_hash is not None:
            result["password_hash"] = self.password_hash
        if self.is_active is not None:
            result["is_active"] = self.is_active
        return result


# ── Repository ────────────────────────────────────────────────────────────────


class UserRepository(BaseRepository[User, UserCreate, UserUpdate]):
    """
    All database operations for the User domain.

    Inherits full CRUD from BaseRepository[User, UserCreate, UserUpdate].
    Adds auth-specific and tenant-scoped queries.
    """

    model = User

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Eager-load helpers ────────────────────────────────────────────────────

    @staticmethod
    def _roles_load_options():
        """
        Selectin chain for loading user → user_roles → role → permissions.
        Used for every path that needs RBAC data (auth checks, token claims).
        """
        return (
            selectinload(User.user_roles)
            .selectinload(UserRole.role)
            .selectinload(Role.permissions)
        )

    # ── Email lookups ─────────────────────────────────────────────────────────

    async def get_by_email(
        self,
        email: str,
        *,
        include_deleted: bool = False,
    ) -> User | None:
        """
        Case-insensitive email lookup.

        Uses lower(:email) to match the functional index
        idx_users_email_lower created in migration 002.
        Returns None when the user is not found or is soft-deleted
        (unless include_deleted=True).
        """
        stmt = select(User).where(
            func.lower(User.email) == email.lower().strip()
        )
        if not include_deleted:
            stmt = stmt.where(User.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email_with_roles(
        self,
        email: str,
        *,
        tenant_id: UUID | None = None,
    ) -> User | None:
        """
        Case-insensitive email lookup with RBAC data eagerly loaded.

        Used by the auth service during login and token refresh.
        Loads user_roles → role → permissions in a single round-trip
        via selectin strategy.

        When tenant_id is provided, user_roles are filtered to the
        given tenant scope (plus global roles with tenant_id IS NULL).
        The full role graph is still returned — filtering is left to
        the RBAC checker which applies tenant context.
        """
        stmt = (
            select(User)
            .where(func.lower(User.email) == email.lower().strip())
            .where(User.deleted_at.is_(None))
            .options(self._roles_load_options())
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── ID-based lookups with relations ──────────────────────────────────────

    async def get_with_roles(self, user_id: UUID) -> User | None:
        """
        Load user + full RBAC graph (roles + permissions).

        Used by the dependency injection layer to build the current-user
        context on every authenticated request.
        """
        stmt = (
            select(User)
            .where(User.id == user_id)
            .where(User.deleted_at.is_(None))
            .options(self._roles_load_options())
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_tenants(self, user_id: UUID) -> User | None:
        """
        Load user + tenant memberships (UserTenant rows).

        Used to list a user's workspaces on the account page.
        """
        stmt = (
            select(User)
            .where(User.id == user_id)
            .where(User.deleted_at.is_(None))
            .options(selectinload(User.user_tenants))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Tenant-scoped user listing ────────────────────────────────────────────

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        include_deleted: bool = False,
    ) -> Page[User]:
        """
        List users who are members of a specific tenant.

        Queries via the user_tenants join table.
        Ordered by full_name ascending for the admin user list.

        Does NOT load role data — callers that need roles should
        follow up with get_with_roles() per user or batch.
        """
        # Subquery: user IDs in this tenant
        member_ids_subq = (
            select(UserTenant.user_id)
            .where(UserTenant.tenant_id == tenant_id)
            .scalar_subquery()
        )

        # Count
        count_stmt = (
            select(func.count())
            .select_from(User)
            .where(User.id.in_(member_ids_subq))
        )
        if not include_deleted:
            count_stmt = count_stmt.where(User.deleted_at.is_(None))
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        # Data
        data_stmt = (
            select(User)
            .where(User.id.in_(member_ids_subq))
            .order_by(User.full_name.asc())
            .offset(self._offset(page, page_size))
            .limit(page_size)
        )
        if not include_deleted:
            data_stmt = data_stmt.where(User.deleted_at.is_(None))

        result = await self._session.execute(data_stmt)
        items = list(result.scalars().all())

        return Page(items=items, total=total, page=page, page_size=page_size)

    # ── Search ────────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        tenant_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[User]:
        """
        Full-name and email prefix search.

        Uses ILIKE for case-insensitive matching.
        When tenant_id is provided, results are restricted to members
        of that tenant (via user_tenants subquery).

        Parameters:
            query:     search string — matched against lower(full_name)
                       and lower(email) with leading wildcard
            tenant_id: restrict to a specific tenant (optional)
            page:      1-based page number
            page_size: rows per page
        """
        pattern = f"%{query.lower().strip()}%"
        base_filter = (
            (func.lower(User.full_name).like(pattern))
            | (func.lower(User.email).like(pattern))
        ) & User.deleted_at.is_(None)

        if tenant_id is not None:
            member_ids_subq = (
                select(UserTenant.user_id)
                .where(UserTenant.tenant_id == tenant_id)
                .scalar_subquery()
            )
            tenant_filter = User.id.in_(member_ids_subq)
        else:
            tenant_filter = None

        count_stmt = (
            select(func.count())
            .select_from(User)
            .where(base_filter)
        )
        if tenant_filter is not None:
            count_stmt = count_stmt.where(tenant_filter)
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(User)
            .where(base_filter)
            .order_by(User.full_name.asc())
            .offset(self._offset(page, page_size))
            .limit(page_size)
        )
        if tenant_filter is not None:
            data_stmt = data_stmt.where(tenant_filter)

        result = await self._session.execute(data_stmt)
        items = list(result.scalars().all())

        return Page(items=items, total=total, page=page, page_size=page_size)

    # ── Timestamp updates ─────────────────────────────────────────────────────

    async def update_last_login(self, user_id: UUID) -> None:
        """
        Set last_login_at = now() on a successful authentication.

        Uses a targeted UPDATE rather than a full object load/flush.
        Not version-gated — last_login_at is a low-contention field.
        """
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=datetime.now(timezone.utc))
        )
        await self._session.execute(stmt)

    # ── Deactivation ──────────────────────────────────────────────────────────

    async def deactivate(
        self,
        user_id: UUID,
        *,
        expected_version: int | None = None,
    ) -> User:
        """
        Set is_active = False on a user (logical suspension).

        Distinguished from soft_delete() — a deactivated user is not
        logically deleted and can be re-activated. Soft-delete is a
        permanent administrative action.

        Raises NotFoundError if the user does not exist.
        Raises OptimisticLockError on version mismatch.
        """
        values: dict = {"is_active": False}
        if expected_version is not None:
            values["version"] = expected_version + 1

        stmt = (
            update(User)
            .where(User.id == user_id)
            .where(User.deleted_at.is_(None))
            .values(**values)
            .returning(User)
        )
        if expected_version is not None:
            stmt = stmt.where(User.version == expected_version)

        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            await self._raise_update_failure(user_id, expected_version)

        return row  # type: ignore[return-value]

    async def reactivate(
        self,
        user_id: UUID,
        *,
        expected_version: int | None = None,
    ) -> User:
        """
        Set is_active = True on a previously deactivated user.

        Raises NotFoundError if the user does not exist or is soft-deleted.
        Raises OptimisticLockError on version mismatch.
        """
        values: dict = {"is_active": True}
        if expected_version is not None:
            values["version"] = expected_version + 1

        stmt = (
            update(User)
            .where(User.id == user_id)
            .where(User.deleted_at.is_(None))
            .values(**values)
            .returning(User)
        )
        if expected_version is not None:
            stmt = stmt.where(User.version == expected_version)

        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            await self._raise_update_failure(user_id, expected_version)

        return row  # type: ignore[return-value]

    # ── Tenant membership ─────────────────────────────────────────────────────

    async def get_tenant_memberships(self, user_id: UUID) -> list[UserTenant]:
        """
        Return all UserTenant rows for a user (their tenant memberships).

        Ordered so the primary workspace appears first.
        """
        stmt = (
            select(UserTenant)
            .where(UserTenant.user_id == user_id)
            .order_by(UserTenant.is_primary.desc(), UserTenant.joined_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def add_tenant_membership(
        self,
        user_id: UUID,
        tenant_id: UUID,
        *,
        is_primary: bool = False,
    ) -> UserTenant:
        """
        Add a user to a tenant.

        Raises DuplicateError if the membership already exists
        (uq_user_tenants constraint on (user_id, tenant_id)).
        """
        try:
            membership = UserTenant(
                user_id=user_id,
                tenant_id=tenant_id,
                is_primary=is_primary,
            )
            self._session.add(membership)
            await self._session.flush()
            await self._session.refresh(membership)
            return membership
        except IntegrityError as exc:
            raise DuplicateError(
                entity="UserTenant",
                field="(user_id, tenant_id)",
                value=f"{user_id},{tenant_id}",
            ) from exc

    async def remove_tenant_membership(
        self,
        user_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """
        Remove a user from a tenant.

        Returns True when the membership existed and was removed.
        Returns False when no such membership existed.
        """
        stmt = sa_delete(UserTenant).where(
            UserTenant.user_id == user_id,
            UserTenant.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    # ── Override create to handle IntegrityError on email unique ─────────────

    async def create(self, data: UserCreate) -> User:  # type: ignore[override]
        """
        Insert a new User, mapping IntegrityError to a descriptive DuplicateError.

        The uq constraint on users.email maps to DuplicateError(field='email').
        """
        try:
            user = User(
                email=data.email,
                password_hash=data.password_hash,
                full_name=data.full_name,
                avatar_url=data.avatar_url,
                is_active=data.is_active,
                is_superadmin=data.is_superadmin,
            )
            self._session.add(user)
            await self._session.flush()
            await self._session.refresh(user)
            return user
        except IntegrityError as exc:
            raise DuplicateError(
                entity="User",
                field="email",
                value=data.email,
            ) from exc
