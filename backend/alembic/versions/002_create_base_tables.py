"""Create base tables: tenants, users, permissions, roles.

Revision ID: 002
Revises: 001
Create Date: 2026-06-04

Tables created (Tier 0 — no FK dependencies):
  tenants
  users
  permissions
  roles
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

from alembic import op

revision: str = "002"
down_revision: str = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # tenants
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("plan", sa.String(50), nullable=False,
                  server_default=sa.text("'free'"),
                  comment="free | starter | pro | enterprise"),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("max_users", sa.Integer(), nullable=False,
                  server_default=sa.text("10")),
        sa.Column("metadata", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb"),
                  comment="Extensible bag: billing_id, logo_url, etc."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_index("idx_tenants_slug", "tenants", ["slug"])

    # ──────────────────────────────────────────────────────────────────────────
    # users
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False, unique=True,
                  comment="Case-insensitive via lower() index in migration"),
        sa.Column("password_hash", sa.String(255), nullable=False,
                  comment="bcrypt hash — never store raw password"),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False,
                  server_default=sa.text("false"),
                  comment="Bypasses all RBAC. Changes must be audit-logged."),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial unique index: one active account per email (handles soft-delete)
    op.create_index(
        "idx_users_email_active",
        "users", ["email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # Case-insensitive email lookup via functional index
    op.execute(
        "CREATE INDEX idx_users_email_lower ON users (lower(email))"
    )
    op.create_index(
        "idx_users_active", "users", ["is_active", "deleted_at"]
    )

    # ──────────────────────────────────────────────────────────────────────────
    # permissions
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "permissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("resource", sa.String(100), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("resource", "action",
                            name="uq_permissions_resource_action"),
    )
    op.create_index("idx_permissions_resource", "permissions", ["resource"])

    # ──────────────────────────────────────────────────────────────────────────
    # roles
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("scope", sa.String(20), nullable=False,
                  server_default=sa.text("'tenant'"),
                  comment="global | tenant"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False,
                  server_default=sa.text("false"),
                  comment="System roles cannot be deleted or renamed."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_index("idx_roles_scope", "roles", ["scope"])


def downgrade() -> None:
    op.drop_table("roles")
    op.drop_table("permissions")
    op.drop_table("users")
    op.drop_table("tenants")
