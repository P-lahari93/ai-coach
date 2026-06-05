# FILE: app/services/auth/user_service.py
"""
UserService — user profile management, listing, role assignment.

Separated from AuthService to isolate concerns:
  - AuthService handles authentication flows (login, logout, register)
  - UserService handles user CRUD and admin operations
"""
from __future__ import annotations

from uuid import UUID

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.database.unit_of_work import UnitOfWork
from app.models.user import User, UserRole
from app.repositories.auth.user_repository import UserUpdate
from app.repositories.base import Page


class UserService:
    """
    User profile management and admin operations.

    Each method opens its own UnitOfWork.
    """

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_user(self, user_id: UUID) -> User:
        """
        Fetch a user by id without roles loaded.

        Raises:
            NotFoundError — user not found or is soft-deleted
        """
        async with UnitOfWork() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFoundError("User", user_id)
            return user

    async def get_user_with_roles(self, user_id: UUID) -> User:
        """
        Fetch a user with roles + permissions eagerly loaded.

        Used by the current-user context on authenticated requests.

        Raises:
            NotFoundError — user not found or is soft-deleted
        """
        async with UnitOfWork() as uow:
            user = await uow.users.get_with_roles(user_id)
            if user is None:
                raise NotFoundError("User", user_id)
            return user

    # ── Profile update ────────────────────────────────────────────────────────

    async def update_profile(
        self,
        user_id: UUID,
        full_name: str | None = None,
        avatar_url: str | None = None,
    ) -> User:
        """
        Update a user's display profile fields.

        Returns the updated user.

        Raises:
            NotFoundError — user not found
        """
        async with UnitOfWork() as uow:
            update_data = UserUpdate(
                full_name=full_name, avatar_url=avatar_url
            )
            user = await uow.users.update(user_id, update_data)
            await uow.commit()
            return user

    # ── Listing / search ──────────────────────────────────────────────────────

    async def list_users(
        self,
        tenant_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[User]:
        """
        List users who are members of a specific tenant.

        Uses the user_tenants join table for tenant-scoped listing.

        Raises:
            None — empty page returned if no members exist
        """
        async with UnitOfWork() as uow:
            return await uow.users.list_by_tenant(
                tenant_id, page=page, page_size=page_size
            )

    async def search_users(
        self,
        query: str,
        tenant_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[User]:
        """
        Full-name and email prefix search for users in a tenant.

        Uses case-insensitive ILIKE matching.
        """
        async with UnitOfWork() as uow:
            return await uow.users.search(
                query, tenant_id=tenant_id, page=page, page_size=page_size
            )

    # ── Activation / deactivation ─────────────────────────────────────────────

    async def deactivate_user(
        self, user_id: UUID, admin_user_id: UUID
    ) -> User:
        """
        Set is_active=False on a user (logical suspension).

        The user cannot log in but their data is retained.
        Prevents an admin from deactivating themselves.

        Raises:
            NotFoundError       — user not found
            PermissionDeniedError — admin is trying to deactivate themselves
        """
        if user_id == admin_user_id:
            raise PermissionDeniedError("You cannot deactivate your own account.")

        async with UnitOfWork() as uow:
            user = await uow.users.deactivate(user_id)
            await uow.commit()
            return user

    async def reactivate_user(self, user_id: UUID) -> User:
        """
        Set is_active=True on a previously deactivated user.

        Raises:
            NotFoundError — user not found
        """
        async with UnitOfWork() as uow:
            user = await uow.users.reactivate(user_id)
            await uow.commit()
            return user

    # ── Role assignment ───────────────────────────────────────────────────────

    async def assign_role(
        self,
        user_id: UUID,
        role_id: UUID,
        tenant_id: UUID | None,
        granted_by: UUID,
    ) -> UserRole:
        """
        Assign a role to a user, optionally scoped to a tenant.

        tenant_id=None means a global role assignment (e.g. superadmin).

        Raises:
            NotFoundError  — user or role not found
            ConflictError  — user already has this role in this scope
        """
        async with UnitOfWork() as uow:
            # Validate user and role existence
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFoundError("User", user_id)

            role = await uow.roles.get(role_id)
            if role is None:
                raise NotFoundError("Role", role_id)

            user_role = await uow.roles.assign_role_to_user(
                user_id, role_id, tenant_id=tenant_id, granted_by=granted_by
            )
            await uow.commit()
            return user_role

    async def revoke_role(
        self, user_id: UUID, role_id: UUID, tenant_id: UUID | None
    ) -> None:
        """
        Revoke a role from a user in a specific tenant scope.

        tenant_id=None removes the global assignment only (does NOT
        revoke all tenant-scoped assignments).

        Returns silently if the role was not assigned.
        """
        async with UnitOfWork() as uow:
            revoked = await uow.roles.revoke_role_from_user(
                user_id, role_id, tenant_id=tenant_id
            )
            await uow.commit()
