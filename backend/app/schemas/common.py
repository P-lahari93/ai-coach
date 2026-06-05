"""
Common / shared schema primitives used across all domain schemas.

Includes:
  - Page[T]          — typed pagination envelope matching BaseRepository.Page
  - PaginationParams — query-param schema for paginated endpoints
  - MessageResponse  — generic success/error message wrapper
  - IDResponse       — response carrying only a UUID
  - TimestampedSchema— mixin adding created_at / updated_at to responses
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


# ── Pagination ────────────────────────────────────────────────────────────────

class Page(BaseModel, Generic[T]):
    """
    Typed pagination envelope.

    Mirrors BaseRepository.Page so service→API translation is zero-cost.
    items must be a list of serialised schema objects (not ORM objects).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T]
    total: int = Field(..., ge=0, description="Total rows matching the filter")
    page: int = Field(..., ge=1, description="Current page (1-based)")
    page_size: int = Field(..., ge=1, description="Rows per page")

    @property
    def pages(self) -> int:
        if self.page_size == 0:
            return 0
        return max(1, math.ceil(self.total / self.page_size))

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1


class PaginationParams(BaseModel):
    """
    Standard query parameters for paginated list endpoints.
    Validated at the API layer before being passed to repositories.
    """

    page: int = Field(default=1, ge=1, description="Page number (1-based)")
    page_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Rows per page (max 100)",
    )


# ── Generic response wrappers ─────────────────────────────────────────────────

class MessageResponse(BaseModel):
    """Generic response for operations that return only a status message."""

    message: str


class IDResponse(BaseModel):
    """Response carrying only the id of a created/updated resource."""

    id: UUID


# ── Timestamp mixin ───────────────────────────────────────────────────────────

class TimestampedSchema(BaseModel):
    """
    Adds created_at and updated_at to any response schema that needs them.

    Uses model_config = ConfigDict(from_attributes=True) so SQLAlchemy
    ORM objects can be passed directly to model_validate().
    """

    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    updated_at: datetime


# ── Soft-delete mixin ─────────────────────────────────────────────────────────

class SoftDeleteSchema(TimestampedSchema):
    """Adds deleted_at to responses that expose soft-delete state."""

    deleted_at: datetime | None = None
