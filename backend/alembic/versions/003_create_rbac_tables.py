"""Create RBAC and auth tables: tenant_settings, role_permissions,
user_roles, user_tenants, refresh_tokens.

Revision ID: 003
Revises: 002
Create Date: 2026-06-04

Tables created (Tier 1 — FK deps: tenants, users, roles, permissions):
  tenant_settings
  role_permissions
  user_roles
  user_tenants
  refresh_tokens
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

from alembic import op

revision: str = "003"
down_revision: str = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # tenant_settings  (1:1 extension of tenants)
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "tenant_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, unique=True,
                  index=True),
        sa.Column("settings", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_tenant_settings_tenant"),
    )

    # ──────────────────────────────────────────────────────────────────────────
    # role_permissions  (M:M join: roles ↔ permissions)
    # Composite PK — no surrogate UUID needed
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "role_permissions",
        sa.Column("role_id", UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("role_id", "permission_id",
                                name="pk_role_permissions"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"],
                                ondelete="CASCADE",
                                name="fk_role_permissions_role"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"],
                                ondelete="CASCADE",
                                name="fk_role_permissions_permission"),
    )

    # ──────────────────────────────────────────────────────────────────────────
    # user_roles  (assigns a Role to a User, optionally scoped to a Tenant)
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "user_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True,
                  comment="NULL = global role assignment (e.g. superadmin)"),
        sa.Column("granted_by", UUID(as_uuid=True), nullable=True,
                  comment="User ID who granted this role assignment"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_user_roles_user"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"],
                                ondelete="CASCADE",
                                name="fk_user_roles_role"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_user_roles_tenant"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"],
                                ondelete="SET NULL",
                                name="fk_user_roles_granted_by"),
    )
    op.create_index("idx_user_roles_user_tenant",
                    "user_roles", ["user_id", "tenant_id"])
    op.create_index("idx_user_roles_role", "user_roles", ["role_id"])
    # NULL-safe uniqueness: one (user, role) per global scope
    op.execute(
        "CREATE UNIQUE INDEX uq_user_roles_global "
        "ON user_roles (user_id, role_id) "
        "WHERE tenant_id IS NULL"
    )
    # NULL-safe uniqueness: one (user, role, tenant) per tenant scope
    op.execute(
        "CREATE UNIQUE INDEX uq_user_roles_tenant "
        "ON user_roles (user_id, role_id, tenant_id) "
        "WHERE tenant_id IS NOT NULL"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # user_tenants  (membership join: users ↔ tenants)
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "user_tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_user_tenants"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_user_tenants_user"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="CASCADE",
                                name="fk_user_tenants_tenant"),
    )
    op.create_index("idx_user_tenants_user", "user_tenants", ["user_id"])
    op.create_index("idx_user_tenants_tenant", "user_tenants", ["tenant_id"])

    # ──────────────────────────────────────────────────────────────────────────
    # refresh_tokens
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True,
                  index=True,
                  comment="SHA-256 hex digest of the raw opaque token"),
        sa.Column("device_hint", sa.String(255), nullable=True,
                  comment="User-agent summary, e.g. 'Chrome 124 / macOS'"),
        sa.Column("ip_address", INET(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="CASCADE",
                                name="fk_refresh_tokens_user"),
    )
    op.create_index("idx_refresh_tokens_user_expires",
                    "refresh_tokens", ["user_id", "expires_at"])
    op.create_index(
        "idx_refresh_tokens_active",
        "refresh_tokens", ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("user_tenants")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("tenant_settings")
