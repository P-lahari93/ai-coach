"""Actually enable Row Level Security (fixes 010's skipped/broken attempt).

Revision ID: 011
Revises: 010
Create Date: 2026-07-07

Migration 010 defined RLS policy logic but never called it — it was
skipped because `tenants` uses `id` as its own identity column, not
`tenant_id`, which broke the generic policy generator.

Scope of this migration:
  Only tables belonging to the session / knowledge-base / module domains
  are enforced here — the ones already audited and fixed at the service
  layer (CoachingSessionService, RoleplaySessionService, FeedbackService,
  CoachingModuleService, KnowledgeBaseService/KnowledgeSourceService).

  Deliberately EXCLUDED from this pass: role_permissions, user_roles,
  user_tenants, refresh_tokens. These are read during the login flow
  itself, before any tenant context exists — forcing RLS on them here,
  without tracing every auth code path first, risks breaking login.
  That is a scoped decision, not an oversight; revisit separately.

Column reality (verified against migrations 002-008, not assumed):
  - tenants: self-referential, uses `id`, not `tenant_id`.
  - coaching_sessions, roleplay_sessions, feedback_reports,
    knowledge_bases, knowledge_chunks, coaching_modules, tenant_settings,
    user_progress, achievements, user_achievements, notifications:
    all have their own `tenant_id` column — flat policy works.
  - knowledge_sources: NO tenant_id column — tenancy inherited via kb_id.
  - module_versions: NO tenant_id column — tenancy inherited via module_id.
  - module_framework_steps, module_prompt_templates, module_personas,
    rubrics: NO tenant_id column — tenancy inherited via
    module_version_id -> module_versions.module_id.
  - conversation_messages: NO tenant_id column — inherited via
    session_id -> coaching_sessions.tenant_id.
  - roleplay_messages: NO tenant_id column — inherited via
    session_id -> roleplay_sessions.tenant_id.
"""
from __future__ import annotations

from alembic import op

revision: str = "011"
down_revision: str = "010"
branch_labels = None
depends_on = None

# Tables with their OWN tenant_id column — flat policy applies directly.
_FLAT_TENANT_TABLES = [
    "tenant_settings",
    "coaching_modules",
    "knowledge_bases",
    "knowledge_chunks",
    "coaching_sessions",
    "roleplay_sessions",
    "feedback_reports",
    "user_progress",
    "achievements",
    "user_achievements",
    "notifications",
]

# Tables where tenant_id is nullable (NULL = global/platform-wide row,
# visible to everyone regardless of GUC).
_NULLABLE_TENANT_TABLES = {
    "coaching_modules",
    "knowledge_bases",
    "achievements",
    "user_progress",
    "user_achievements",
    "notifications",
}


def _enable_rls_flat(table: str, tenant_column: str, nullable_tenant: bool = False) -> None:
    """Enable RLS on a table that has its own tenant column."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    if nullable_tenant:
        tenant_check = (
            f"{tenant_column} IS NULL OR "
            f"{tenant_column} = current_setting('app.current_tenant_id', true)::uuid"
        )
    else:
        tenant_check = (
            f"{tenant_column} = current_setting('app.current_tenant_id', true)::uuid"
        )

    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"FOR ALL USING ({tenant_check})"
    )
    op.execute(
        f"CREATE POLICY superadmin_bypass ON {table} "
        f"FOR ALL USING ("
        f"  current_setting('app.is_superadmin', true) = 'true'"
        f")"
    )


def _enable_rls_subquery(table: str, using_clause: str) -> None:
    """Enable RLS on a table whose tenancy is inherited via a FK subquery."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL USING ({using_clause})"
    )
    op.execute(
        f"CREATE POLICY superadmin_bypass ON {table} "
        f"FOR ALL USING (current_setting('app.is_superadmin', true) = 'true')"
    )


def upgrade() -> None:
    # tenants: self-referential — its own identity column IS the tenant column
    _enable_rls_flat("tenants", tenant_column="id")

    for table in _FLAT_TENANT_TABLES:
        _enable_rls_flat(
            table,
            tenant_column="tenant_id",
            nullable_tenant=table in _NULLABLE_TENANT_TABLES,
        )

    # knowledge_sources — tenancy inherited via kb_id
    _enable_rls_subquery(
        "knowledge_sources",
        "kb_id IN ("
        "  SELECT id FROM knowledge_bases "
        "  WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid"
        ")",
    )

    # module_versions — tenancy inherited via module_id.
    # coaching_modules.tenant_id is nullable (global modules), so a NULL
    # tenant_id there makes the version visible regardless of GUC.
    _enable_rls_subquery(
        "module_versions",
        "module_id IN ("
        "  SELECT id FROM coaching_modules "
        "  WHERE tenant_id IS NULL "
        "     OR tenant_id = current_setting('app.current_tenant_id', true)::uuid"
        ")",
    )

    # module_framework_steps, module_prompt_templates, module_personas,
    # rubrics — all keyed by module_version_id, two hops from tenant.
    _module_version_child_clause = (
        "module_version_id IN ("
        "  SELECT mv.id FROM module_versions mv"
        "  JOIN coaching_modules cm ON cm.id = mv.module_id"
        "  WHERE cm.tenant_id IS NULL"
        "     OR cm.tenant_id = current_setting('app.current_tenant_id', true)::uuid"
        ")"
    )
    for table in (
        "module_framework_steps",
        "module_prompt_templates",
        "module_personas",
        "rubrics",
    ):
        _enable_rls_subquery(table, _module_version_child_clause)

    # conversation_messages — tenancy inherited via session_id
    _enable_rls_subquery(
        "conversation_messages",
        "session_id IN ("
        "  SELECT id FROM coaching_sessions "
        "  WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid"
        ")",
    )

    # roleplay_messages — tenancy inherited via session_id
    _enable_rls_subquery(
        "roleplay_messages",
        "session_id IN ("
        "  SELECT id FROM roleplay_sessions "
        "  WHERE tenant_id = current_setting('app.current_tenant_id', true)::uuid"
        ")",
    )


def downgrade() -> None:
    all_tables = (
        ["tenants"]
        + _FLAT_TENANT_TABLES
        + [
            "knowledge_sources",
            "module_versions",
            "module_framework_steps",
            "module_prompt_templates",
            "module_personas",
            "rubrics",
            "conversation_messages",
            "roleplay_messages",
        ]
    )
    for table in all_tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS superadmin_bypass ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")