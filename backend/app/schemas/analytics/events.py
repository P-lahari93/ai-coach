"""
Analytics event schemas.

Covers:
  - TrackEventRequest        — inbound event ingestion payload
  - AnalyticsEventResponse   — full event record response
  - AuditLogResponse         — compliance audit trail record
  - AIGenerationResponse     — LLM call telemetry record

AnalyticsEvent design notes:
  - occurred_at vs created_at: occurred_at is the canonical event time
    (may be client-provided for offline sync); created_at is the DB
    insert time.
  - entity_type / entity_id: loose polymorphic references — no FK,
    allows partitioning/archiving independently of the referenced tables.
  - session_id_ref: loose reference to the active session at event time.

AuditLog design notes:
  - before_state / after_state JSONB: trimmed field snapshots managed
    by the service layer; never a full row dump.
  - ip_address: subject to GDPR data-retention policy; may be None
    in sanitised responses after the retention window.

AIGeneration design notes:
  - session_type discriminator: 'coaching' | 'roleplay' | 'embedding'
  - generation_type: 'feedback' | 'roleplay_turn' | 'scoring' |
    'recommendation' | 'embedding'
  - raw_ai_response is intentionally excluded from all responses.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Shared literals ───────────────────────────────────────────────────────────

GenerationType = Literal[
    "feedback",
    "roleplay_turn",
    "scoring",
    "recommendation",
    "embedding",
]

AISessionType = Literal["coaching", "roleplay", "embedding"]


# ── TrackEventRequest ─────────────────────────────────────────────────────────

class TrackEventRequest(BaseModel):
    """
    POST /analytics/events

    Client-submitted behavioral event. Validated and written to the
    analytics_events table by the analytics service.

    occurred_at: optional client-provided event time. If omitted the
    server sets it to now(). Must not be more than 24 hours in the past
    (prevents event replay attacks and backfill abuse).

    properties: arbitrary event-specific payload. Max 50 keys enforced
    to prevent abuse; individual values must be JSON-serialisable.

    entity_type / entity_id: optional loose reference to the primary
    entity for this event (e.g. 'coaching_session' + UUID).

    session_id_ref: optional loose reference to the active session
    at the time of the event, used for funnel analysis.
    """

    event_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description=(
            "High-level category: session_started, feedback_viewed, "
            "module_opened, achievement_earned, page_view, etc."
        ),
    )
    event_name: str | None = Field(
        default=None,
        max_length=200,
        description="Optional sub-type or label, e.g. 'sbi_feedback'",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary event-specific payload (max 50 keys)",
    )
    entity_type: str | None = Field(
        default=None,
        max_length=50,
        description="Type of the primary entity, e.g. 'coaching_session'",
    )
    entity_id: UUID | None = Field(
        default=None,
        description="UUID of the primary entity for this event",
    )
    session_id_ref: UUID | None = Field(
        default=None,
        description="Loose reference to active session at event time",
    )
    occurred_at: datetime | None = Field(
        default=None,
        description=(
            "Client-provided event time; server uses now() if omitted. "
            "Must not be more than 24 hours in the past."
        ),
    )

    @field_validator("properties")
    @classmethod
    def validate_properties_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 50:
            raise ValueError("properties must not contain more than 50 keys")
        return v

    @field_validator("event_type")
    @classmethod
    def normalise_event_type(cls, v: str) -> str:
        return v.strip().lower()


# ── AnalyticsEventResponse ────────────────────────────────────────────────────

class AnalyticsEventResponse(BaseModel):
    """
    Full analytics event record.

    Returned by the analytics service when an event is retrieved
    by ID or in aggregate query results.

    user_id may be None for anonymous events; it is retained on
    account deletion (SET NULL FK) for aggregate analytics integrity.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None = Field(
        default=None,
        description="None for anonymous events; retained on account deletion",
    )
    tenant_id: UUID | None = None
    event_type: str
    event_name: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    entity_type: str | None = None
    entity_id: UUID | None = None
    session_id_ref: UUID | None = None
    occurred_at: datetime = Field(
        ...,
        description="When the event happened (may differ from created_at)",
    )
    created_at: datetime = Field(
        ...,
        description="When the row was written to the DB",
    )


# ── AuditLogResponse ──────────────────────────────────────────────────────────

class AuditLogResponse(BaseModel):
    """
    Compliance audit log entry.

    Returned by GET /audit-logs for platform admins and tenant admins
    with audit read permissions.

    ip_address: may be None in sanitised responses after the GDPR
    data-retention window has elapsed.

    before_state / after_state: trimmed JSON snapshots. None for
    CREATE (no before_state) and often None after_state for DELETE.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID | None = Field(
        default=None,
        description="Who performed the action; None if account was deleted",
    )
    tenant_id: UUID | None = None
    action: str = Field(
        ...,
        description=(
            "CREATE | UPDATE | DELETE | PUBLISH | ARCHIVE | "
            "LOGIN | LOGOUT | PASSWORD_CHANGE | ROLE_GRANT | ROLE_REVOKE | "
            "KB_UPLOAD | KB_DELETE | SUPERADMIN_GRANT"
        ),
    )
    entity_type: str = Field(
        ...,
        description="Resource type, e.g. 'coaching_module', 'user'",
    )
    entity_id: UUID | None = Field(
        default=None,
        description="UUID of the affected resource; None for login events",
    )
    before_state: dict[str, Any] | None = Field(
        default=None,
        description="Trimmed field snapshot before mutation; None for CREATE",
    )
    after_state: dict[str, Any] | None = Field(
        default=None,
        description="Trimmed field snapshot after mutation; None for DELETE",
    )
    ip_address: str | None = Field(
        default=None,
        description="Request IP; may be None after GDPR retention window",
    )
    user_agent: str | None = None
    created_at: datetime


# ── AIGenerationResponse ──────────────────────────────────────────────────────

class AIGenerationResponse(BaseModel):
    """
    LLM call telemetry record.

    Returned by the analytics service for cost tracking, quota
    monitoring, and quality debugging dashboards.

    raw_ai_response is intentionally excluded — it is internal
    debugging data and must never be exposed via the API.

    succeeded: True when error_message is None.
    """

    model_config = ConfigDict(
        from_attributes=True,
        # model_name is a domain field (Ollama model identifier), not a
        # Pydantic internal — suppress the protected namespace warning.
        protected_namespaces=(),
    )

    id: UUID
    user_id: UUID | None = None
    tenant_id: UUID | None = None
    session_id: UUID | None = Field(
        default=None,
        description="Loose reference to coaching or roleplay session",
    )
    session_type: AISessionType | None = Field(
        default=None,
        description="coaching | roleplay | embedding; None for standalone calls",
    )
    generation_type: GenerationType = Field(
        ...,
        description="feedback | roleplay_turn | scoring | recommendation | embedding",
    )
    model_name: str = Field(
        ...,
        description="Ollama model identifier, e.g. 'qwen3:4b'",
    )
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
    total_tokens: int = Field(
        ...,
        ge=0,
        description="prompt_tokens + completion_tokens",
    )
    response_time_ms: int | None = Field(
        default=None,
        ge=0,
        description="Wall-clock time from request to first token (ms)",
    )
    was_cached: bool = Field(
        ...,
        description="True if served from Ollama KV cache or prompt cache",
    )
    succeeded: bool = Field(
        ...,
        description="True when error_message is None",
    )
    error_message: str | None = Field(
        default=None,
        description="Non-None when generation failed",
    )
    created_at: datetime
