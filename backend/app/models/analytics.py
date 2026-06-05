"""
Analytics domain models: AnalyticsEvent, AuditLog, APIUsageLog, AIGeneration.

Architecture decisions:
────────────────────────

  AnalyticsEvent
    High-volume, append-only behavioral event log. Every user action
    of interest (session started, feedback viewed, module opened, etc.)
    produces one row here.

    Design choices for scale (100k+ users):
      - No FK constraint on session_id_ref — loose UUID reference.
        Avoids FK overhead on the write-hot path and allows the table
        to be partitioned or archived independently of sessions.
      - No FK constraint on entity_id — same reason; entity_type
        describes what it refers to (e.g. "coaching_session").
      - occurred_at separate from created_at: occurred_at records when
        the event happened (client-side or server-side event time),
        created_at records when it was persisted.
      - Partitioning: partition by occurred_at (monthly range) in
        production. Declared in Alembic migration, not here.
      - user_id FK uses SET NULL so orphaned events are retained for
        aggregate analytics even after account deletion.

  AuditLog
    Compliance and security trail for every mutating operation.
    Every CREATE / UPDATE / DELETE / PUBLISH / LOGIN passes through
    the service layer which writes one AuditLog row.

    before_state / after_state JSONB:
      Snapshots of the resource before and after the change.
      Kept trimmed to relevant fields — not a full row dump.
      NULL for CREATE (no before) and DELETE (no after needed).

    ip_address / user_agent:
      Stored for security investigations. Subject to data-retention
      policy (auto-purge after N days via scheduled job).

  APIUsageLog
    Per-request HTTP log for SLA monitoring, per-tenant billing,
    and abuse detection. High-volume append-only table.

    Performance strategy:
      - Written asynchronously (fire-and-forget background task)
        to avoid adding latency to API responses.
      - Partition by created_at (monthly) in production.
      - No FK on user_id — loose reference, same rationale as
        AnalyticsEvent.

  AIGeneration
    Telemetry for every LLM call made by the coaching/roleplay engine.
    Enables: cost tracking per tenant/user, quality debugging,
    per-tenant token quota enforcement, latency monitoring.

    total_tokens:
      Stored as a regular column populated by the AI engine
      (prompt_tokens + completion_tokens). Using a computed/generated
      column would require a migration change later — easier to
      compute in the service and store the result.

    session_id / session_type:
      Loose references (no FK). A generation can belong to either
      a CoachingSession or a RoleplaySession. session_type
      ('coaching' | 'roleplay') disambiguates which table to join
      if needed.

    was_cached:
      True when Ollama served from its KV cache or when the prompt
      builder detected an identical prompt hash in Redis (future).
      Tracked for cost analysis.

Circular import strategy:
  All cross-model references (User, Tenant) are under TYPE_CHECKING.
  None of the analytics tables are referenced back from User or Tenant
  (no back_populates needed — analytics are write-only from the
  domain's perspective, read only by the analytics service).

Performance strategy for 100k+ users:
  - Monthly range partitioning on occurred_at / created_at
    (declared in migrations, not ORM)
  - Partial indexes on common filter patterns
  - No foreign key constraints on high-volume reference columns
    (session_id, entity_id) — prevents FK lock contention
  - All reads go through the analytics repository which uses
    aggregate queries, never full table scans
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
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
# AnalyticsEvent
# ─────────────────────────────────────────────────────────────────────────────

class AnalyticsEvent(UUIDPrimaryKeyMixin, Base):
    """
    Immutable behavioral event record.

    Inherits UUIDPrimaryKeyMixin only — no TimestampMixin because
    occurred_at serves as the canonical event timestamp and
    created_at is added explicitly here with a different semantic:
      occurred_at — when the event happened (may be client-provided)
      created_at  — when the row was written to the DB

    TABLE PARTITIONING:
      This table uses monthly range partitioning by occurred_at.
      Parent table created by ORM. Child partitions created by
      explicit migration DDL. See create_analytics_tables_partitioned.py.

      Partition structure:
        analytics_events (parent, PARTITION BY RANGE occurred_at)
          └── analytics_events_YYYY_MM (one per month)

      Retention: partitions older than 13 months are dropped by
      scheduled job (see docs/MIGRATION_BLOCKING_ISSUES_ANALYSIS.md).

    event_type: high-level category, e.g.:
        session_started | session_completed | feedback_viewed |
        module_opened   | achievement_earned | kb_uploaded |
        roleplay_turn   | login | logout | page_view

    event_name: optional sub-type, e.g. 'sbi_feedback' for
        a session_started event on that specific module.

    properties JSONB:
        Arbitrary event-specific payload. Examples:
          session_started:  {"module_key": "sbi_feedback", "version": 3}
          feedback_viewed:  {"score": 87.5, "knowledge_used": true}
          page_view:        {"path": "/dashboard", "referrer": "/login"}

    entity_type / entity_id:
        Loose reference to the primary entity for this event.
        No FK — allows partitioning and archiving independently.
        e.g. entity_type="coaching_session", entity_id=<uuid>

    session_id_ref:
        Loose reference to the active coaching or roleplay session
        at the time of the event. Useful for funnel analysis.
    """

    __tablename__ = "analytics_events"
    __table_args__ = (
        Index(
            "idx_analytics_user_type_occurred",
            "user_id",
            "event_type",
            "occurred_at",
        ),
        Index("idx_analytics_tenant_occurred", "tenant_id", "occurred_at"),
        Index("idx_analytics_event_type_occurred", "event_type", "occurred_at"),
        Index("idx_analytics_entity", "entity_type", "entity_id"),
        # Monthly partition key index — migration declares the partition itself
        Index("idx_analytics_occurred_at", "occurred_at"),
        # NOTE: Table partitioning by RANGE (occurred_at) is implemented
        # via explicit DDL in the Alembic migration file. SQLAlchemy ORM
        # does not natively support PARTITION BY declarations in __table_args__.
        # See migration: create_analytics_tables_partitioned.py
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL for anonymous events; retained on account deletion",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="High-level category: session_started, feedback_viewed, ...",
    )
    event_name: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Optional sub-type or label for the event",
    )
    properties: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Arbitrary event-specific payload",
    )
    entity_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="e.g. 'coaching_session', 'module', 'knowledge_base'",
    )
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Loose UUID reference — no FK to allow partitioning",
    )
    session_id_ref: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Loose reference to active session at event time",
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        comment="When the event happened (may differ from DB insert time)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        comment="When the row was written to the DB",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="select",
    )
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<AnalyticsEvent type={self.event_type!r} "
            f"user={self.user_id} occurred={self.occurred_at}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AuditLog
# ─────────────────────────────────────────────────────────────────────────────

class AuditLog(UUIDPrimaryKeyMixin, Base):
    """
    Compliance and security audit trail.

    Append-only — rows are never updated or deleted during normal
    operations. Retention policy applied by a scheduled archival job.

    TABLE PARTITIONING:
      Monthly range partitioning by created_at. Unlike analytics_events
      and api_usage_logs (which drop old partitions), audit_logs
      partitions are RETAINED for compliance (7+ years in most
      jurisdictions). Old partitions should be archived to cold storage,
      not dropped.

      See create_analytics_tables_partitioned.py for DDL.

    action values:
        CREATE | UPDATE | DELETE | PUBLISH | ARCHIVE |
        LOGIN  | LOGOUT | PASSWORD_CHANGE | ROLE_GRANT |
        ROLE_REVOKE | KB_UPLOAD | KB_DELETE | SUPERADMIN_GRANT

    entity_type: the resource being acted on, e.g.
        "coaching_module" | "user" | "knowledge_base" |
        "module_version"  | "tenant_settings"

    before_state / after_state JSONB:
        Trimmed snapshots of the relevant fields before and after
        the mutation. Service layer decides which fields to include.
        NULL for CREATE (no before) and DELETE (after often omitted).

    actor_user_id:
        The authenticated user who performed the action.
        Uses SET NULL so audit records survive account deletion —
        essential for compliance (you need the trail even if the
        user account is gone).

    ip_address:
        Stored for security investigations.
        Subject to GDPR data-retention policy.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_actor_created", "actor_user_id", "created_at"),
        Index("idx_audit_entity", "entity_type", "entity_id", "created_at"),
        Index("idx_audit_tenant_created", "tenant_id", "created_at"),
        Index("idx_audit_action_created", "action", "created_at"),
        # Support common security query: all actions by a user in a tenant
        Index(
            "idx_audit_actor_tenant_created",
            "actor_user_id",
            "tenant_id",
            "created_at",
        ),
        # NOTE: Table partitioning by RANGE (created_at) implemented
        # via explicit DDL in Alembic migration. See:
        # create_analytics_tables_partitioned.py
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Who performed the action; SET NULL on account deletion",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="CREATE|UPDATE|DELETE|PUBLISH|LOGIN|ROLE_GRANT|...",
    )
    entity_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Resource type, e.g. 'coaching_module', 'user'",
    )
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="UUID of the affected resource; NULL for login events",
    )
    before_state: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Trimmed state snapshot before mutation; NULL for CREATE",
    )
    after_state: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Trimmed state snapshot after mutation; NULL for DELETE",
    )
    ip_address: Mapped[Optional[str]] = mapped_column(
        INET,
        nullable=True,
        comment="Request IP; subject to data-retention policy",
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Request User-Agent header",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    actor: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[actor_user_id],
        lazy="select",
    )
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog action={self.action!r} "
            f"entity={self.entity_type!r}:{self.entity_id} "
            f"actor={self.actor_user_id}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# APIUsageLog
# ─────────────────────────────────────────────────────────────────────────────

class APIUsageLog(UUIDPrimaryKeyMixin, Base):
    """
    Per-request HTTP usage record.

    High-volume append-only table — every API request (or a sampled
    subset in very high traffic) produces one row.

    TABLE PARTITIONING:
      Monthly range partitioning by created_at. Parent table created
      by ORM. Child partitions created by explicit migration DDL.
      See create_analytics_tables_partitioned.py.

    Write strategy:
        Written asynchronously via a background task (fire-and-forget)
        to avoid adding DB write latency to the request path.
        Acceptable trade-off: a small % of records may be lost on
        process crash; this is fine for usage telemetry.

    Partitioning:
        Partition by created_at (monthly range) in production.
        A 100k-user platform generates ~50-200M rows/month at typical
        API call rates. Without partitioning, indexes degrade.

    user_id / tenant_id:
        Stored without FK constraints — loose references. Avoids
        FK lock contention on the hot write path and allows the
        table to be archived independently.

    status_code: SmallInt (2 bytes) — HTTP status is always 1xx-5xx
    latency_ms: Integer — milliseconds; sufficient precision for SLA

    request_size_bytes / response_size_bytes:
        Captured for bandwidth billing and to detect abnormally
        large payloads (potential abuse indicator).
    """

    __tablename__ = "api_usage_logs"
    __table_args__ = (
        Index("idx_api_usage_user_created", "user_id", "created_at"),
        Index("idx_api_usage_tenant_created", "tenant_id", "created_at"),
        Index("idx_api_usage_endpoint_created", "endpoint", "created_at"),
        Index("idx_api_usage_status_created", "status_code", "created_at"),
        # Latency SLA monitoring: slow requests
        Index(
            "idx_api_usage_slow",
            "latency_ms",
            "created_at",
            postgresql_where=text("latency_ms > 1000"),
        ),
        # Error rate monitoring: 4xx/5xx
        Index(
            "idx_api_usage_errors",
            "tenant_id",
            "status_code",
            "created_at",
            postgresql_where=text("status_code >= 400"),
        ),
        # NOTE: Table partitioning by RANGE (created_at) implemented
        # via explicit DDL in Alembic migration. See:
        # create_analytics_tables_partitioned.py
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Loose reference — no FK for write performance",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Loose reference — for per-tenant usage billing",
    )
    endpoint: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Route path, e.g. '/v1/sessions' (no query string)",
    )
    method: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="HTTP method: GET|POST|PUT|PATCH|DELETE",
    )
    status_code: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="HTTP response status code",
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Request-to-response latency in milliseconds",
    )
    request_size_bytes: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    response_size_bytes: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    ip_address: Mapped[Optional[str]] = mapped_column(
        INET,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        comment="When the request was completed",
    )

    def __repr__(self) -> str:
        return (
            f"<APIUsageLog {self.method} {self.endpoint} "
            f"{self.status_code} {self.latency_ms}ms>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AIGeneration
# ─────────────────────────────────────────────────────────────────────────────

class AIGeneration(UUIDPrimaryKeyMixin, Base):
    """
    Telemetry record for every LLM call made by the AI engine.

    One row per inference call. Enables:
      - Cost tracking: sum(total_tokens) per tenant per period
      - Quota enforcement: rolling token budget per tenant
      - Quality debugging: correlate low scores with high latency
      - Model comparison: compare qwen3:4b vs other models
      - Cache effectiveness: track was_cached rate

    session_id / session_type:
        Loose references (no FK constraints). session_type
        disambiguates which session table to join:
          'coaching'  -> coaching_sessions
          'roleplay'  -> roleplay_sessions
          'embedding' -> no session (used for RAG embedding calls)
          NULL        -> standalone generation (e.g. admin preview)

    generation_type:
        What the generation was used for:
          feedback       — coaching feedback pass
          roleplay_turn  — single roleplay response
          scoring        — rubric evaluation
          recommendation — improvement suggestions
          embedding      — vector embedding (different cost model)

    total_tokens:
        Populated by the AI engine as prompt_tokens + completion_tokens.
        Stored explicitly (not computed) for simple SUM() aggregation.

    error_message:
        Non-null when the generation failed (timeout, OOM, etc.).
        Used to track model reliability.
    """

    __tablename__ = "ai_generations"
    __table_args__ = (
        Index("idx_ai_gen_user_created", "user_id", "created_at"),
        Index("idx_ai_gen_tenant_created", "tenant_id", "created_at"),
        Index("idx_ai_gen_session", "session_id", "created_at"),
        Index("idx_ai_gen_model_created", "model_name", "created_at"),
        Index("idx_ai_gen_type_created", "generation_type", "created_at"),
        # Token cost aggregation index
        Index("idx_ai_gen_tenant_tokens", "tenant_id", "total_tokens"),
        CheckConstraint(
            "generation_type IN "
            "('feedback', 'roleplay_turn', 'scoring', "
            "'recommendation', 'embedding')",
            name="ck_ai_generation_type",
        ),
        CheckConstraint(
            "session_type IN ('coaching', 'roleplay', 'embedding') "
            "OR session_type IS NULL",
            name="ck_ai_generation_session_type",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Loose reference to coaching or roleplay session",
    )
    session_type: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="coaching | roleplay | embedding | NULL",
    )
    generation_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="feedback|roleplay_turn|scoring|recommendation|embedding",
    )
    model_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Ollama model identifier, e.g. 'qwen3:4b'",
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="prompt_tokens + completion_tokens; set by AI engine",
    )
    response_time_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Wall-clock time from request to first token (ms)",
    )
    was_cached: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="True if served from Ollama KV cache or prompt cache",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Non-null when generation failed",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="select",
    )
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def succeeded(self) -> bool:
        return self.error_message is None

    def __repr__(self) -> str:
        return (
            f"<AIGeneration type={self.generation_type!r} "
            f"model={self.model_name!r} tokens={self.total_tokens}>"
        )
