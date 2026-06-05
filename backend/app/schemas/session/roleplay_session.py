"""
Roleplay session schemas.

Covers:
  - RoleplaySessionBase / Create / Update   — request lifecycle
  - RoleplayTurnRequest / RoleplayTurnResponse — per-turn interaction
  - RoleplayMessageSchema                   — individual message in the roleplay
  - RoleplaySessionResponse                 — standard API response
  - RoleplaySessionSummary                  — lightweight list-view projection
  - RoleplaySessionDetail                   — full response with messages + feedback

RoleplaySession status values (enforced by DB CHECK):
    active | paused | completed | abandoned

RoleplayMessage role values (enforced by DB CHECK):
    user | persona
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Shared literals ───────────────────────────────────────────────────────────

RoleplaySessionStatus = Literal["active", "paused", "completed", "abandoned"]
RoleplayMessageRole = Literal["user", "persona"]


# ── RoleplayMessageSchema ─────────────────────────────────────────────────────

class RoleplayMessageSchema(BaseModel):
    """
    Represents a single turn message in a roleplay conversation.

    Append-only; never updated after creation.
    turn_number is 1-based and unique per (session, role).

    coaching_note is hidden from the learner during the roleplay and only
    revealed in the post-session FeedbackReport to preserve immersion.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    session_id: UUID
    turn_number: int = Field(..., ge=1, description="1-based turn counter within this session")
    role: RoleplayMessageRole = Field(..., description="user | persona")
    content: str
    emotion_detected: str | None = Field(
        default=None,
        max_length=50,
        description="Emotion tag on persona messages, e.g. 'frustrated', 'curious'",
    )
    coaching_note: str | None = Field(
        default=None,
        description="Hidden inline coaching hint; revealed in post-session report only",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="latency_ms, model_name, retrieval_ids, intent_detected",
        alias="metadata_",
    )
    created_at: datetime


# ── Base ──────────────────────────────────────────────────────────────────────

class RoleplaySessionBase(BaseModel):
    """
    Fields shared across create and response schemas.

    module_version_id is pinned at creation time; it never changes.
    persona_id is optional — NULL means the engine uses the module's default persona.
    tenant_id is optional for global/platform sessions.
    """

    module_id: UUID = Field(..., description="The coaching module this session belongs to")
    module_version_id: UUID = Field(
        ...,
        description="Pinned module version at session creation; immutable",
    )
    persona_id: UUID | None = Field(
        default=None,
        description="Module persona adopted by the AI; NULL = module default persona",
    )
    tenant_id: UUID | None = Field(
        default=None,
        description="Tenant context; NULL for platform-level sessions",
    )


# ── Request schemas ───────────────────────────────────────────────────────────

class RoleplaySessionCreate(BaseModel):
    """
    POST /sessions/roleplay

    Client sends module_id and optionally persona_id, scenario_prompt, tenant_id.
    The backend auto-resolves the current published module version.
    module_version_id is set server-side — never accepted from the client.
    """

    module_id: UUID = Field(..., description="The coaching module this session belongs to")
    persona_id: UUID | None = Field(
        default=None,
        description="Module persona adopted by the AI; NULL = module default persona",
    )
    tenant_id: UUID | None = Field(
        default=None,
        description="Tenant context; NULL for platform-level sessions",
    )
    scenario_prompt: str | None = Field(
        default=None,
        description="Setup text shown to the learner before the roleplay starts",
    )


class RoleplaySessionUpdate(BaseModel):
    """
    PATCH /sessions/roleplay/{session_id}

    Only active/paused sessions can be updated.
    Partial update — all fields optional.
    Status transitions (complete, abandon, pause, resume) use dedicated endpoints.
    """

    status: Literal["paused", "active"] | None = Field(
        default=None,
        description="Pause or resume an active/paused session",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Partial engine-state update; merged into the existing context bag. "
            "Only used by the AI engine — not accepted from learner-facing clients."
        ),
    )


class RoleplayTurnRequest(BaseModel):
    """
    POST /sessions/roleplay/{session_id}/turn

    Submits the learner's next message to an active roleplay session.
    The AI engine generates a persona response and appends both messages.
    """

    content: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="The learner's message for this turn",
    )

    @field_validator("content")
    @classmethod
    def strip_content(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


# ── Response schemas ──────────────────────────────────────────────────────────

class RoleplayTurnResponse(BaseModel):
    """
    Response to POST /sessions/roleplay/{session_id}/turn.

    Returns the persona's reply plus updated session state.
    coaching_note is always None during the roleplay (hidden until report).
    """

    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    turn_number: int = Field(..., ge=1)
    persona_content: str = Field(
        ...,
        description="The AI persona's response message for this turn",
    )
    emotion_detected: str | None = Field(
        default=None,
        description="Emotion tag on the persona's response, if detected",
    )
    session_status: RoleplaySessionStatus
    turn_count: int = Field(..., ge=0, description="Updated total turn count")


class RoleplaySessionResponse(RoleplaySessionBase):
    """
    Standard roleplay session response.

    Returned by POST, PATCH, and GET /sessions/roleplay/{session_id}.
    Does not include conversation messages or embedded feedback
    (use RoleplaySessionDetail for the full payload).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    status: RoleplaySessionStatus
    scenario_prompt: str | None = None
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Mutable engine-state bag; updated each turn",
    )
    final_score: Decimal | None = Field(
        default=None,
        description="Weighted rubric score; 0.00–100.00. Null until completed.",
    )
    turn_count: int = Field(..., ge=0, description="Total user turns in this session")
    completed_at: datetime | None = None
    version: int = Field(..., description="Optimistic-lock version counter")
    created_at: datetime
    updated_at: datetime


class RoleplaySessionSummary(BaseModel):
    """
    Lightweight projection for list views and dashboard widgets.

    Omits context and nested objects to keep list responses lean.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    module_id: UUID
    module_version_id: UUID
    persona_id: UUID | None
    tenant_id: UUID | None
    status: RoleplaySessionStatus
    final_score: Decimal | None
    turn_count: int
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RoleplaySessionDetail(RoleplaySessionResponse):
    """
    Full roleplay session response with message history and feedback.

    Used by the session review UI, feedback display page, and the AI engine's
    context-loading path.

    messages:    Ordered by turn_number ascending; loaded via selectinload().
    feedback:    Embedded feedback report if the session is completed; None otherwise.
    """

    messages: list[RoleplayMessageSchema] = Field(
        default_factory=list,
        description="Full roleplay message history ordered by turn_number",
    )
    feedback: "RoleplayFeedbackEmbedded | None" = Field(
        default=None,
        description="Embedded feedback summary; populated after session completion",
    )


# ── Forward reference for RoleplayFeedbackEmbedded ────────────────────────────

class RoleplayFeedbackEmbedded(BaseModel):
    """
    Minimal feedback report embedded inside RoleplaySessionDetail.

    Carries only the fields needed for the session review screen.
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
RoleplaySessionDetail.model_rebuild()
