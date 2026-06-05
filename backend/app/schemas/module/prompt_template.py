"""Prompt template schemas."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TemplateType = Literal["coaching", "roleplay_system", "roleplay_turn", "scoring"]


class PromptTemplateBase(BaseModel):
    template_type: TemplateType = Field(
        ...,
        description="coaching | roleplay_system | roleplay_turn | scoring",
    )
    template_body: str = Field(
        ...,
        min_length=1,
        description="Prompt text with {{variable}} slots",
    )
    variables: list[str] = Field(
        default_factory=list,
        description="Declared slot names, e.g. ['framework', 'rubric', 'knowledge']",
    )


class PromptTemplateCreate(PromptTemplateBase):
    module_version_id: UUID


class PromptTemplateUpdate(BaseModel):
    """Only template_body and variables may be updated (draft versions only)."""

    template_body: str | None = Field(default=None, min_length=1)
    variables: list[str] | None = None


class PromptTemplateResponse(PromptTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module_version_id: UUID
