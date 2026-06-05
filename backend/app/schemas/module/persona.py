"""Module persona schemas."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PersonaBase(BaseModel):
    persona_name: str = Field(..., min_length=1, max_length=255, examples=["Hostile Prospect"])
    description: str | None = Field(
        default=None,
        description="Shown to the learner before the roleplay starts",
    )
    system_prompt: str = Field(
        ...,
        min_length=1,
        description="Full system message injected as the AI persona",
    )
    traits: list[str] = Field(
        default_factory=list,
        description="Adjective list used by scoring engine, e.g. ['direct', 'impatient']",
        examples=[["direct", "impatient", "detail-oriented"]],
    )
    is_default: bool = Field(
        default=False,
        description="Selected when no explicit persona_id is given at session creation",
    )


class PersonaCreate(PersonaBase):
    module_version_id: UUID


class PersonaUpdate(BaseModel):
    """Allowed on draft versions only."""

    persona_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    system_prompt: str | None = Field(default=None, min_length=1)
    traits: list[str] | None = None
    is_default: bool | None = None


class PersonaResponse(PersonaBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module_version_id: UUID


class PersonaSummary(BaseModel):
    """Minimal persona reference used inside version responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    persona_name: str
    description: str | None = None
    is_default: bool
