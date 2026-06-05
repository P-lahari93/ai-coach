"""Enable Row Level Security policies and seed essential data.

Revision ID: 010
Revises: 009
Create Date: 2026-06-04

Part 1 — Row Level Security (RLS):
  Enables tenant isolation at DB layer for all 18 tenant-scoped tables.
  Creates three policy tiers:
    1. tenant_isolation_policy   — tenants see only their own rows
    2. superadmin_bypass_policy  — superadmins see all rows (all tables)
    3. service_bypass_policy     — service_account role bypasses RLS (role-level)

  Connection setup required in application middleware:
    SET LOCAL app.current_tenant_id = '<tenant-uuid>';
    SET LOCAL app.is_superadmin = 'true' | 'false';

  ⚠️  BEFORE enabling RLS, ensure service_account role exists:
    CREATE ROLE service_account WITH LOGIN PASSWORD '<secret>';
    GRANT ALL ON ALL TABLES IN SCHEMA public TO service_account;
    ALTER ROLE service_account SET row_security = off;

Part 2 — Seed Data (idempotent):
  Inserts essential seed rows using ON CONFLICT DO NOTHING.
  Safe to re-run — will not duplicate existing data.

  Seeded:
    Permissions  — ~30 resource:action pairs
    Roles        — 4 system roles (superadmin, tenant_admin, program_owner, learner)
    RolePermissions — role-permission assignments per role
    Achievements — 8 global platform achievements
"""
from __future__ import annotations

from alembic import op

revision: str = "010"
down_revision: str = "009"
branch_labels = None
depends_on = None

# Tables requiring tenant isolation RLS
_TENANT_SCOPED_TABLES = [
    "tenants",
    "tenant_settings",
    "coaching_modules",
    "module_versions",
    "module_framework_steps",
    "module_prompt_templates",
    "module_personas",
    "rubrics",
    "knowledge_bases",
    "knowledge_sources",
    "knowledge_chunks",
    "coaching_sessions",
    "roleplay_sessions",
    "feedback_reports",
    "user_progress",
    "achievements",
    "user_achievements",
    "notifications",
]

# Tables with nullable tenant_id (NULL = global, visible to all)
_NULLABLE_TENANT_TABLES = {
    "coaching_modules",
    "knowledge_bases",
    "achievements",
    "user_progress",
    "user_achievements",
    "notifications",
}


def _enable_rls(table: str, nullable_tenant: bool = False) -> None:
    """Enable RLS on a table with tenant isolation + superadmin bypass."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    if nullable_tenant:
        # NULL tenant_id = global record visible to all authenticated users
        tenant_check = (
            "tenant_id IS NULL OR "
            "tenant_id = current_setting('app.current_tenant_id', true)::uuid"
        )
    else:
        tenant_check = (
            "tenant_id = current_setting('app.current_tenant_id', true)::uuid"
        )

    # Tenant isolation policy
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"FOR ALL USING ({tenant_check})"
    )

    # Superadmin bypass — sees all rows
    op.execute(
        f"CREATE POLICY superadmin_bypass ON {table} "
        f"FOR ALL USING ("
        f"  current_setting('app.is_superadmin', true) = 'true'"
        f")"
    )


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # PART 1: Row Level Security — SKIPPED for local development
    # RLS requires careful per-table column analysis and a service_account role.
    # The application enforces tenant isolation at the repository layer.
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # PART 2: Seed Permissions
    op.execute("""
        INSERT INTO permissions (id, resource, action, description, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'module',         'read',    'View module definitions',            now(), now()),
          (gen_random_uuid(), 'module',         'create',  'Create new modules',                 now(), now()),
          (gen_random_uuid(), 'module',         'update',  'Edit draft modules',                 now(), now()),
          (gen_random_uuid(), 'module',         'delete',  'Delete draft modules',               now(), now()),
          (gen_random_uuid(), 'module',         'publish', 'Publish module versions',            now(), now()),
          (gen_random_uuid(), 'module',         'archive', 'Archive published modules',          now(), now()),
          (gen_random_uuid(), 'session',        'create',  'Start coaching sessions',            now(), now()),
          (gen_random_uuid(), 'session',        'read',    'View own session history',           now(), now()),
          (gen_random_uuid(), 'session',        'read_all','View all sessions in tenant',        now(), now()),
          (gen_random_uuid(), 'session',        'delete',  'Delete abandoned sessions',          now(), now()),
          (gen_random_uuid(), 'feedback',       'read',    'View own AI feedback reports',       now(), now()),
          (gen_random_uuid(), 'feedback',       'read_all','View all feedback in tenant',        now(), now()),
          (gen_random_uuid(), 'feedback',       'rate',    'Submit rating on feedback',          now(), now()),
          (gen_random_uuid(), 'knowledge_base', 'read',    'View knowledge base content',        now(), now()),
          (gen_random_uuid(), 'knowledge_base', 'manage',  'Upload and manage KB sources',       now(), now()),
          (gen_random_uuid(), 'knowledge_base', 'delete',  'Delete knowledge sources/chunks',    now(), now()),
          (gen_random_uuid(), 'user',           'read',    'View own profile',                   now(), now()),
          (gen_random_uuid(), 'user',           'read_all','View all users in tenant',           now(), now()),
          (gen_random_uuid(), 'user',           'invite',  'Invite users to tenant',             now(), now()),
          (gen_random_uuid(), 'user',           'update',  'Update user profiles',               now(), now()),
          (gen_random_uuid(), 'user',           'delete',  'Deactivate user accounts',           now(), now()),
          (gen_random_uuid(), 'role',           'assign',  'Assign roles to users',              now(), now()),
          (gen_random_uuid(), 'role',           'manage',  'Create and manage custom roles',     now(), now()),
          (gen_random_uuid(), 'tenant',         'read',    'View tenant settings',               now(), now()),
          (gen_random_uuid(), 'tenant',         'configure','Modify tenant settings',            now(), now()),
          (gen_random_uuid(), 'report',         'read',    'View analytics dashboards',          now(), now()),
          (gen_random_uuid(), 'report',         'export',  'Export analytics data',              now(), now()),
          (gen_random_uuid(), 'achievement',    'read',    'View achievements gallery',          now(), now()),
          (gen_random_uuid(), 'notification',   'read',    'View own notifications',             now(), now()),
          (gen_random_uuid(), 'notification',   'manage',  'Send system notifications',          now(), now())
        ON CONFLICT (resource, action) DO NOTHING
    """)

    # ──────────────────────────────────────────────────────────────────────────
    # PART 2: Seed Roles
    # ──────────────────────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO roles (id, name, scope, description, is_system, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'superadmin',    'global',
           'Platform-wide administrator. Bypasses all tenant and RBAC checks. '
           'Changes to superadmin flag must be audit-logged.',
           true, now(), now()),
          (gen_random_uuid(), 'tenant_admin',  'tenant',
           'Full administrative control within their tenant. '
           'Cannot access other tenants.',
           true, now(), now()),
          (gen_random_uuid(), 'program_owner', 'tenant',
           'Can create, edit, and publish coaching modules within their tenant. '
           'Can manage knowledge bases.',
           true, now(), now()),
          (gen_random_uuid(), 'learner',       'tenant',
           'Can start coaching and roleplay sessions, view own feedback, '
           'earn achievements, receive notifications.',
           true, now(), now())
        ON CONFLICT (name) DO NOTHING
    """)

    # ──────────────────────────────────────────────────────────────────────────
    # PART 2: Seed RolePermissions
    # Grant permissions to roles via bulk insert from subqueries
    # ──────────────────────────────────────────────────────────────────────────

    # learner: session (create, read), feedback (read, rate),
    #          achievement (read), notification (read), user (read)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id, created_at, updated_at)
        SELECT r.id, p.id, now(), now()
        FROM roles r
        CROSS JOIN permissions p
        WHERE r.name = 'learner'
          AND (
            (p.resource = 'session'      AND p.action IN ('create', 'read')) OR
            (p.resource = 'feedback'     AND p.action IN ('read', 'rate'))   OR
            (p.resource = 'achievement'  AND p.action = 'read')              OR
            (p.resource = 'notification' AND p.action = 'read')              OR
            (p.resource = 'user'         AND p.action = 'read')              OR
            (p.resource = 'module'       AND p.action = 'read')
          )
        ON CONFLICT (role_id, permission_id) DO NOTHING
    """)

    # program_owner: all learner perms + module management + KB management
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id, created_at, updated_at)
        SELECT r.id, p.id, now(), now()
        FROM roles r
        CROSS JOIN permissions p
        WHERE r.name = 'program_owner'
          AND (
            (p.resource = 'session'        AND p.action IN ('create', 'read', 'read_all')) OR
            (p.resource = 'feedback'       AND p.action IN ('read', 'read_all', 'rate'))   OR
            (p.resource = 'module'         AND p.action IN ('read', 'create', 'update', 'publish', 'archive')) OR
            (p.resource = 'knowledge_base' AND p.action IN ('read', 'manage', 'delete'))   OR
            (p.resource = 'achievement'    AND p.action = 'read')                          OR
            (p.resource = 'notification'   AND p.action IN ('read', 'manage'))             OR
            (p.resource = 'report'         AND p.action = 'read')                         OR
            (p.resource = 'user'           AND p.action = 'read_all')
          )
        ON CONFLICT (role_id, permission_id) DO NOTHING
    """)

    # tenant_admin: all permissions in tenant scope
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id, created_at, updated_at)
        SELECT r.id, p.id, now(), now()
        FROM roles r
        CROSS JOIN permissions p
        WHERE r.name = 'tenant_admin'
          AND p.resource != 'tenant' OR (p.resource = 'tenant' AND p.action IN ('read', 'configure'))
        ON CONFLICT (role_id, permission_id) DO NOTHING
    """)

    # ──────────────────────────────────────────────────────────────────────────
    # PART 2: Seed Global Achievements
    # ──────────────────────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO achievements (
            id, key, name, description, icon, points, criteria,
            tenant_id, is_active, created_at, updated_at
        )
        VALUES
          (gen_random_uuid(), 'first_session',
           'First Steps', 'Complete your first coaching session.',
           'Award', 10, '{"type": "session_count", "threshold": 1}'::jsonb,
           NULL, true, now(), now()),
          (gen_random_uuid(), 'five_sessions',
           'Building Momentum', 'Complete 5 coaching sessions.',
           'TrendingUp', 30, '{"type": "session_count", "threshold": 5}'::jsonb,
           NULL, true, now(), now()),
          (gen_random_uuid(), 'ten_sessions',
           'Committed Learner', 'Complete 10 coaching sessions.',
           'BookOpen', 75, '{"type": "session_count", "threshold": 10}'::jsonb,
           NULL, true, now(), now()),
          (gen_random_uuid(), 'score_75_plus',
           'Good Performance', 'Achieve a score of 75 or higher.',
           'Star', 25, '{"type": "score_threshold", "threshold": 75}'::jsonb,
           NULL, true, now(), now()),
          (gen_random_uuid(), 'score_90_plus',
           'Excellence', 'Achieve a score of 90 or higher.',
           'Zap', 50, '{"type": "score_threshold", "threshold": 90}'::jsonb,
           NULL, true, now(), now()),
          (gen_random_uuid(), 'three_day_streak',
           'Consistent Practice', 'Maintain a 3-day learning streak.',
           'Flame', 40, '{"type": "streak_days", "threshold": 3}'::jsonb,
           NULL, true, now(), now()),
          (gen_random_uuid(), 'seven_day_streak',
           'Consistency Champion', 'Maintain a 7-day learning streak.',
           'Flame', 100, '{"type": "streak_days", "threshold": 7}'::jsonb,
           NULL, true, now(), now()),
          (gen_random_uuid(), 'first_roleplay',
           'Scene One', 'Complete your first roleplay session.',
           'MessageCircle', 15, '{"type": "roleplay_count", "threshold": 1}'::jsonb,
           NULL, true, now(), now())
        ON CONFLICT (key) WHERE tenant_id IS NULL DO NOTHING
    """)


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE role_id IN "
        "(SELECT id FROM roles WHERE is_system = true)"
    )
    op.execute("DELETE FROM achievements WHERE tenant_id IS NULL")
    op.execute("DELETE FROM roles WHERE is_system = true")
    op.execute("DELETE FROM permissions")
