"""
Gamification / achievement schemas.

Covers:
  - AchievementCriteriaSchema  — typed representation of criteria JSONB
  - AchievementResponse        — full achievement definition
  - AchievementSummary         — lightweight list/gallery projection
  - UserAchievementResponse    — award record with embedded achievement

Achievement criteria JSONB types (from gamification engine):
    session_count    — complete N sessions
    score_threshold  — achieve score >= threshold
    streak_days      — maintain an N-day streak
    module_complete  — complete a specific module (100%)
    feedback_count   — view N feedback reports
    roleplay_count   — complete N roleplay sessions

UserAchievement.metadata_ JSONB context at award time:
    {"session_id": "uuid", "score": 91.5,
     "streak_days_at_award": 8, "module_key": "sbi_feedback"}
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Achievement criteria types ────────────────────────────────────────────────

AchievementCriteriaType = Literal[
    "session_count",
    "score_threshold",
    "streak_days",
    "module_complete",
    "feedback_count",
    "roleplay_count",
]


# ── AchievementCriteriaSchema ─────────────────────────────────────────────────

class AchievementCriteriaSchema(BaseModel):
    """
    Typed representation of Achievement.criteria JSONB.

    Deserialised from the DB JSONB column by the service layer before
    returning the response; not stored directly — the JSONB is the
    source of truth.

    module_key: optional restriction to a specific module.
    score_min:  optional minimum score floor for score_threshold type.
    """

    type: AchievementCriteriaType = Field(
        ...,
        description="Evaluation rule type used by the gamification engine",
    )
    threshold: int = Field(
        ...,
        ge=1,
        description=(
            "Target value: session count, score value, streak days, etc. "
            "Interpreted by the gamification engine based on `type`."
        ),
    )
    module_key: str | None = Field(
        default=None,
        description="Optional: restrict criteria evaluation to this module key",
    )
    score_min: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Optional minimum score floor; used with score_threshold type",
    )


# ── AchievementResponse ───────────────────────────────────────────────────────

class AchievementResponse(BaseModel):
    """
    Full achievement definition response.

    Returned by GET /achievements/{id} and the gamification service
    when returning earned achievement details.

    criteria_parsed: typed, validated representation of the JSONB criteria.
    Populated by the service layer; may be None if the JSONB does not
    conform to AchievementCriteriaSchema (legacy/custom types).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str = Field(..., description="Machine-readable identifier, e.g. 'first_session'")
    name: str = Field(..., description="Display name shown in the achievements gallery")
    description: str
    icon: str | None = Field(
        default=None,
        description="Icon identifier for the UI, e.g. 'Trophy'",
    )
    points: int = Field(
        ...,
        ge=0,
        description="Gamification points awarded when this achievement is earned",
    )
    criteria: dict[str, Any] = Field(
        ...,
        description="Raw JSONB criteria dict; use criteria_parsed for typed access",
    )
    criteria_parsed: AchievementCriteriaSchema | None = Field(
        default=None,
        description=(
            "Typed, validated criteria; None if JSONB schema is non-standard. "
            "Populated by the service layer."
        ),
    )
    tenant_id: UUID | None = Field(
        default=None,
        description="None = global platform achievement available to all tenants",
    )
    is_active: bool = Field(
        ...,
        description="Inactive achievements are hidden but existing awards are retained",
    )
    created_at: datetime
    updated_at: datetime


# ── AchievementSummary ────────────────────────────────────────────────────────

class AchievementSummary(BaseModel):
    """
    Lightweight projection for the achievements gallery and badge lists.

    Omits raw criteria JSONB and timestamps to keep list payloads lean.
    earned: set by the service layer based on whether the requesting
    user has a UserAchievement record for this achievement.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    description: str
    icon: str | None
    points: int
    tenant_id: UUID | None
    is_active: bool
    earned: bool = Field(
        default=False,
        description="True if the requesting user has earned this achievement",
    )
    earned_at: datetime | None = Field(
        default=None,
        description="When the requesting user earned it; None if not yet earned",
    )


# ── UserAchievementResponse ───────────────────────────────────────────────────

class UserAchievementResponse(BaseModel):
    """
    Award record: a user earned a specific achievement.

    achievement: always embedded (UserAchievement.achievement uses
    lazy='selectin' — loaded automatically with the award record).

    award_metadata: typed representation of the metadata_ JSONB column
    capturing context at award time (session_id, score, streak, etc.).
    Passed through as a raw dict so clients can display "how you earned
    this" detail without the schema being overly prescriptive.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    user_id: UUID
    achievement_id: UUID
    tenant_id: UUID | None = None
    awarded_at: datetime = Field(
        ...,
        description="When the gamification engine awarded this achievement",
    )
    award_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Context at award time: session_id, score, streak_days_at_award, "
            "module_key. Sourced from the metadata_ JSONB column."
        ),
        alias="metadata_",
    )
    achievement: AchievementSummary = Field(
        ...,
        description="Embedded achievement definition (always loaded)",
    )
