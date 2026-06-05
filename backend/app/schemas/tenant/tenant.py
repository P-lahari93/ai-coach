"""
Tenant schemas.

Tenant is the top-level isolation unit for the multi-tenant platform.
Every business entity is scoped to a tenant except global platform records
(tenant_id IS NULL).

Plan values: free | starter | pro | enterprise
Entitlement enforcement is in the service layer; the schema only
validates that the string is a known value.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Literals / enums ──────────────────────────────────────────────────────────

PLAN_VALUES = frozenset({"free", "starter", "pro", "enterprise"})


# ── Base ──────────────────────────────────────────────────────────────────────

class TenantBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Acme Corp"])
    slug: str = Field(
        ...,
        min_length=2,
        max_length=63,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="URL-safe identifier, e.g. 'acme-corp'. Lowercase, hyphens only.",
        examples=["acme-corp"],
    )


# ── Request schemas ───────────────────────────────────────────────────────────

class TenantCreate(TenantBase):
    """
    POST /tenants  (superadmin only)

    plan defaults to 'free'; max_users defaults to 10.
    Both can be overridden by a superadmin at creation time.
    """

    plan: str = Field(
        default="free",
        description="free | starter | pro | enterprise",
    )
    max_users: int = Field(default=10, ge=1, le=100_000)

    @field_validator("plan")
    @classmethod
    def validate_plan(cls, v: str) -> str:
        if v not in PLAN_VALUES:
            raise ValueError(f"plan must be one of {sorted(PLAN_VALUES)}")
        return v


class TenantUpdate(BaseModel):
    """
    PATCH /tenants/{tenant_id}  (superadmin or tenant_admin for name only)

    plan and max_users changes require superadmin.
    name can be updated by a tenant_admin.
    is_active=False suspends the tenant (superadmin only).
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    plan: str | None = Field(default=None)
    max_users: int | None = Field(default=None, ge=1, le=100_000)
    is_active: bool | None = None

    @field_validator("plan")
    @classmethod
    def validate_plan(cls, v: str | None) -> str | None:
        if v is not None and v not in PLAN_VALUES:
            raise ValueError(f"plan must be one of {sorted(PLAN_VALUES)}")
        return v


# ── Response schemas ──────────────────────────────────────────────────────────

class TenantResponse(TenantBase):
    """
    Standard tenant response — safe for tenant_admin and superadmin.
    metadata_ is excluded (may contain billing IDs not safe for all callers).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan: str
    is_active: bool
    max_users: int
    created_at: datetime
    updated_at: datetime


class TenantAdminResponse(TenantResponse):
    """
    Extended tenant response for superadmin — includes metadata JSONB bag.
    Maps ORM's metadata_ column (aliased to avoid clash with Base.metadata).
    """

    metadata_: dict = Field(default_factory=dict, alias="metadata_")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TenantSummary(BaseModel):
    """
    Minimal tenant representation embedded in other responses
    (e.g. user profile listing their tenants).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    plan: str
