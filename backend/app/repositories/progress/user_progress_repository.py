"""
UserProgressRepository — async SQLAlchemy 2.0 implementation.

Model: UserProgress (UUIDPrimaryKeyMixin + TimestampMixin)
  Soft-delete:     NO
  Optimistic lock: NO
  Tenant:          nullable (NULL = platform-level, no tenant scope)

NULL-safe uniqueness:
  Two partial indexes in migration enforce one row per (user, module, tenant):
    uq_user_progress_no_tenant   — WHERE tenant_id IS NULL
    uq_user_progress_with_tenant — WHERE tenant_id IS NOT NULL

  The core upsert_after_session() uses raw SQL ON CONFLICT with
  conditional index resolution because SQLAlchemy's ORM insert
  cannot reference partial index constraint names directly.

Transaction contract: no commit() or rollback() here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.progress import UserProgress
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import NotFoundError


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class UserProgressCreate:
    user_id: UUID
    module_id: UUID
    tenant_id: Optional[UUID] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "user_id": self.user_id,
            "module_id": self.module_id,
            "tenant_id": self.tenant_id,
            "sessions_completed": 0,
            "sessions_total": 0,
            "completion_percent": Decimal("0.00"),
            "total_score": Decimal("0.00"),
            "streak_days": 0,
        }


@dataclass
class UserProgressUpdate:
    completion_percent: Optional[Decimal] = None
    streak_days: Optional[int] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result: dict = {}
        if self.completion_percent is not None:
            result["completion_percent"] = self.completion_percent
        if self.streak_days is not None:
            result["streak_days"] = self.streak_days
        return result


# ── Repository ────────────────────────────────────────────────────────────────

class UserProgressRepository(
    BaseRepository[UserProgress, UserProgressCreate, UserProgressUpdate]
):
    """
    Database operations for UserProgress pre-aggregated records.

    The primary write path is upsert_after_session() — a single atomic
    INSERT ... ON CONFLICT DO UPDATE that increments all counters in one
    round-trip, safe under concurrent session completions.
    """

    model = UserProgress

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_for_user_module(
        self,
        user_id: UUID,
        module_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> UserProgress | None:
        """
        Fetch the progress row for a (user, module, tenant) triple.

        NULL tenant_id matches the platform-level row.
        Uses idx_user_progress_user_tenant index.
        """
        stmt = (
            select(UserProgress)
            .where(UserProgress.user_id == user_id)
            .where(UserProgress.module_id == module_id)
        )
        if tenant_id is not None:
            stmt = stmt.where(UserProgress.tenant_id == tenant_id)
        else:
            stmt = stmt.where(UserProgress.tenant_id.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── User progress listing ─────────────────────────────────────────────────

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> list[UserProgress]:
        """
        All progress rows for a user.
        When tenant_id is provided, returns rows for that tenant
        PLUS platform-level rows (tenant_id IS NULL).
        """
        stmt = (
            select(UserProgress)
            .where(UserProgress.user_id == user_id)
        )
        if tenant_id is not None:
            stmt = stmt.where(
                (UserProgress.tenant_id == tenant_id)
                | UserProgress.tenant_id.is_(None)
            )
        stmt = stmt.order_by(UserProgress.last_activity_at.desc().nulls_last())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Leaderboard ───────────────────────────────────────────────────────────

    async def leaderboard(
        self,
        tenant_id: UUID,
        *,
        module_id: UUID | None = None,
        top_n: int = 10,
    ) -> list[UserProgress]:
        """
        Top-N learners by average_score within a tenant.
        Uses idx_user_progress_tenant_score partial index.
        """
        stmt = (
            select(UserProgress)
            .where(UserProgress.tenant_id == tenant_id)
            .where(UserProgress.average_score.is_not(None))
        )
        if module_id is not None:
            stmt = stmt.where(UserProgress.module_id == module_id)
        stmt = stmt.order_by(UserProgress.average_score.desc()).limit(top_n)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Core upsert (called after every session completion) ───────────────────

    async def upsert_after_session(
        self,
        user_id: UUID,
        module_id: UUID,
        *,
        tenant_id: UUID | None,
        session_score: Decimal,
        was_completed: bool,
        completion_percent: Decimal,
    ) -> UserProgress:
        """
        Atomically update or create a UserProgress row after a session ends.

        Uses PostgreSQL INSERT ... ON CONFLICT DO UPDATE with two
        separate conflict targets (one per partial index) selected by
        whether tenant_id is NULL or not.

        All counter increments happen in a single round-trip with no
        read-modify-write, safe under concurrent session completions
        for the same (user, module, tenant).

        Parameters:
            session_score:       final_score of the completed session
            was_completed:       True for 'completed', False for 'abandoned'
            completion_percent:  new pre-computed completion percentage
        """
        now = datetime.now(timezone.utc)
        completed_delta = 1 if was_completed else 0

        if tenant_id is not None:
            # Conflict on the tenant-scoped partial unique index
            stmt = text("""
                INSERT INTO user_progress
                  (id, user_id, module_id, tenant_id,
                   sessions_completed, sessions_total,
                   total_score, average_score, best_score,
                   completion_percent, streak_days,
                   last_activity_at, created_at, updated_at)
                VALUES
                  (gen_random_uuid(), :uid, :mid, :tid,
                   :completed_delta, 1,
                   :score, :score, :score,
                   :pct, 0,
                   :now, :now, :now)
                ON CONFLICT (user_id, module_id, tenant_id)
                WHERE tenant_id IS NOT NULL
                DO UPDATE SET
                  sessions_completed = user_progress.sessions_completed
                               + :completed_delta,
                  sessions_total     = user_progress.sessions_total + 1,
                  total_score        = user_progress.total_score + :score,
                  average_score      = (user_progress.total_score + :score)
                               / NULLIF(user_progress.sessions_completed
                               + :completed_delta, 0),
                  best_score         = GREATEST(user_progress.best_score, :score),
                  completion_percent = :pct,
                  last_activity_at   = :now,
                  updated_at         = :now
                RETURNING *
            """)
        else:
            # Conflict on the NULL-tenant partial unique index
            stmt = text("""
                INSERT INTO user_progress
                  (id, user_id, module_id, tenant_id,
                   sessions_completed, sessions_total,
                   total_score, average_score, best_score,
                   completion_percent, streak_days,
                   last_activity_at, created_at, updated_at)
                VALUES
                  (gen_random_uuid(), :uid, :mid, NULL,
                   :completed_delta, 1,
                   :score, :score, :score,
                   :pct, 0,
                   :now, :now, :now)
                ON CONFLICT (user_id, module_id)
                WHERE tenant_id IS NULL
                DO UPDATE SET
                  sessions_completed = user_progress.sessions_completed
                               + :completed_delta,
                  sessions_total     = user_progress.sessions_total + 1,
                  total_score        = user_progress.total_score + :score,
                  average_score      = (user_progress.total_score + :score)
                               / NULLIF(user_progress.sessions_completed
                               + :completed_delta, 0),
                  best_score         = GREATEST(user_progress.best_score, :score),
                  completion_percent = :pct,
                  last_activity_at   = :now,
                  updated_at         = :now
                RETURNING *
            """)

        result = await self._session.execute(
            stmt,
            {
                "uid": str(user_id),
                "mid": str(module_id),
                "tid": str(tenant_id) if tenant_id else None,
                "score": float(session_score),
                "pct": float(completion_percent),
                "completed_delta": completed_delta,
                "now": now,
            },
        )
        row = result.fetchone()
        if row is None:
            raise NotFoundError("UserProgress", f"{user_id}:{module_id}")

        # Re-fetch as ORM object so callers get a properly mapped instance
        progress = await self.get_for_user_module(
            user_id, module_id, tenant_id=tenant_id
        )
        if progress is None:
            raise NotFoundError("UserProgress", f"{user_id}:{module_id}")
        return progress

    # ── Streak update ─────────────────────────────────────────────────────────

    async def update_streak(
        self,
        user_id: UUID,
        module_id: UUID,
        *,
        tenant_id: UUID | None,
        streak_days: int,
    ) -> bool:
        """
        Set the streak_days counter. Called by the daily streak job.
        Returns True when the row was found and updated.
        """
        stmt = (
            update(UserProgress)
            .where(UserProgress.user_id == user_id)
            .where(UserProgress.module_id == module_id)
        )
        if tenant_id is not None:
            stmt = stmt.where(UserProgress.tenant_id == tenant_id)
        else:
            stmt = stmt.where(UserProgress.tenant_id.is_(None))

        stmt = stmt.values(streak_days=streak_days)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    # ── Aggregate reads ───────────────────────────────────────────────────────

    async def total_sessions_for_user(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> int:
        """Sum of sessions_completed across all modules for a user."""
        stmt = select(
            func.coalesce(func.sum(UserProgress.sessions_completed), 0)
        ).where(UserProgress.user_id == user_id)
        if tenant_id is not None:
            stmt = stmt.where(
                (UserProgress.tenant_id == tenant_id)
                | UserProgress.tenant_id.is_(None)
            )
        return int((await self._session.execute(stmt)).scalar_one())
