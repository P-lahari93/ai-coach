"""Coaching module schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.module.module_version import ModuleVersionDetail, ModuleVersionResponse

MODULE_STATUS = frozenset({"draft", "published", "archived"})


# ── Gamification overrides sub-schema ─────────────────────────────────────────

class GamificationOverrides(BaseModel):
    """
    Optional per-module gamification tuning stored as JSONB.
    All fields have platform-level defaults; only set to override.
    """

    points_per_session: int | None = Field(
        default=None,
        ge=0,
        description="Points awarded for completing one session",
    )
    points_per_score_band: dict[str, int] | None = Field(
        default=None,
        description='Map of band → points, e.g. {"1": 10, "2": 20, "3": 30, "4": 50}',
    )
    level_threshold: int | None = Field(
        default=None,
        ge=1,
        description="Sessions required to advance a level",
    )


# ── Base ──────────────────────────────────────────────────────────────────────

class CoachingModuleBase(BaseModel):
    key: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9_]+$",
        description="Machine-readable slug, e.g. 'sbi_feedback'. Lowercase, underscores only.",
        examples=["sbi_feedback"],
    )
    name: str = Field(..., min_length=1, max_length=255)
    icon: str | None = Field(
        default=None,
        max_length=50,
        description="Icon identifier for the UI, e.g. 'MessageSquare'",
    )
    blurb: str | None = Field(
        default=None,
        description="Short marketing description shown in the module library",
    )


# ── Request schemas ───────────────────────────────────────────────────────────

class CoachingModuleCreate(CoachingModuleBase):
    """
    POST /modules

    tenant_id=None creates a global platform module (superadmin only).
    status defaults to 'draft' — use the publish endpoint to activate.
    """

    tenant_id: UUID | None = None
    gamification_overrides: GamificationOverrides = Field(
        default_factory=GamificationOverrides
    )


class CoachingModuleUpdate(BaseModel):
    """PATCH /modules/{module_id} — draft modules only."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    icon: str | None = Field(default=None, max_length=50)
    blurb: str | None = None
    gamification_overrides: GamificationOverrides | None = None


# ── Response schemas ──────────────────────────────────────────────────────────

class CoachingModuleResponse(CoachingModuleBase):
    """Standard module response — no version detail."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    status: str
    created_by: UUID | None
    gamification_overrides: dict
    version: int
    created_at: datetime
    updated_at: datetime


class CoachingModuleList(CoachingModuleResponse):
    """
    Module response for list views.
    Includes the current version's summary (version_number, is_current, published_at)
    without loading the full definition.
    """

    current_version: ModuleVersionResponse | None = None


class CoachingModuleDetail(CoachingModuleResponse):
    """
    Full module response with current version's complete definition.
    Used by the session startup engine and the admin module editor.
    """

    current_version: ModuleVersionDetail | None = None
