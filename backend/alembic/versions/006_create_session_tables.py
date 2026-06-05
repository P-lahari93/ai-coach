"""Create session domain tables.

Revision ID: 006
Revises: 005
Create Date: 2026-06-04

Tables created (Tier 4 — FK deps: users, coaching_modules,
module_versions, module_personas, tenants, rubrics):
  coaching_sessions
  conversation_messages
  roleplay_sessions
  roleplay_messages
  feedback_reports
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "006"
down_revision: str = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # coaching_sessions
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "coaching_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("module_id", UUID(as_uuid=True), nullable=False,
                  comment="RESTRICT: cannot delete a module with active sessions"),
        sa.Column("module_version_id", UUID(as_uuid=True), nullable=False,
                  comment="Pinned at session creation; never changes"),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'in_progress'"),
                  comment="in_progress | completed | abandoned"),
        sa.Column("intake_data", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("final_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'abandoned')",
            name="ck_coaching_session_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_coaching_sessions_user"),
        sa.ForeignKeyConstraint(["module_id"], ["coaching_modules.id"],
                                ondelete="RESTRICT",
                                name="fk_coaching_sessions_module"),
        sa.ForeignKeyConstraint(["module_version_id"], ["module_versions.id"],
                                ondelete="RESTRICT",
                                name="fk_coaching_sessions_version"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="SET NULL",
                                name="fk_coaching_sessions_tenant"),
    )
    op.create_index("idx_coaching_sessions_user_created",
                    "coaching_sessions", ["user_id", "created_at"])
    op.create_index("idx_coaching_sessions_tenant_created",
                    "coaching_sessions", ["tenant_id", "created_at"])
    op.create_index("idx_coaching_sessions_module_status",
                    "coaching_sessions", ["module_id", "status"])
    op.create_index("idx_coaching_sessions_user_tenant_status",
                    "coaching_sessions",
                    ["user_id", "tenant_id", "status", "created_at"])

    # ──────────────────────────────────────────────────────────────────────────
    # conversation_messages  (append-only)
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "conversation_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False,
                  comment="user | assistant | system"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_index", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("session_id", "message_index",
                            name="uq_conversation_message_index"),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')",
                           name="ck_conv_message_role"),
        sa.ForeignKeyConstraint(["session_id"], ["coaching_sessions.id"],
                                ondelete="CASCADE",
                                name="fk_conv_messages_session"),
    )
    op.create_index("idx_conv_messages_session",
                    "conversation_messages", ["session_id", "message_index"])

    # ──────────────────────────────────────────────────────────────────────────
    # roleplay_sessions
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "roleplay_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("module_id", UUID(as_uuid=True), nullable=False),
        sa.Column("module_version_id", UUID(as_uuid=True), nullable=False,
                  comment="Pinned at session creation; never changes"),
        sa.Column("persona_id", UUID(as_uuid=True), nullable=True,
                  comment="NULL = use module default persona"),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'active'"),
                  comment="active | paused | completed | abandoned"),
        sa.Column("context", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("scenario_prompt", sa.Text(), nullable=True),
        sa.Column("final_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'abandoned')",
            name="ck_roleplay_session_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_roleplay_sessions_user"),
        sa.ForeignKeyConstraint(["module_id"], ["coaching_modules.id"],
                                ondelete="RESTRICT",
                                name="fk_roleplay_sessions_module"),
        sa.ForeignKeyConstraint(["module_version_id"], ["module_versions.id"],
                                ondelete="RESTRICT",
                                name="fk_roleplay_sessions_version"),
        sa.ForeignKeyConstraint(["persona_id"], ["module_personas.id"],
                                ondelete="SET NULL",
                                name="fk_roleplay_sessions_persona"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="SET NULL",
                                name="fk_roleplay_sessions_tenant"),
    )
    op.create_index("idx_roleplay_sessions_user_created",
                    "roleplay_sessions", ["user_id", "created_at"])
    op.create_index("idx_roleplay_sessions_tenant_created",
                    "roleplay_sessions", ["tenant_id", "created_at"])
    op.create_index(
        "idx_roleplay_sessions_user_status",
        "roleplay_sessions", ["user_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ──────────────────────────────────────────────────────────────────────────
    # roleplay_messages  (append-only)
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "roleplay_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False,
                  comment="user | persona"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("emotion_detected", sa.String(50), nullable=True),
        sa.Column("coaching_note", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("session_id", "turn_number", "role",
                            name="uq_roleplay_message_turn_role"),
        sa.CheckConstraint("role IN ('user', 'persona')",
                           name="ck_roleplay_message_role"),
        sa.ForeignKeyConstraint(["session_id"], ["roleplay_sessions.id"],
                                ondelete="CASCADE",
                                name="fk_roleplay_messages_session"),
    )
    op.create_index("idx_roleplay_messages_session",
                    "roleplay_messages", ["session_id", "turn_number"])

    # ──────────────────────────────────────────────────────────────────────────
    # feedback_reports
    # XOR constraint: exactly one of session_id / roleplay_id must be set
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "feedback_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True,
                  comment="Set when linked to CoachingSession; NULL otherwise"),
        sa.Column("roleplay_id", UUID(as_uuid=True), nullable=True,
                  comment="Set when linked to RoleplaySession; NULL otherwise"),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("rubric_id", UUID(as_uuid=True), nullable=True,
                  comment="Rubric version used; audit trail"),
        sa.Column("scores", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("feedback_text", sa.Text(), nullable=False),
        sa.Column("strengths", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("improvements", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("recommendations", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("citations", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("knowledge_used", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("raw_ai_response", sa.Text(), nullable=True,
                  comment="Raw LLM output for debugging and re-processing"),
        sa.Column("user_rating", sa.Integer(), nullable=True,
                  comment="1-5 star rating from learner"),
        sa.Column("user_notes", sa.Text(), nullable=True,
                  comment="Free-text learner annotation on the feedback"),
        sa.Column("next_steps", sa.Text(), nullable=True,
                  comment="Actionable next steps from the AI coach"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # XOR: exactly one session reference must be set (FIX DB-02)
        sa.CheckConstraint(
            "(session_id IS NOT NULL AND roleplay_id IS NULL) OR "
            "(session_id IS NULL AND roleplay_id IS NOT NULL)",
            name="ck_feedback_report_session_xor",
        ),
        sa.CheckConstraint(
            "user_rating IS NULL OR (user_rating >= 1 AND user_rating <= 5)",
            name="ck_feedback_user_rating",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["coaching_sessions.id"],
                                ondelete="CASCADE",
                                name="fk_feedback_reports_session"),
        sa.ForeignKeyConstraint(["roleplay_id"], ["roleplay_sessions.id"],
                                ondelete="CASCADE",
                                name="fk_feedback_reports_roleplay"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_feedback_reports_user"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="SET NULL",
                                name="fk_feedback_reports_tenant"),
        sa.ForeignKeyConstraint(["rubric_id"], ["rubrics.id"],
                                ondelete="SET NULL",
                                name="fk_feedback_reports_rubric"),
    )
    op.create_index("idx_feedback_user_created",
                    "feedback_reports", ["user_id", "created_at"])
    op.create_index(
        "idx_feedback_session", "feedback_reports", ["session_id"],
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )
    op.create_index(
        "idx_feedback_roleplay", "feedback_reports", ["roleplay_id"],
        postgresql_where=sa.text("roleplay_id IS NOT NULL"),
    )
    op.create_index("idx_feedback_tenant_created",
                    "feedback_reports", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_table("feedback_reports")
    op.drop_table("roleplay_messages")
    op.drop_table("roleplay_sessions")
    op.drop_table("conversation_messages")
    op.drop_table("coaching_sessions")
