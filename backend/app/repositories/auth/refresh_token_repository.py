"""
RefreshTokenRepository — async SQLAlchemy 2.0 implementation.

Covers:
  Token lookup by SHA-256 hash (the primary auth-service query)
  Active token listing per user (security dashboard)
  Single token revocation
  Bulk revocation (all tokens for a user — logout-everywhere)
  Expired/revoked token cleanup (scheduled job support)

Security design (mirrors model docstring):
  The raw token is never stored. Only token_hash (SHA-256 hex, 64 chars)
  is persisted. Lookup is always by hash — never by user_id alone.
  Revocation sets revoked_at; the row is retained for audit purposes
  until the cleanup job removes it.

Model characteristics:
  RefreshToken has NO soft-delete (revoked_at is the sentinel instead).
  RefreshToken has NO version column (no optimistic locking needed —
  each token is write-once then revoke-once; no concurrent updates).
  RefreshToken is user-scoped, not tenant-scoped.

Transaction contract:
  No commit() or rollback() calls here.
  flush() is used after INSERT to materialise the PK.
  The session owner (get_db) handles commit/rollback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import RefreshToken
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import DuplicateError, NotFoundError


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class RefreshTokenCreate:
    """
    Data required to create a new RefreshToken row.

    token_hash: SHA-256 hex digest of the raw opaque token (64 chars).
                The raw token must NEVER be passed here.
    expires_at: absolute expiry timestamp (timezone-aware).
    """

    user_id: UUID
    token_hash: str          # SHA-256 hex, 64 chars
    expires_at: datetime     # timezone-aware
    device_hint: Optional[str] = None
    ip_address: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "user_id": self.user_id,
            "token_hash": self.token_hash,
            "expires_at": self.expires_at,
            "device_hint": self.device_hint,
            "ip_address": self.ip_address,
        }


@dataclass
class RefreshTokenUpdate:
    """Placeholder — RefreshTokens are write-once; update() is not used."""

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        return {}


# ── Repository ────────────────────────────────────────────────────────────────

class RefreshTokenRepository(
    BaseRepository[RefreshToken, RefreshTokenCreate, RefreshTokenUpdate]
):
    """
    All database operations for the RefreshToken security model.

    Token lookup is always by token_hash — the primary key is used
    only for internal relations. The auth service never exposes the
    row id to the client.

    Revocation semantics:
      revoke()             — single token, by id
      revoke_by_hash()     — single token, by hash (auth service path)
      revoke_all_for_user()— all active tokens, for logout-everywhere

    Cleanup:
      delete_expired_and_revoked() — hard-deletes stale rows for a user
      delete_all_expired()         — platform-wide cleanup (scheduled job)

    BaseRepository provides: get, get_or_raise, create, update,
    hard_delete, exists, list_paginated.
    """

    model = RefreshToken

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Hash-based lookup ─────────────────────────────────────────────────────

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """
        Fetch a RefreshToken by its SHA-256 hash.

        This is the primary lookup path used on every token refresh request.
        Uses the unique index on token_hash for O(1) retrieval.

        Returns None when:
          - No token with this hash exists.
          - The token has been revoked (revoked_at IS NOT NULL).
          - The token has expired (expires_at <= now()).

        Callers should check token.is_valid after loading to handle the
        expired/revoked cases explicitly when an audit trail is needed.
        """
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .where(RefreshToken.revoked_at.is_(None))
            .where(RefreshToken.expires_at > datetime.now(timezone.utc))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_hash_including_revoked(
        self, token_hash: str
    ) -> RefreshToken | None:
        """
        Fetch a RefreshToken by hash regardless of revocation/expiry status.

        Used by security audit endpoints that need to inspect token history.
        """
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Active token listing ──────────────────────────────────────────────────

    async def list_active_for_user(
        self, user_id: UUID
    ) -> list[RefreshToken]:
        """
        Return all non-revoked, non-expired tokens for a user.

        Used by the security dashboard to show active sessions.
        Ordered by created_at descending (most recent first).

        Does NOT use pagination — a user should never have more than a
        handful of active tokens (one per device). A large result here
        indicates a token leak and should be flagged by the service layer.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .where(RefreshToken.revoked_at.is_(None))
            .where(RefreshToken.expires_at > now)
            .order_by(RefreshToken.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_active_for_user(self, user_id: UUID) -> int:
        """
        Return the count of active (non-revoked, non-expired) tokens
        for a user.

        Used by the auth service to enforce per-user token limits before
        issuing a new token (e.g. max 10 concurrent sessions).
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .where(RefreshToken.revoked_at.is_(None))
            .where(RefreshToken.expires_at > now)
        )
        return (await self._session.execute(stmt)).scalar_one()

    # ── Revocation ────────────────────────────────────────────────────────────

    async def revoke(self, token_id: UUID) -> bool:
        """
        Revoke a single token by its primary key.

        Sets revoked_at = now(). Idempotent: if already revoked,
        returns False without error.

        Returns True when the token was found and revoked.
        Returns False when the token was already revoked or not found.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def revoke_by_hash(self, token_hash: str) -> bool:
        """
        Revoke a single token by its SHA-256 hash.

        The primary revocation path used by the auth service on logout.
        Sets revoked_at = now(). Idempotent: safe to call on already-
        revoked tokens.

        Returns True when an active token was found and revoked.
        Returns False when the token was already revoked or does not exist.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def revoke_all_for_user(self, user_id: UUID) -> int:
        """
        Revoke all active tokens for a user (logout-everywhere).

        Sets revoked_at = now() on every non-revoked token for the user.
        Expired tokens are left unchanged (already unusable).

        Returns the count of tokens that were revoked.
        Used by:
          - Explicit "logout from all devices" action
          - Password change (all sessions invalidated as a security measure)
          - Account suspension / deactivation
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def delete_expired_and_revoked(
        self,
        user_id: UUID,
    ) -> int:
        """
        Hard-delete all expired or revoked tokens for a specific user.

        Called after issuing a new token to keep the table lean
        on a per-user basis. Rows that are both within expiry AND
        not yet revoked are intentionally preserved (they represent
        active sessions).

        Returns the count of deleted rows.
        """
        now = datetime.now(timezone.utc)
        stmt = sa_delete(RefreshToken).where(
            RefreshToken.user_id == user_id,
            (RefreshToken.expires_at <= now)
            | RefreshToken.revoked_at.is_not(None),
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def delete_all_expired(
        self,
        *,
        older_than: datetime | None = None,
    ) -> int:
        """
        Platform-wide hard-delete of expired tokens.

        Designed to be called by a scheduled cleanup job (e.g. nightly).
        Deletes tokens where expires_at <= older_than (defaults to now()).

        Also deletes revoked tokens regardless of their expiry timestamp —
        revoked tokens are permanently unusable and carry no audit value
        beyond the revoked_at timestamp which is already in the session log.

        Parameters:
            older_than: only delete tokens that expired before this
                        timestamp. Defaults to now(). Set to a date in
                        the past (e.g. now() - 7 days) for conservative
                        cleanup that retains recently-expired tokens for
                        short-term audit purposes.

        Returns the count of deleted rows.
        """
        cutoff = older_than or datetime.now(timezone.utc)
        stmt = sa_delete(RefreshToken).where(
            (RefreshToken.expires_at <= cutoff)
            | RefreshToken.revoked_at.is_not(None)
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    # ── Override create for descriptive DuplicateError ───────────────────────

    async def create(  # type: ignore[override]
        self, data: RefreshTokenCreate
    ) -> RefreshToken:
        """
        Persist a new RefreshToken row.

        Maps the unique constraint on token_hash (uq on token_hash column)
        to a DuplicateError. A hash collision is cryptographically
        near-impossible; if it occurs it indicates a bug in the token
        generator.

        Transaction note: no rollback here — session owner handles it.
        """
        try:
            token = RefreshToken(
                user_id=data.user_id,
                token_hash=data.token_hash,
                expires_at=data.expires_at,
                device_hint=data.device_hint,
                ip_address=data.ip_address,
            )
            self._session.add(token)
            await self._session.flush()
            await self._session.refresh(token)
            return token
        except IntegrityError as exc:
            raise DuplicateError(
                entity="RefreshToken",
                field="token_hash",
                value="(hash collision — regenerate token)",
            ) from exc
