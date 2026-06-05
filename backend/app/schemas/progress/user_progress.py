"""
User progress schemas.

Covers:
  - UserProgressResponse    — full pre-aggregated progress record
  - UserProgressSummary     — lightweight list/dashboard projection
  - UserProgressDetail      — progress + embedded recent achievements
  - LeaderboardEntry        — single row in a per-tenant leaderboard

UserProgress is a pre-aggregated record per (user × module × tenant).
It is updated via UPSERT after every session completion — never
returned from a direct session query.

completion_percent:   0.00–100.00 (clamped by SessionService)
best_score / avg:     Numeric(5,2) — may be None until first session completes
streak_days:          Consecutive days with at least one completed session;
                      reset by the daily streak maintenance job.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── UserProgressResponse ──────────────────────────────────────────────────────

class UserProgressResponse(BaseModel):
    """
    Full pre-aggregated progress record for a single user × module × tenant.

    Returned by GET /progress/{user_id}/{module_id} and the progress
    service after a session completion UPSERT.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    module_id: UUID
    tenant_id: UUID | None = None

    sessions_completed: int = Field(..., ge=0, description="Count of completed sessions")
    sessions_total: int = Field(
        ...,
        ge=0,
        description="All sessions started (completed + abandoned)",
    )
    completion_percent: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("100"),
        description="0.00–100.00; clamped by SessionService",
    )
    best_score: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("100"),
        description="Highest final_score across all completed sessions",
    )
    average_score: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("100"),
        description="Running average of final_score; None until first completion",
    )
    total_score: Decimal = Field(
        ...,
        ge=Decimal("0"),
        description="Running sum of all final_scores; used to compute average",
    )
    streak_days: int = Field(
        ...,
        ge=0,
        description="Consecutive days with at least one completed session",
    )
    last_activity_at: datetime | None = Field(
        default=None,
        description="Timestamp of the most recent session completion for this module",
    )
    created_at: datetime
    updated_at: datetime


# ── UserProgressSummary ───────────────────────────────────────────────────────

class UserProgressSummary(BaseModel):
    """
    Lightweight projection for list views, dashboard widgets, and
    module-card progress bars.

    Omits total_score (internal accounting field) to keep the payload lean.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    module_id: UUID
    tenant_id: UUID | None = None
    sessions_completed: int
    completion_percent: Decimal
    best_score: Decimal | None
    average_score: Decimal | None
    streak_days: int
    last_activity_at: datetime | None
    updated_at: datetime


# ── UserProgressDetail ────────────────────────────────────────────────────────

class UserProgressDetail(UserProgressResponse):
    """
    Full progress record with embedded recent achievements for the
    learner profile and module detail page.

    recent_achievements: up to 5 most recently earned achievements
    for this module scope, populated by the service layer via
    a separate UserAchievement query.

    total_achievements: total count of achievements earned in this
    module/tenant scope, for the badge count indicator.
    """

    recent_achievements: list[Any] = Field(
        default_factory=list,
        description=(
            "Up to 5 most recently earned UserAchievementResponse objects "
            "for this module scope; populated by the service layer"
        ),
    )
    total_achievements: int = Field(
        default=0,
        ge=0,
        description="Total achievements earned in this module/tenant scope",
    )


# ── LeaderboardEntry ──────────────────────────────────────────────────────────

class LeaderboardEntry(BaseModel):
    """
    Single row in a per-tenant or per-module leaderboard.

    rank is 1-based; ties share the same rank and the next rank is
    skipped (standard competition ranking).
    display_name is pre-formatted by the service layer (full_name
    or anonymised alias depending on tenant privacy settings).
    avatar_url is optional; None when the user has no avatar set.
    """

    model_config = ConfigDict(from_attributes=True)

    rank: int = Field(..., ge=1, description="1-based leaderboard position")
    user_id: UUID
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Full name or privacy-anonymised alias",
    )
    avatar_url: str | None = Field(
        default=None,
        description="Presigned URL for the user's avatar image; None if unset",
    )
    average_score: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("100"),
        description="Average session score for ranking; None if no completed sessions",
    )
    best_score: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("100"),
    )
    sessions_completed: int = Field(..., ge=0)
    streak_days: int = Field(..., ge=0)
    completion_percent: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("100"),
    )
