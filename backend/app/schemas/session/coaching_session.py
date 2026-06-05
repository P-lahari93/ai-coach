"""
Coaching session schemas.

Covers:
  - CoachingSessionBase / Create / Update   — request lifecycle
  - SessionCompleteRequest                  — complete a session with final intake
  - IntakeDataSchema                        — validated intake form payload
  - ConversationMessageSchema               — individual message in the session chat
  - CoachingSessionResponse                 — standard API response
  - CoachingSessionSummary                  — lightweight list-view projection
  - CoachingSessionDetail                   — full response with messages + feedback

CoachingSession status values (enforced by DB CHECK):
    in_progress | completed | abandoned
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Shared status literal ─────────────────────────────────────────────────────

CoachingSessionStatus = Literal["in_progress", "completed", "abandoned"]
ConversationRole = Literal["user", "assistant", "system"]


# ── IntakeDataSchema ──────────────────────────────────────────────────────────

class IntakeDataSchema(BaseModel):
    """
    Validated intake form submission.

    intake_data is an open dict keyed by ModuleVersion.intake_schema[].field_key.
    The exact keys are module-specific (e.g. 'situation', 'behaviour', 'impact'
    for SBI), so we validate that the dict is non-empty and that every key and
    value is a non-empty string.

    Strict structural validation against the module's intake_schema definition
    happens in the service layer (not here), where the module version is known.
    """

    intake_data: dict[str, str] = Field(
        ...,
        description=(
            "Keyed by ModuleVersion.intake_schema[].field_key. "
            "Example: {'situation': '...', 'behaviour': '...', 'impact': '...'}"
        ),
        examples=[
            {
                "situation": "In our Monday team meeting with six people present.",
                "behaviour": "You interrupted me three times while I was speaking.",
                "impact": "I felt unable to finish my point and the team lost context.",
            }
        ],
    )

    @field_validator("intake_data")
    @classmethod
    def validate_intake_fields(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("intake_data must contain at least one field")
        for key, value in v.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"intake_data key must be a non-empty string, got: {key!r}")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"intake_data['{key}'] must be a non-empty string"
                )
        return v


# ── ConversationMessageSchema ─────────────────────────────────────────────────

class ConversationMessageSchema(BaseModel):
    """
    Represents a single message in the coaching session conversation.

    Append-only; never updated after creation.
    message_index is 0-based and unique per session.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    role: ConversationRole = Field(
        ...,
        description="user | assistant | system",
    )
    content: str
    message_index: int = Field(..., ge=0)
    token_count: int | None = Field(
        default=None,
        ge=0,
        description="Token count for cost tracking; populated by the AI engine",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="latency_ms, model_name, cached, retrieval_ids",
        alias="metadata_",
    )
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── Base ──────────────────────────────────────────────────────────────────────

class CoachingSessionBase(BaseModel):
    """
    Fields shared across create and response schemas.

    module_version_id is pinned at creation time; it never changes.
    tenant_id is optional for global/platform sessions.
    """

    module_id: UUID = Field(..., description="The coaching module this session belongs to")
    module_version_id: UUID = Field(
        ...,
        description="Pinned module version at session creation; immutable",
    )
    tenant_id: UUID | None = Field(
        default=None,
        description="Tenant context; NULL for platform-level sessions",
    )


# ── Request schemas ───────────────────────────────────────────────────────────

class CoachingSessionCreate(BaseModel):
    """
    POST /sessions/coaching

    Client sends only module_id (and optionally tenant_id + initial intake_data).
    The backend auto-resolves the current published module version.
    module_version_id is set server-side — never accepted from the client.
    """

    module_id: UUID = Field(..., description="The coaching module to start a session for")
    tenant_id: UUID | None = Field(
        default=None,
        description="Tenant context; NULL for platform-level sessions",
    )
    intake_data: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional initial intake submission. "
            "If empty, must be provided in SessionCompleteRequest."
        ),
    )


class CoachingSessionUpdate(BaseModel):
    """
    PATCH /sessions/coaching/{session_id}

    Only in_progress sessions can be updated.
    Partial update — all fields optional.
    Status transitions (complete, abandon) use dedicated endpoints.
    """

    intake_data: dict[str, str] | None = Field(
        default=None,
        description="Partial or full replacement of the intake form data",
    )


class SessionCompleteRequest(IntakeDataSchema):
    """
    POST /sessions/coaching/{session_id}/complete

    Submits the final intake_data and triggers AI feedback generation.
    Inherits intake_data validation from IntakeDataSchema.
    The session status transitions: in_progress → completed.
    """

    pass


# ── Response schemas ──────────────────────────────────────────────────────────

class CoachingSessionResponse(CoachingSessionBase):
    """
    Standard coaching session response.

    Returned by POST, PATCH, and GET /sessions/coaching/{session_id}.
    Does not include conversation messages or embedded feedback
    (use CoachingSessionDetail for the full payload).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    status: CoachingSessionStatus
    intake_data: dict[str, Any]
    final_score: Decimal | None = Field(
        default=None,
        description="Weighted rubric score; 0.00–100.00. Null until completed.",
    )
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Session duration in seconds; populated at completion",
    )
    completed_at: datetime | None = None
    version: int = Field(..., description="Optimistic-lock version counter")
    created_at: datetime
    updated_at: datetime


class CoachingSessionSummary(BaseModel):
    """
    Lightweight projection for list views and dashboard widgets.

    Omits intake_data and nested objects to keep list responses lean.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    module_id: UUID
    module_version_id: UUID
    tenant_id: UUID | None
    status: CoachingSessionStatus
    final_score: Decimal | None
    duration_seconds: int | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CoachingSessionDetail(CoachingSessionResponse):
    """
    Full coaching session response with conversation history and feedback.

    Used by the session review UI, feedback display page, and the AI engine's
    context-loading path.

    messages:    Ordered by message_index ascending; loaded via selectinload().
    feedback:    Embedded feedback report if the session is completed; None otherwise.
    """

    messages: list[ConversationMessageSchema] = Field(
        default_factory=list,
        description="Full conversation history ordered by message_index",
    )
    feedback: "FeedbackReportEmbedded | None" = Field(
        default=None,
        description="Embedded feedback summary; populated after session completion",
    )


# ── Forward reference for FeedbackReportEmbedded (resolved at bottom) ─────────

class FeedbackReportEmbedded(BaseModel):
    """
    Minimal feedback report embedded inside CoachingSessionDetail.

    Avoids a full circular import with feedback_report.py by carrying
    only the fields needed for the session review screen.
    The full FeedbackReportResponse lives in feedback_report.py.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    overall_score: Decimal
    feedback_text: str
    strengths: list[str]
    improvements: list[str]
    knowledge_used: bool
    user_rating: int | None = Field(default=None, ge=1, le=5)
    created_at: datetime


# Resolve forward references
CoachingSessionDetail.model_rebuild()
