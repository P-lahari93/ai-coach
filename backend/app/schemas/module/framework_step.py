"""Framework step schemas."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FrameworkStepBase(BaseModel):
    step_order: int = Field(..., ge=0, description="0-based display order within the version")
    label: str = Field(..., min_length=1, max_length=255, examples=["Situation"])
    description: str = Field(..., min_length=1)
    scoring_hints: str | None = Field(
        default=None,
        description="Private hints injected into the LLM scoring prompt — not shown to learners",
    )


class FrameworkStepCreate(FrameworkStepBase):
    module_version_id: UUID


class FrameworkStepResponse(FrameworkStepBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module_version_id: UUID
