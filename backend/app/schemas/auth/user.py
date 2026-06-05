"""User schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.auth.role import RoleResponse


# ── Base ──────────────────────────────────────────────────────────────────────

class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    avatar_url: str | None = None


# ── Request schemas ───────────────────────────────────────────────────────────

class UserCreate(UserBase):
    """
    Used by the registration / admin-create-user endpoint.
    password is plain text — hashed by the auth service before storage.
    """

    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def email_lowercase(cls, v: str) -> str:
        return v.lower().strip()


class UserUpdate(BaseModel):
    """All fields optional for PATCH semantics."""

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    avatar_url: str | None = None


class UserPasswordUpdate(BaseModel):
    """Used by the change-password endpoint."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


# ── Response schemas ──────────────────────────────────────────────────────────

class UserResponse(UserBase):
    """Safe user response — never exposes password_hash."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_active: bool
    is_superadmin: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserWithRoles(UserResponse):
    """User response with eagerly loaded role graph."""

    roles: list[RoleResponse] = Field(default_factory=list)

    @classmethod
    def from_orm_with_roles(cls, user) -> "UserWithRoles":  # type: ignore[override]
        """
        Build UserWithRoles from a User ORM object that has user_roles
        eagerly loaded (user_roles → role).
        """
        data = cls.model_validate(user)
        data.roles = [
            RoleResponse.model_validate(ur.role)
            for ur in user.user_roles
            if ur.role is not None
        ]
        return data


class UserSummary(BaseModel):
    """Minimal user representation for embedding in other responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    email: EmailStr
    avatar_url: str | None = None
