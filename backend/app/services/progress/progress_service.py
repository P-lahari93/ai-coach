# FILE: app/services/progress/progress_service.py
"""
ProgressService — pre-aggregated progress tracking and leaderboards.

Responsibilities:
- Get/list user progress records
- Update progress after session completion (calls repository upsert)
- Leaderboard queries
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.database.unit_of_work import UnitOfWork
from app.models.progress import UserProgress


class ProgressService:
    """
    User progress tracking service.

    Each method opens its own UnitOfWork.
    """

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_progress(
        self, user_id: UUID, module_id: UUID, tenant_id: UUID | None = None
    ) -> UserProgress | None:
        """
        Fetch the progress record for a (user, module, tenant) triple.

        Returns None if no progress exists yet (user has not started
        any sessions for this module).
        """
        async with UnitOfWork() as uow:
            return await uow.user_progress.get_for_user_module(
                user_id, module_id, tenant_id=tenant_id
            )

    async def list_user_progress(
        self, user_id: UUID, tenant_id: UUID | None = None
    ) -> list[UserProgress]:
        """
        Return all progress records for a user.

        When tenant_id is provided, returns progress for that tenant
        plus platform-level (tenant_id=NULL) records.
        """
        async with UnitOfWork() as uow:
            return await uow.user_progress.list_for_user(
                user_id, tenant_id=tenant_id
            )

    # ── Update after session ──────────────────────────────────────────────────

    async def update_after_session(
        self,
        user_id: UUID,
        module_id: UUID,
        tenant_id: UUID | None,
        final_score: Decimal,
        was_completed: bool = True,
        completion_percent: Decimal = Decimal("0.00"),
    ) -> UserProgress:
        """
        Atomically update or create a UserProgress row after a session ends.

        Calls the repository upsert which increments all counters in a
        single atomic operation.

        Parameters:
            final_score:         the session's final_score
            was_completed:       True for 'completed', False for 'abandoned'
            completion_percent:  pre-computed completion percentage

        Returns:
            The updated UserProgress record.
        """
        async with UnitOfWork() as uow:
            progress = await uow.user_progress.upsert_after_session(
                user_id,
                module_id,
                tenant_id=tenant_id,
                session_score=final_score,
                was_completed=was_completed,
                completion_percent=completion_percent,
            )
            await uow.commit()
            return progress

    # ── Leaderboard ───────────────────────────────────────────────────────────

    async def get_leaderboard(
        self,
        module_id: UUID | None,
        tenant_id: UUID,
        limit: int = 10,
    ) -> list[UserProgress]:
        """
        Top-N learners by average_score within a tenant.

        When module_id is provided, restricts to that module only.
        When module_id is None, returns top learners across all modules.
        """
        async with UnitOfWork() as uow:
            return await uow.user_progress.leaderboard(
                tenant_id, module_id=module_id, top_n=limit
            )
