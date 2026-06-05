"""
Gamification domain models: Achievement and UserAchievement.

Architecture decisions:
────────────────────────

  Achievement
    Definition of an earnable badge/milestone.

    key: machine-readable identifier used by the gamification engine
         to check and award achievements programmatically.
         e.g. 'first_session', 'score_90_plus', 'seven_day_streak'

    tenant_id = NULL  -> global platform achievement available to all
    tenant_id set     -> tenant-custom achievement

    Uniqueness (FIX DB-04 from validation report):
      Achievement key uniqueness must be per-tenant-scope, not global.
      Otherwise Tenant A's 'first_session' would block Tenant B from
      creating their own 'first_session' with different criteria.
      Handled by two partial indexes in migration:
        UNIQUE (key) WHERE tenant_id IS NULL       (global)
        UNIQUE (key, tenant_id) WHERE tenant_id IS NOT NULL  (tenant)

    criteria JSONB schema:
        {
          "type": "session_count",      // see types below
          "threshold": 5,
          "module_key": null,           // optional: restrict to module
          "score_min": null             // optional: minimum score
        }

        Supported type values:
          session_count    — complete N sessions
          score_threshold  — achieve score >= threshold
          streak_days      — maintain a N-day streak
          module_complete  — complete a specific module (100%)
          feedback_count   — view N feedback reports
          roleplay_count   — complete N roleplay sessions

        The gamification service evaluates these criteria after
        relevant events (session_completed, feedback_viewed, etc.)
        using UserProgress and AnalyticsEvent data.

  UserAchievement
    Award record: which user earned which achievement and when.

    Uniqueness: (user_id, achievement_id, tenant_id) — a user can
    only earn the same achievement once per tenant scope.
    Same NULL-handling issue as UserProgress — two partial indexes
    in migration handle the NULL tenant_id case.

    awarded_at: when the gamification engine awarded the achievement.
    metadata_ JSONB: context at award time, e.g.:
        {"session_id": "uuid", "score": 91.5, "streak_at_award": 8}
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.tenant import Tenant


# ─────────────────────────────────────────────────────────────────────────────
# Achievement
# ─────────────────────────────────────────────────────────────────────────────

class Achievement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Definition of an earnable achievement.

    Global achievements (tenant_id=NULL) are seeded at platform launch.
    Tenant-custom achievements are created by tenant admins.

    Key uniqueness per scope is enforced by partial indexes in migration.
    """

    __tablename__ = "achievements"
    __table_args__ = (
        Index("idx_achievements_tenant", "tenant_id"),
        Index(
            "idx_achievements_active",
            "tenant_id",
            "is_active",
            postgresql_where=text("is_active = true"),
        ),
        # NOTE: partial unique indexes for key uniqueness by scope
        # are defined in Alembic migration (FIX DB-04):
        #   CREATE UNIQUE INDEX uq_achievement_key_global
        #   ON achievements (key)
        #   WHERE tenant_id IS NULL;
        #
        #   CREATE UNIQUE INDEX uq_achievement_key_tenant
        #   ON achievements (key, tenant_id)
        #   WHERE tenant_id IS NOT NULL;
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Machine-readable identifier, e.g. 'first_session'",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name, e.g. 'First Steps'",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Shown to the learner in the achievements gallery",
    )
    icon: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Icon identifier for the UI, e.g. 'Trophy'",
    )
    points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Gamification points awarded when earned",
    )
    criteria: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment=(
            "Evaluation criteria for the gamification engine. "
            "Schema: {type, threshold, module_key?, score_min?}"
        ),
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = global platform achievement",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="Inactive achievements are hidden but existing awards kept",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )
    user_achievements: Mapped[list[UserAchievement]] = relationship(
        "UserAchievement",
        back_populates="achievement",
        cascade="all, delete-orphan",
        lazy="write_only",
    )

    def __repr__(self) -> str:
        return (
            f"<Achievement key={self.key!r} "
            f"points={self.points} tenant={self.tenant_id}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# UserAchievement
# ─────────────────────────────────────────────────────────────────────────────

class UserAchievement(UUIDPrimaryKeyMixin, Base):
    """
    Award record: a user earned an achievement at a specific time.

    Append-only — rows are never updated once created.
    No TimestampMixin: awarded_at serves as the single timestamp;
    there is no updated_at concept for an immutable award record.

    Uniqueness: one award per (user, achievement, tenant) scope.
    Same NULL-safe uniqueness requirement as UserProgress.
    Two partial indexes in migration handle NULL tenant_id.

    metadata_ JSONB captures context at award time:
        {
          "session_id": "uuid-of-triggering-session",
          "score": 91.5,
          "streak_days_at_award": 8,
          "module_key": "sbi_feedback"
        }
    Useful for "how did you earn this?" explanations in the UI.
    """

    __tablename__ = "user_achievements"
    __table_args__ = (
        Index("idx_user_achievements_user_tenant", "user_id", "tenant_id"),
        Index("idx_user_achievements_achievement", "achievement_id"),
        Index("idx_user_achievements_awarded", "user_id", "awarded_at"),
        # NOTE: partial unique indexes for NULL-safe uniqueness
        # are defined in Alembic migration:
        #   CREATE UNIQUE INDEX uq_user_achievement_no_tenant
        #   ON user_achievements (user_id, achievement_id)
        #   WHERE tenant_id IS NULL;
        #
        #   CREATE UNIQUE INDEX uq_user_achievement_with_tenant
        #   ON user_achievements (user_id, achievement_id, tenant_id)
        #   WHERE tenant_id IS NOT NULL;
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    achievement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("achievements.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = global achievement award",
    )
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        comment="When the gamification engine awarded this achievement",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Context at award time: session_id, score, streak, module_key",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="achievements",
        lazy="select",
    )
    achievement: Mapped[Achievement] = relationship(
        "Achievement",
        back_populates="user_achievements",
        lazy="selectin",    # always load the achievement definition with the award
    )
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<UserAchievement user={self.user_id} "
            f"achievement={self.achievement_id} "
            f"awarded={self.awarded_at}>"
        )
