# FILE: app/services/auth/auth_service.py
"""
AuthService — authentication, registration, login, logout, token management.

Responsibilities:
- Register new users (hash password, assign default role, create tenant membership)
- Login (verify credentials, create JWT pair, store refresh token hash)
- Refresh tokens (validate stored hash, issue new access token)
- Logout (revoke refresh token)
- Change password
- List/revoke active sessions

Does NOT contain HTTP concerns — raises domain exceptions only.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    InvalidTokenError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database.unit_of_work import UnitOfWork
from app.models.user import RefreshToken, User
from app.repositories.auth.refresh_token_repository import RefreshTokenCreate
from app.repositories.auth.user_repository import UserCreate, UserUpdate


@dataclass(frozen=True)
class TokenPair:
    """Returned on login and token refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _hash_token(raw_token: str) -> str:
    """Return SHA-256 hex digest of a raw token string."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


class AuthService:
    """
    All authentication flows: registration, login, token refresh, logout,
    password change, and session listing/revocation.

    Each method opens its own UnitOfWork so callers (routers, tasks, tests)
    do not need to manage transactions.
    """

    # ── Registration ──────────────────────────────────────────────────────────

    async def register(
        self,
        email: str,
        password: str,
        full_name: str,
        tenant_id: UUID | None = None,
    ) -> User:
        """
        Register a new user account.

        - Checks that the email is not already taken.
        - Hashes the password with bcrypt.
        - Assigns the "learner" default role (scoped to tenant_id if provided).
        - Creates a UserTenant membership when tenant_id is given.

        Raises:
            ConflictError   — email already registered
            NotFoundError   — "learner" role does not exist in seed data
        """
        async with UnitOfWork() as uow:
            # Check for duplicate email
            existing = await uow.users.get_by_email(email)
            if existing is not None:
                raise ConflictError(f"Email '{email}' is already registered.")

            # Create the user
            user = await uow.users.create(
                UserCreate(
                    email=email.lower().strip(),
                    password_hash=hash_password(password),
                    full_name=full_name,
                    is_active=True,
                    is_superadmin=False,
                )
            )

            # Assign the default "learner" role
            learner_role = await uow.roles.get_by_name("learner")
            if learner_role is None:
                raise NotFoundError("Role", "learner")

            await uow.roles.assign_role_to_user(
                user.id,
                learner_role.id,
                tenant_id=tenant_id,
                granted_by=None,
            )

            # Add tenant membership if scoped
            if tenant_id is not None:
                await uow.users.add_tenant_membership(
                    user.id,
                    tenant_id,
                    is_primary=True,
                )

            await uow.commit()
            return user

    # ── Login ─────────────────────────────────────────────────────────────────

    async def login(
        self,
        email: str,
        password: str,
        tenant_id: UUID | None = None,
    ) -> TokenPair:
        """
        Authenticate a user and return a JWT access+refresh token pair.

        - Loads user with roles for JWT claims.
        - Verifies password with bcrypt.
        - Creates signed JWT access token and opaque refresh token.
        - Stores SHA-256 hash of the refresh token in the DB.
        - Updates last_login_at timestamp.
        - Cleans up expired/revoked tokens for the user.

        Raises:
            AuthenticationError — invalid credentials or inactive account
        """
        async with UnitOfWork() as uow:
            user = await uow.users.get_by_email_with_roles(
                email, tenant_id=tenant_id
            )

            if user is None or not user.is_active:
                raise AuthenticationError("Invalid email or password.")

            if not verify_password(password, user.password_hash):
                raise AuthenticationError("Invalid email or password.")

            # Resolve role names for the JWT claims
            role_names: list[str] = [
                ur.role.name
                for ur in (user.user_roles or [])
                if ur.role is not None
            ]

            # Create tokens
            access_token = create_access_token(
                user_id=user.id,
                roles=role_names,
                tenant_id=tenant_id,
            )
            raw_refresh_token = secrets.token_urlsafe(32)
            token_hash = _hash_token(raw_refresh_token)

            expires_at = datetime.now(timezone.utc) + timedelta(
                days=settings.REFRESH_TOKEN_EXPIRE_DAYS
            )

            # Persist the hashed refresh token
            await uow.refresh_tokens.create(
                RefreshTokenCreate(
                    user_id=user.id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )

            # Update last login timestamp
            await uow.users.update_last_login(user.id)

            # Housekeeping: remove stale tokens (non-blocking within tx)
            await uow.refresh_tokens.delete_expired_and_revoked(user.id)

            await uow.commit()

        return TokenPair(
            access_token=access_token,
            refresh_token=raw_refresh_token,
        )

    # ── Token refresh ─────────────────────────────────────────────────────────

    async def refresh_access_token(self, refresh_token: str) -> TokenPair:
        """
        Issue a new access token given a valid refresh token.

        - Hashes the supplied raw token.
        - Looks up the hash in the DB (must be valid: not revoked, not expired).
        - Loads the user with roles for JWT claims.
        - Issues a new access token (refresh token is reused, not rotated).

        Raises:
            InvalidTokenError — token not found, revoked, or expired
        """
        token_hash = _hash_token(refresh_token)

        async with UnitOfWork() as uow:
            stored = await uow.refresh_tokens.get_by_hash(token_hash)
            if stored is None:
                raise InvalidTokenError("Refresh token is invalid or has expired.")

            user = await uow.users.get_with_roles(stored.user_id)
            if user is None or not user.is_active:
                raise InvalidTokenError("User account not found or is inactive.")

            role_names: list[str] = [
                ur.role.name
                for ur in (user.user_roles or [])
                if ur.role is not None
            ]

            # Determine tenant_id from the stored token context (none stored
            # on the token itself — use None; routers can re-scope via header)
            access_token = create_access_token(
                user_id=user.id,
                roles=role_names,
                tenant_id=None,
            )

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,  # return the same raw token
        )

    # ── Logout ────────────────────────────────────────────────────────────────

    async def logout(self, refresh_token: str) -> None:
        """
        Revoke a refresh token, invalidating the user's session.

        Silently succeeds if the token was already revoked or does not
        exist (idempotent).
        """
        token_hash = _hash_token(refresh_token)

        async with UnitOfWork() as uow:
            await uow.refresh_tokens.revoke_by_hash(token_hash)
            await uow.commit()

    # ── Password change ───────────────────────────────────────────────────────

    async def change_password(
        self,
        user_id: UUID,
        current_password: str,
        new_password: str,
    ) -> None:
        """
        Change a user's password after verifying the current one.

        Revokes all existing refresh tokens as a security measure so
        any stolen sessions are immediately invalidated.

        Raises:
            NotFoundError       — user not found
            AuthenticationError — current_password is incorrect
            ValidationError     — new password is empty
        """
        if not new_password or not new_password.strip():
            raise ValidationError("New password must not be empty.")

        async with UnitOfWork() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFoundError("User", user_id)

            if not verify_password(current_password, user.password_hash):
                raise AuthenticationError("Current password is incorrect.")

            await uow.users.update(
                user_id,
                UserUpdate(password_hash=hash_password(new_password)),
            )

            # Revoke all sessions — password change invalidates all devices
            await uow.refresh_tokens.revoke_all_for_user(user_id)

            await uow.commit()

    # ── Session listing ───────────────────────────────────────────────────────

    async def get_active_sessions(self, user_id: UUID) -> list[RefreshToken]:
        """
        Return all active (non-revoked, non-expired) refresh tokens for a user.

        Used to display the "active sessions" security dashboard.

        Raises:
            NotFoundError — user not found
        """
        async with UnitOfWork() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise NotFoundError("User", user_id)

            return await uow.refresh_tokens.list_active_for_user(user_id)

    # ── Session revocation ────────────────────────────────────────────────────

    async def revoke_session(self, user_id: UUID, token_id: UUID) -> None:
        """
        Revoke a specific refresh token (session) owned by user_id.

        Validates ownership before revoking to prevent a user from
        revoking another user's session.

        Raises:
            NotFoundError       — token not found
            PermissionDeniedError — token does not belong to user_id
        """
        async with UnitOfWork() as uow:
            token = await uow.refresh_tokens.get(token_id)
            if token is None:
                raise NotFoundError("RefreshToken", token_id)

            if token.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to revoke this session."
                )

            await uow.refresh_tokens.revoke(token_id)
            await uow.commit()
