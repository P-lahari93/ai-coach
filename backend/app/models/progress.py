"""
User progress tracking model.

Architecture decisions:
────────────────────────

  UserProgress
    Pre-aggregated progress record per (user, module, tenant).

    Why pre-aggregate instead of computing from sessions?
    ──────────────────────────────────────────────────────
    Computing "completion percentage", "average score", and "streak"
    for the dashboard by querying coaching_sessions at read time
    would require:
      SELECT COUNT(*), AVG(final_score), MAX(created_at)
      FROM coaching_sessions
      WHERE user_id = :uid AND module_id = :mid AND status = 'completed'
    At 100k users x 10 modules = 1M potential rows scanned per
    dashboard load. Pre-aggregation makes dashboard reads O(1).

    Update strategy:
    ─────────────────
    The SessionService calls an UPSERT after every session completion:
      INSERT INTO user_progress (...) VALUES (...)
      ON CONFLICT (user_id, module_id, COALESCE(tenant_id, '00000000-...'))
      DO UPDATE SET
        sessions_completed = user_progress.sessions_completed + 1,
        total_score = user_progress.total_score + EXCLUDED.overall_score,
        average_score = (total_score + EXCLUDED.overall_score) /
                        (sessions_completed + 1),
        best_score = GREATEST(best_score, EXCLUDED.overall_score),
        last_activity_at = now(),
        updated_at = now()
    This is a single indexed write, no read-modify-write needed.

    Uniqueness + NULL tenant_id (FIX DB-03):
    ──────────────────────────────────────────
    PostgreSQL treats NULL != NULL in standard UNIQUE constraints.
    A user without a tenant could have multiple rows for the same
    module. Two partial indexes enforce correct uniqueness:
      UNIQUE (user_id, module_id) WHERE tenant_id IS NULL
      UNIQUE (user_id, module_id, tenant_id) WHERE tenant_id IS NOT NULL
    Declared in the Alembic migration (not expressible as plain
    UniqueConstraint due to WHERE clauses).

    streak_days:
    ─────────────
    Number of consecutive days the user has completed at least one
    session for this module. Updated by a daily scheduled job that
    checks last_activity_at and increments or resets accordingly.
    Stored here for O(1) reads; recalculated from sessions if needed.

    completion_percent:
    ────────────────────
    Percentage of the module's recommended session count completed.
    Calculated by: (sessions_completed / target_sessions) * 100
    where target_sessions comes from module gamification_overrides
    or a platform default. Updated on each session completion.
    Clamped to 100.00 by the SessionService.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.tenant import Tenant
    from app.models.module import CoachingModule


class UserProgress(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Pre-aggregated learning progress per user x module x tenant.

    One row per (user, module, tenant) combination.
    Uniqueness with NULL tenant handled by partial indexes in migration.
    Updated via UPSERT after every session completion.
    """

    __tablename__ = "user_progress"
    __table_args__ = (
        # Standard indexes for lookup patterns
        Index("idx_user_progress_user_tenant", "user_id", "tenant_id"),
        Index("idx_user_progress_module", "module_id"),
        # Partial index for activity-based queries (streak reminders,
        # learner re-engagement campaigns)
        Index(
            "idx_user_progress_activity",
            "user_id",
            "last_activity_at",
            postgresql_where=text("last_activity_at IS NOT NULL"),
        ),
        # Top learner leaderboard by average_score within a tenant
        Index(
            "idx_user_progress_tenant_score",
            "tenant_id",
            "average_score",
            postgresql_where=text("average_score IS NOT NULL"),
        ),
        # NOTE: Partial UNIQUE indexes for NULL-safe uniqueness constraint
        # are defined in the Alembic migration (FIX DB-03):
        #   CREATE UNIQUE INDEX uq_user_progress_no_tenant
        #   ON user_progress (user_id, module_id)
        #   WHERE tenant_id IS NULL;
        #
        #   CREATE UNIQUE INDEX uq_user_progress_with_tenant
        #   ON user_progress (user_id, module_id, tenant_id)
        #   WHERE tenant_id IS NOT NULL;
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_modules.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = platform-level progress (no tenant scope)",
    )
    sessions_completed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Count of status='completed' sessions for this module",
    )
    sessions_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Count of all sessions started (completed + abandoned)",
    )
    completion_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="0.00-100.00; based on sessions_completed / target_sessions",
    )
    best_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Highest final_score achieved across all sessions",
    )
    average_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Running average of final_score; = total_score / sessions_completed",
    )
    total_score: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Running sum of final_score; used to compute average_score",
    )
    streak_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Consecutive days with at least one completed session",
    )
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of most recent session completion for this module",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="progress",
        lazy="select",
    )
    module: Mapped[CoachingModule] = relationship(
        "CoachingModule",
        foreign_keys=[module_id],
        back_populates="user_progress",
        lazy="select",
    )
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def has_started(self) -> bool:
        return self.sessions_total > 0

    @property
    def is_complete(self) -> bool:
        return self.completion_percent >= Decimal("100.00")

    def __repr__(self) -> str:
        return (
            f"<UserProgress user={self.user_id} "
            f"module={self.module_id} "
            f"pct={self.completion_percent} "
            f"avg={self.average_score}>"
        )
