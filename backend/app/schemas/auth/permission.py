"""Permission schemas."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PermissionBase(BaseModel):
    resource: str = Field(..., max_length=100, examples=["module"])
    action: str = Field(..., max_length=50, examples=["read"])
    description: str | None = Field(default=None, max_length=500)


class PermissionCreate(PermissionBase):
    pass


class PermissionResponse(PermissionBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID

    @property
    def key(self) -> str:
        """Convenience: 'resource:action' permission key string."""
        return f"{self.resource}:{self.action}"
