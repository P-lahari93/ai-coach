"""Module version schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.module.framework_step import FrameworkStepResponse
from app.schemas.module.persona import PersonaResponse, PersonaSummary
from app.schemas.module.prompt_template import PromptTemplateResponse
from app.schemas.module.rubric import RubricResponse


# ── Intake field schema (documents the JSONB structure) ───────────────────────

class IntakeFieldSchema(BaseModel):
    """
    One field in a module's intake form.

    Used to document / validate the intake_schema JSONB array that is
    stored on ModuleVersion. Not persisted directly — parsed from JSONB.
    """

    field_key: str = Field(..., min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=255)
    type: str = Field(
        ...,
        pattern="^(text|longtext|voice)$",
        description="text | longtext | voice",
    )
    required: bool = True
    placeholder: str | None = None


# ── Base ──────────────────────────────────────────────────────────────────────

class ModuleVersionBase(BaseModel):
    framework_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        examples=["SBI", "GROW"],
    )
    intake_schema: list[IntakeFieldSchema] = Field(
        default_factory=list,
        description="Ordered list of intake field definitions",
    )
    scoring_rubric: dict = Field(
        default_factory=dict,
        description="Legacy JSONB rubric snapshot; prefer the rubric relationship for new code",
    )


# ── Request schemas ───────────────────────────────────────────────────────────

class ModuleVersionCreate(ModuleVersionBase):
    """Create a new draft version for a module."""

    module_id: UUID


class ModuleVersionUpdate(BaseModel):
    """Allowed on draft (unpublished) versions only."""

    framework_name: str | None = Field(default=None, min_length=1, max_length=100)
    intake_schema: list[IntakeFieldSchema] | None = None
    scoring_rubric: dict | None = None


# ── Response schemas ──────────────────────────────────────────────────────────

class ModuleVersionResponse(ModuleVersionBase):
    """Summary version response — no child objects loaded."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module_id: UUID
    version_number: int
    is_current: bool
    published_at: datetime | None
    published_by: UUID | None
    created_at: datetime


class ModuleVersionDetail(ModuleVersionResponse):
    """
    Full version response with all child objects eagerly loaded.

    Used by:
      - Admin module editor (shows full definition)
      - Session startup engine (loads templates, steps, personas, rubric)
      - Scoring engine (loads rubric)
    """

    framework_steps: list[FrameworkStepResponse] = Field(default_factory=list)
    prompt_templates: list[PromptTemplateResponse] = Field(default_factory=list)
    personas: list[PersonaResponse] = Field(default_factory=list)
    rubric: RubricResponse | None = None
