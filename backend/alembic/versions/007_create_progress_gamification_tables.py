"""Create progress tracking, gamification, and notification tables.

Revision ID: 007
Revises: 006
Create Date: 2026-06-04

Tables created (Tier 5 — FK deps: users, coaching_modules, tenants):
  user_progress       — pre-aggregated learning progress per (user, module, tenant)
  achievements        — earnable badge/milestone definitions
  user_achievements   — award records (append-only, no TimestampMixin)
  notifications       — in-app notification records
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "007"
down_revision: str = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # user_progress
    # Pre-aggregated progress per (user, module, tenant).
    # NULL-safe uniqueness requires partial indexes (FIX DB-03).
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "user_progress",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("module_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True,
                  comment="NULL = platform-level progress (no tenant scope)"),
        sa.Column("sessions_completed", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("sessions_total", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("completion_percent", sa.Numeric(5, 2), nullable=False,
                  server_default=sa.text("0.00")),
        sa.Column("best_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("average_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("total_score", sa.Numeric(10, 2), nullable=False,
                  server_default=sa.text("0.00")),
        sa.Column("streak_days", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_user_progress_user"),
        sa.ForeignKeyConstraint(["module_id"], ["coaching_modules.id"],
                                ondelete="CASCADE",
                                name="fk_user_progress_module"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_user_progress_tenant"),
    )
    op.create_index("idx_user_progress_user_tenant",
                    "user_progress", ["user_id", "tenant_id"])
    op.create_index("idx_user_progress_module",
                    "user_progress", ["module_id"])
    op.create_index(
        "idx_user_progress_activity", "user_progress",
        ["user_id", "last_activity_at"],
        postgresql_where=sa.text("last_activity_at IS NOT NULL"),
    )
    op.create_index(
        "idx_user_progress_tenant_score", "user_progress",
        ["tenant_id", "average_score"],
        postgresql_where=sa.text("average_score IS NOT NULL"),
    )
    # NULL-safe uniqueness: one row per (user, module) without tenant (FIX DB-03)
    op.execute(
        "CREATE UNIQUE INDEX uq_user_progress_no_tenant "
        "ON user_progress (user_id, module_id) "
        "WHERE tenant_id IS NULL"
    )
    # NULL-safe uniqueness: one row per (user, module, tenant)
    op.execute(
        "CREATE UNIQUE INDEX uq_user_progress_with_tenant "
        "ON user_progress (user_id, module_id, tenant_id) "
        "WHERE tenant_id IS NOT NULL"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # achievements
    # Global (tenant_id=NULL) or tenant-custom achievement definitions.
    # NULL-safe uniqueness on key per tenant scope (FIX DB-04).
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "achievements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False,
                  comment="Machine-readable identifier, e.g. 'first_session'"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("icon", sa.String(100), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("criteria", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True,
                  comment="NULL = global platform achievement"),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_achievements_tenant"),
    )
    op.create_index("idx_achievements_tenant", "achievements", ["tenant_id"])
    op.create_index(
        "idx_achievements_active", "achievements", ["tenant_id", "is_active"],
        postgresql_where=sa.text("is_active = true"),
    )
    # NULL-safe unique key per scope (FIX DB-04)
    op.execute(
        "CREATE UNIQUE INDEX uq_achievement_key_global "
        "ON achievements (key) "
        "WHERE tenant_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_achievement_key_tenant "
        "ON achievements (key, tenant_id) "
        "WHERE tenant_id IS NOT NULL"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # user_achievements  (append-only award records)
    # No TimestampMixin — awarded_at is the sole timestamp.
    # NULL-safe uniqueness on (user, achievement, tenant).
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "user_achievements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("achievement_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True,
                  comment="NULL = global achievement award"),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("metadata", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb"),
                  comment="Context at award time: session_id, score, streak, module_key"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_user_achievements_user"),
        sa.ForeignKeyConstraint(["achievement_id"], ["achievements.id"],
                                ondelete="CASCADE",
                                name="fk_user_achievements_achievement"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_user_achievements_tenant"),
    )
    op.create_index("idx_user_achievements_user_tenant",
                    "user_achievements", ["user_id", "tenant_id"])
    op.create_index("idx_user_achievements_achievement",
                    "user_achievements", ["achievement_id"])
    op.create_index("idx_user_achievements_awarded",
                    "user_achievements", ["user_id", "awarded_at"])
    # NULL-safe uniqueness: one award per (user, achievement) without tenant
    op.execute(
        "CREATE UNIQUE INDEX uq_user_achievement_no_tenant "
        "ON user_achievements (user_id, achievement_id) "
        "WHERE tenant_id IS NULL"
    )
    # NULL-safe uniqueness: one award per (user, achievement, tenant)
    op.execute(
        "CREATE UNIQUE INDEX uq_user_achievement_with_tenant "
        "ON user_achievements (user_id, achievement_id, tenant_id) "
        "WHERE tenant_id IS NOT NULL"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # notifications
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True,
                  comment="NULL = platform-level notification"),
        sa.Column("notification_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True,
                  comment="Polymorphic reference — no FK"),
        sa.Column("extra", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "notification_type IN ("
            "'session_feedback_ready', 'achievement_earned', "
            "'module_published', 'kb_processing_complete', "
            "'kb_processing_failed', 'system_message', 'streak_reminder'"
            ")",
            name="ck_notification_type",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_notifications_user"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_notifications_tenant"),
    )
    # Critical: unread count query — partial index on unread only
    op.create_index(
        "idx_notifications_user_unread", "notifications",
        ["user_id", "created_at"],
        postgresql_where=sa.text("is_read = false"),
    )
    op.create_index("idx_notifications_user_all",
                    "notifications", ["user_id", "created_at"])
    op.create_index("idx_notifications_tenant_type",
                    "notifications",
                    ["tenant_id", "notification_type", "created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("user_achievements")
    op.drop_table("achievements")
    op.drop_table("user_progress")
