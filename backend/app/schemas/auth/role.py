"""Role schemas."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.auth.permission import PermissionResponse


class RoleBase(BaseModel):
    name: str = Field(..., max_length=50, examples=["learner"])
    scope: str = Field(
        default="tenant",
        pattern="^(global|tenant)$",
        description="global | tenant",
    )
    description: str | None = None


class RoleCreate(RoleBase):
    permission_ids: list[UUID] = Field(
        default_factory=list,
        description="Permission UUIDs to assign to this role at creation",
    )


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=50)
    description: str | None = None


class RoleResponse(RoleBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_system: bool


class RoleWithPermissions(RoleResponse):
    """Role response with eagerly loaded permissions list."""

    permissions: list[PermissionResponse] = Field(default_factory=list)
