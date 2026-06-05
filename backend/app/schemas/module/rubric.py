"""Rubric schemas — scoring dimensions and band descriptors."""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Sub-schemas ───────────────────────────────────────────────────────────────

class DimensionSchema(BaseModel):
    """
    One scoring dimension within a rubric.

    weight must be in (0, 1]. The sum of all dimension weights
    in a rubric must equal 1.0 — enforced at the rubric level.

    band_descriptors keys are integer strings "1"–"4" (or "1"–"5").
    """

    name: str = Field(..., min_length=1, max_length=255, examples=["Situation Clarity"])
    weight: Decimal = Field(
        ...,
        gt=0,
        le=1,
        description="Proportion of total score; all weights must sum to 1.0",
        examples=[0.25],
    )
    band_descriptors: dict[str, str] = Field(
        ...,
        description='Keyed by band number string, e.g. {"1": "No situation described", "4": "Specific and detailed"}',
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_band_keys(self) -> "DimensionSchema":
        for key in self.band_descriptors:
            if not key.isdigit() or int(key) < 1:
                raise ValueError(
                    f"band_descriptors keys must be positive integer strings; got '{key}'"
                )
        return self


# ── Base ──────────────────────────────────────────────────────────────────────

class RubricBase(BaseModel):
    dimensions: list[DimensionSchema] = Field(
        ...,
        min_length=1,
        description="Ordered list of scoring dimensions. Weights must sum to 1.0.",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable rubric description for admin UI display",
    )
    change_notes: str | None = Field(
        default=None,
        description="Notes on what changed in this content_version",
    )

    @model_validator(mode="after")
    def validate_weight_sum(self) -> "RubricBase":
        total = sum(d.weight for d in self.dimensions)
        if abs(total - Decimal("1.0")) > Decimal("0.001"):
            raise ValueError(
                f"Dimension weights must sum to 1.0; got {total:.4f}"
            )
        return self


# ── Request schemas ───────────────────────────────────────────────────────────

class RubricCreate(RubricBase):
    module_version_id: UUID


class RubricUpdate(BaseModel):
    """
    Update a rubric's wording (content_version incremented by service).
    Only allowed when the parent ModuleVersion is a draft.
    Structural changes (add/remove dimensions) require a new version.
    """

    dimensions: list[DimensionSchema] | None = None
    description: str | None = None
    change_notes: str | None = None

    @model_validator(mode="after")
    def validate_weight_sum_if_provided(self) -> "RubricUpdate":
        if self.dimensions is not None:
            total = sum(d.weight for d in self.dimensions)
            if abs(total - Decimal("1.0")) > Decimal("0.001"):
                raise ValueError(
                    f"Dimension weights must sum to 1.0; got {total:.4f}"
                )
        return self


# ── Response schemas ──────────────────────────────────────────────────────────

class RubricResponse(RubricBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module_version_id: UUID
    content_version: int
