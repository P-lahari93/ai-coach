"""Create module domain tables.

Revision ID: 004
Revises: 003
Create Date: 2026-06-04

Tables created (Tier 2 — FK deps: tenants, users):
  coaching_modules
  module_versions
  module_framework_steps
  module_prompt_templates
  module_personas
  rubrics
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "004"
down_revision: str = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # coaching_modules
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "coaching_modules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False,
                  comment="Machine-readable slug, e.g. 'sbi_feedback'"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("blurb", sa.Text(), nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True, index=True,
                  comment="NULL = global platform module"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'draft'"),
                  comment="draft | published | archived"),
        sa.Column("gamification_overrides", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_coaching_module_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_coaching_modules_tenant"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"],
                                ondelete="SET NULL",
                                name="fk_coaching_modules_created_by"),
    )
    op.create_index(
        "idx_modules_tenant_status", "coaching_modules",
        ["tenant_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_modules_key_active", "coaching_modules", ["key"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # NULL-safe unique key per scope
    op.execute(
        "CREATE UNIQUE INDEX uq_module_key_global "
        "ON coaching_modules (key) "
        "WHERE tenant_id IS NULL AND deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_module_key_tenant "
        "ON coaching_modules (key, tenant_id) "
        "WHERE tenant_id IS NOT NULL AND deleted_at IS NULL"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # module_versions
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "module_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("module_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False,
                  server_default=sa.text("1"),
                  comment="Monotonically increasing per module_id"),
        sa.Column("is_current", sa.Boolean(), nullable=False,
                  server_default=sa.text("false"),
                  comment="Only one version per module may be True"),
        sa.Column("framework_name", sa.String(100), nullable=False),
        sa.Column("intake_schema", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("scoring_rubric", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("module_id", "version_number",
                            name="uq_module_version_number"),
        sa.ForeignKeyConstraint(["module_id"], ["coaching_modules.id"],
                                ondelete="CASCADE",
                                name="fk_module_versions_module"),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"],
                                ondelete="SET NULL",
                                name="fk_module_versions_published_by"),
    )
    op.create_index("idx_module_versions_current",
                    "module_versions", ["module_id", "is_current"])
    op.create_index("idx_module_versions_module",
                    "module_versions", ["module_id"])
    # Exactly one is_current=true per module (FIX DB-01)
    op.execute(
        "CREATE UNIQUE INDEX uq_module_one_current_version "
        "ON module_versions (module_id) "
        "WHERE is_current = true"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # module_framework_steps
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "module_framework_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("module_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False,
                  comment="0-based display order within this version"),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("scoring_hints", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("module_version_id", "step_order",
                            name="uq_framework_step_order"),
        sa.ForeignKeyConstraint(["module_version_id"], ["module_versions.id"],
                                ondelete="CASCADE",
                                name="fk_framework_steps_version"),
    )
    op.create_index("idx_framework_steps_version",
                    "module_framework_steps", ["module_version_id"])

    # ──────────────────────────────────────────────────────────────────────────
    # module_prompt_templates
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "module_prompt_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("module_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("template_type", sa.String(50), nullable=False,
                  comment="coaching | roleplay_system | roleplay_turn | scoring"),
        sa.Column("template_body", sa.Text(), nullable=False),
        sa.Column("variables", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("module_version_id", "template_type",
                            name="uq_prompt_template_type_per_version"),
        sa.CheckConstraint(
            "template_type IN "
            "('coaching', 'roleplay_system', 'roleplay_turn', 'scoring')",
            name="ck_prompt_template_type",
        ),
        sa.ForeignKeyConstraint(["module_version_id"], ["module_versions.id"],
                                ondelete="CASCADE",
                                name="fk_prompt_templates_version"),
    )
    op.create_index("idx_prompt_templates_version",
                    "module_prompt_templates", ["module_version_id"])

    # ──────────────────────────────────────────────────────────────────────────
    # module_personas
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "module_personas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("module_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("persona_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("traits", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_default", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["module_version_id"], ["module_versions.id"],
                                ondelete="CASCADE",
                                name="fk_personas_version"),
    )
    op.create_index("idx_personas_version",
                    "module_personas", ["module_version_id"])
    op.create_index(
        "idx_personas_version_default", "module_personas",
        ["module_version_id"],
        postgresql_where=sa.text("is_default = true"),
    )
    # Exactly one default persona per version
    op.execute(
        "CREATE UNIQUE INDEX uq_persona_one_default_per_version "
        "ON module_personas (module_version_id) "
        "WHERE is_default = true"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # rubrics
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "rubrics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("module_version_id", UUID(as_uuid=True), nullable=False,
                  unique=True, comment="1:1 with ModuleVersion"),
        sa.Column("dimensions", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="List of {name, weight, band_descriptors} objects"),
        sa.Column("content_version", sa.Integer(), nullable=False,
                  server_default=sa.text("1"),
                  comment="Incremented on wording changes"),
        sa.Column("description", sa.Text(), nullable=True,
                  comment="Human-readable rubric description for admin UI"),
        sa.Column("change_notes", sa.Text(), nullable=True,
                  comment="Notes on what changed in this content_version"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("module_version_id",
                            name="uq_rubric_per_module_version"),
        sa.ForeignKeyConstraint(["module_version_id"], ["module_versions.id"],
                                ondelete="CASCADE",
                                name="fk_rubrics_version"),
    )
    op.create_index("idx_rubrics_version", "rubrics", ["module_version_id"])


def downgrade() -> None:
    op.drop_table("rubrics")
    op.drop_table("module_personas")
    op.drop_table("module_prompt_templates")
    op.drop_table("module_framework_steps")
    op.drop_table("module_versions")
    op.drop_table("coaching_modules")
