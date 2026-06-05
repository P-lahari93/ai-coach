# FILE: app/services/progress/achievement_service.py
"""
AchievementService — achievement definition and awarding logic.

Responsibilities:
- List achievements (global + tenant)
- Get user achievements
- Award achievements (idempotent)
- Check criteria and auto-award (called after trigger events)
"""
from __future__ import annotations

from uuid import UUID

from app.database.unit_of_work import UnitOfWork
from app.models.gamification import Achievement, UserAchievement


class AchievementService:
    """
    Achievement definition and awarding service.

    Each method opens its own UnitOfWork.
    """

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_achievements(
        self, tenant_id: UUID | None = None
    ) -> list[Achievement]:
        """
        List all active achievements.

        When tenant_id is provided, returns global (tenant_id=NULL)
        plus tenant-specific achievements.
        When tenant_id is None, returns all achievements (superadmin).
        """
        async with UnitOfWork() as uow:
            from sqlalchemy import select

            from app.models.gamification import Achievement

            stmt = (
                select(Achievement)
                .where(Achievement.is_active.is_(True))
                .order_by(Achievement.points.desc(), Achievement.name.asc())
            )

            if tenant_id is not None:
                stmt = stmt.where(
                    (Achievement.tenant_id == tenant_id)
                    | Achievement.tenant_id.is_(None)
                )

            result = await uow.session.execute(stmt)
            return list(result.scalars().all())

    async def get_user_achievements(
        self, user_id: UUID, tenant_id: UUID | None = None
    ) -> list[UserAchievement]:
        """
        Return all achievements earned by a user.

        When tenant_id is provided, filters to that tenant scope
        plus global awards (tenant_id=NULL).
        """
        async with UnitOfWork() as uow:
            from sqlalchemy import select

            from app.models.gamification import UserAchievement

            stmt = (
                select(UserAchievement)
                .where(UserAchievement.user_id == user_id)
                .order_by(UserAchievement.awarded_at.desc())
            )

            if tenant_id is not None:
                stmt = stmt.where(
                    (UserAchievement.tenant_id == tenant_id)
                    | UserAchievement.tenant_id.is_(None)
                )

            result = await uow.session.execute(stmt)
            return list(result.scalars().all())

    # ── Award achievement ─────────────────────────────────────────────────────

    async def award_achievement(
        self,
        user_id: UUID,
        achievement_key: str,
        tenant_id: UUID | None = None,
        metadata: dict | None = None,
    ) -> UserAchievement | None:
        """
        Award an achievement to a user.

        Idempotent: if the user already has this achievement in this
        scope, returns None without error.

        Raises:
            NotFoundError — achievement key not found
        """
        async with UnitOfWork() as uow:
            from sqlalchemy import select

            from app.core.exceptions import NotFoundError
            from app.models.gamification import Achievement, UserAchievement

            # Find the achievement
            stmt = (
                select(Achievement)
                .where(Achievement.key == achievement_key)
                .where(Achievement.is_active.is_(True))
            )
            if tenant_id is not None:
                stmt = stmt.where(
                    (Achievement.tenant_id == tenant_id)
                    | Achievement.tenant_id.is_(None)
                )
            else:
                stmt = stmt.where(Achievement.tenant_id.is_(None))

            result = await uow.session.execute(stmt)
            achievement = result.scalar_one_or_none()

            if achievement is None:
                raise NotFoundError("Achievement", achievement_key)

            # Check if already awarded (idempotent)
            check_stmt = (
                select(UserAchievement)
                .where(UserAchievement.user_id == user_id)
                .where(UserAchievement.achievement_id == achievement.id)
            )
            if tenant_id is not None:
                check_stmt = check_stmt.where(
                    UserAchievement.tenant_id == tenant_id
                )
            else:
                check_stmt = check_stmt.where(
                    UserAchievement.tenant_id.is_(None)
                )

            existing = await uow.session.execute(check_stmt)
            if existing.scalar_one_or_none() is not None:
                return None  # already earned

            # Award the achievement
            from datetime import datetime, timezone

            user_achievement = UserAchievement(
                user_id=user_id,
                achievement_id=achievement.id,
                tenant_id=tenant_id,
                awarded_at=datetime.now(timezone.utc),
                metadata_=metadata or {},
            )
            uow.session.add(user_achievement)
            await uow.session.flush()
            await uow.session.refresh(user_achievement)
            await uow.commit()
            return user_achievement

    # ── Criteria checking and auto-award ──────────────────────────────────────

    async def check_and_award_achievements(
        self,
        user_id: UUID,
        module_id: UUID,
        tenant_id: UUID | None,
        trigger_event: str,
        context: dict,
    ) -> list[UserAchievement]:
        """
        Check achievement criteria after a trigger event and auto-award
        any that have been earned.

        Stub implementation: returns empty list. In production, this
        would query achievements with matching criteria.type, evaluate
        the criteria against user progress data, and call award_achievement()
        for each newly-earned achievement.

        Parameters:
            trigger_event: e.g. "session_completed", "feedback_viewed"
            context:       event data (session_id, score, etc.)

        Returns:
            List of newly-awarded UserAchievement records.
        """
        # TODO: implement criteria evaluation logic
        # For now, return empty list
        return []
