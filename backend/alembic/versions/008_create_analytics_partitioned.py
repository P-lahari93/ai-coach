"""Create analytics and audit tables with monthly range partitioning.

Revision ID: 008
Revises: 007
Create Date: 2026-06-04

Tables created (Tier 6 — loose FKs for write performance):
  analytics_events   — PARTITIONED BY RANGE (occurred_at), monthly
  audit_logs         — PARTITIONED BY RANGE (created_at), monthly (RETAIN)
  api_usage_logs     — PARTITIONED BY RANGE (created_at), monthly
  ai_generations     — NOT partitioned (moderate volume)

PARTITIONING STRATEGY:
  analytics_events and api_usage_logs: drop partitions > 13 months old.
  audit_logs: RETAIN all partitions for compliance (7+ year requirement).
              Archive to cold storage instead of dropping.

  13 forward-looking monthly child partitions created per table
  (June 2026 through June 2027), plus a DEFAULT partition for safety.

  Partition automation: a scheduled pg_cron job or application-level
  job must create next month's partition on the 25th of each month.
  See docs/MIGRATION_ARCHITECTURE_REVIEW.md Section 6 for pg_cron SQL.

IMPORTANT: Due to PostgreSQL limitations, SQLAlchemy ORM creates
the parent table structure. Partitioning REQUIRES the table to be
declared with PARTITION BY RANGE at creation time. We achieve this
by dropping the ORM-created table and recreating it with partitioning.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

from alembic import op

revision: str = "008"
down_revision: str = "007"
branch_labels = None
depends_on = None

# Monthly partitions to create: (year, month_num) tuples
# 13 months forward from June 2026 through June 2027
_ANALYTICS_MONTHS = [
    (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10),
    (2026, 11), (2026, 12),
    (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5), (2027, 6),
]


def _next_month(year: int, month: int) -> tuple[int, int]:
    """Return (year, month) of the next calendar month."""
    return (year + 1, 1) if month == 12 else (year, month + 1)


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # analytics_events  (PARTITIONED BY RANGE occurred_at)
    # ──────────────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE analytics_events (
            id          uuid        NOT NULL DEFAULT gen_random_uuid(),
            user_id     uuid        REFERENCES users(id) ON DELETE SET NULL,
            tenant_id   uuid        REFERENCES tenants(id) ON DELETE SET NULL,
            event_type  varchar(100) NOT NULL,
            event_name  varchar(200),
            properties  jsonb        NOT NULL DEFAULT '{}'::jsonb,
            entity_type varchar(50),
            entity_id   uuid,
            session_id_ref uuid,
            occurred_at timestamptz  NOT NULL DEFAULT now(),
            created_at  timestamptz  NOT NULL DEFAULT now(),
            PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at)
    """)

    # Indexes on parent (automatically inherited by children)
    op.create_index("idx_analytics_user_type_occurred",
                    "analytics_events",
                    ["user_id", "event_type", "occurred_at"])
    op.create_index("idx_analytics_tenant_occurred",
                    "analytics_events", ["tenant_id", "occurred_at"])
    op.create_index("idx_analytics_event_type_occurred",
                    "analytics_events", ["event_type", "occurred_at"])
    op.create_index("idx_analytics_entity",
                    "analytics_events", ["entity_type", "entity_id"])
    op.create_index("idx_analytics_occurred_at",
                    "analytics_events", ["occurred_at"])

    # Monthly child partitions
    for year, month in _ANALYTICS_MONTHS:
        ny, nm = _next_month(year, month)
        op.execute(
            f"CREATE TABLE analytics_events_{year}_{month:02d} "
            f"PARTITION OF analytics_events "
            f"FOR VALUES FROM ('{year}-{month:02d}-01') "
            f"TO ('{ny}-{nm:02d}-01')"
        )

    # Default partition catches any rows outside declared ranges
    op.execute(
        "CREATE TABLE analytics_events_default "
        "PARTITION OF analytics_events DEFAULT"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # audit_logs  (PARTITIONED BY RANGE created_at, COMPLIANCE RETAIN)
    # ──────────────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE audit_logs (
            id              uuid        NOT NULL DEFAULT gen_random_uuid(),
            actor_user_id   uuid        REFERENCES users(id) ON DELETE SET NULL,
            tenant_id       uuid        REFERENCES tenants(id) ON DELETE SET NULL,
            action          varchar(100) NOT NULL,
            entity_type     varchar(100) NOT NULL,
            entity_id       uuid,
            before_state    jsonb,
            after_state     jsonb,
            ip_address      inet,
            user_agent      text,
            created_at      timestamptz  NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)

    op.create_index("idx_audit_actor_created",
                    "audit_logs", ["actor_user_id", "created_at"])
    op.create_index("idx_audit_entity",
                    "audit_logs", ["entity_type", "entity_id", "created_at"])
    op.create_index("idx_audit_tenant_created",
                    "audit_logs", ["tenant_id", "created_at"])
    op.create_index("idx_audit_action_created",
                    "audit_logs", ["action", "created_at"])
    op.create_index("idx_audit_actor_tenant_created",
                    "audit_logs",
                    ["actor_user_id", "tenant_id", "created_at"])

    for year, month in _ANALYTICS_MONTHS:
        ny, nm = _next_month(year, month)
        op.execute(
            f"CREATE TABLE audit_logs_{year}_{month:02d} "
            f"PARTITION OF audit_logs "
            f"FOR VALUES FROM ('{year}-{month:02d}-01') "
            f"TO ('{ny}-{nm:02d}-01')"
        )
    op.execute("CREATE TABLE audit_logs_default PARTITION OF audit_logs DEFAULT")

    # ──────────────────────────────────────────────────────────────────────────
    # api_usage_logs  (PARTITIONED BY RANGE created_at)
    # No FK constraints — loose references for write performance
    # ──────────────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE api_usage_logs (
            id                    uuid        NOT NULL DEFAULT gen_random_uuid(),
            user_id               uuid,
            tenant_id             uuid,
            endpoint              varchar(255) NOT NULL,
            method                varchar(10)  NOT NULL,
            status_code           smallint     NOT NULL,
            latency_ms            integer      NOT NULL,
            request_size_bytes    integer,
            response_size_bytes   integer,
            ip_address            inet,
            created_at            timestamptz  NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)

    op.create_index("idx_api_usage_user_created",
                    "api_usage_logs", ["user_id", "created_at"])
    op.create_index("idx_api_usage_tenant_created",
                    "api_usage_logs", ["tenant_id", "created_at"])
    op.create_index("idx_api_usage_endpoint_created",
                    "api_usage_logs", ["endpoint", "created_at"])
    op.create_index("idx_api_usage_status_created",
                    "api_usage_logs", ["status_code", "created_at"])
    op.create_index(
        "idx_api_usage_slow", "api_usage_logs", ["latency_ms", "created_at"],
        postgresql_where=sa.text("latency_ms > 1000"),
    )
    op.create_index(
        "idx_api_usage_errors", "api_usage_logs",
        ["tenant_id", "status_code", "created_at"],
        postgresql_where=sa.text("status_code >= 400"),
    )

    for year, month in _ANALYTICS_MONTHS:
        ny, nm = _next_month(year, month)
        op.execute(
            f"CREATE TABLE api_usage_logs_{year}_{month:02d} "
            f"PARTITION OF api_usage_logs "
            f"FOR VALUES FROM ('{year}-{month:02d}-01') "
            f"TO ('{ny}-{nm:02d}-01')"
        )
    op.execute(
        "CREATE TABLE api_usage_logs_default "
        "PARTITION OF api_usage_logs DEFAULT"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # ai_generations  (NOT partitioned — moderate volume)
    # ──────────────────────────────────────────────────────────────────────────
    op.create_table(
        "ai_generations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True,
                  comment="Loose reference — no FK"),
        sa.Column("session_type", sa.String(20), nullable=True,
                  comment="coaching | roleplay | embedding | NULL"),
        sa.Column("generation_type", sa.String(30), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("completion_tokens", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("total_tokens", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("was_cached", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "generation_type IN "
            "('feedback', 'roleplay_turn', 'scoring', "
            "'recommendation', 'embedding')",
            name="ck_ai_generation_type",
        ),
        sa.CheckConstraint(
            "session_type IN ('coaching', 'roleplay', 'embedding') "
            "OR session_type IS NULL",
            name="ck_ai_generation_session_type",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="SET NULL",
                                name="fk_ai_generations_user"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                ondelete="SET NULL",
                                name="fk_ai_generations_tenant"),
    )
    op.create_index("idx_ai_gen_user_created",
                    "ai_generations", ["user_id", "created_at"])
    op.create_index("idx_ai_gen_tenant_created",
                    "ai_generations", ["tenant_id", "created_at"])
    op.create_index("idx_ai_gen_session",
                    "ai_generations", ["session_id", "created_at"])
    op.create_index("idx_ai_gen_model_created",
                    "ai_generations", ["model_name", "created_at"])
    op.create_index("idx_ai_gen_type_created",
                    "ai_generations", ["generation_type", "created_at"])
    op.create_index("idx_ai_gen_tenant_tokens",
                    "ai_generations", ["tenant_id", "total_tokens"])


def downgrade() -> None:
    op.drop_table("ai_generations")

    # Drop all partition children first, then parent
    for year, month in _ANALYTICS_MONTHS:
        op.execute(
            f"DROP TABLE IF EXISTS api_usage_logs_{year}_{month:02d}"
        )
    op.execute("DROP TABLE IF EXISTS api_usage_logs_default")
    op.execute("DROP TABLE IF EXISTS api_usage_logs")

    for year, month in _ANALYTICS_MONTHS:
        op.execute(f"DROP TABLE IF EXISTS audit_logs_{year}_{month:02d}")
    op.execute("DROP TABLE IF EXISTS audit_logs_default")
    op.execute("DROP TABLE IF EXISTS audit_logs")

    for year, month in _ANALYTICS_MONTHS:
        op.execute(
            f"DROP TABLE IF EXISTS analytics_events_{year}_{month:02d}"
        )
    op.execute("DROP TABLE IF EXISTS analytics_events_default")
    op.execute("DROP TABLE IF EXISTS analytics_events")
