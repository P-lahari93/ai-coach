"""
TenantSettings schemas.

TenantSettings is a 1:1 extension of Tenant stored in a separate table.
All settings live in a single JSONB column for schema flexibility.

The JSONB keys are documented and validated here in Pydantic (not at
the DB layer). This is the single source of truth for what settings
are supported.

JSONB key catalogue:
  logo_url           str   — absolute URL for tenant branding image
  primary_color      str   — hex colour code, e.g. "#3B82F6"
  citations_visible  bool  — whether RAG source citations are shown to learners
  allowed_modules    list  — list of module keys that are whitelisted;
                             empty list = all modules allowed
  default_language   str   — BCP-47 language tag, e.g. "en", "fr", "de"
  ai_model_override  str   — Ollama model name override for this tenant;
                             overrides the platform default (OLLAMA_MODEL config)
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Settings payload (the inner typed structure) ──────────────────────────────

class TenantSettingsPayload(BaseModel):
    """
    Typed representation of the settings JSONB blob.

    All fields are optional — a tenant does not need to configure every
    setting. Unset fields fall back to platform defaults.
    """

    logo_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Absolute URL for tenant branding image",
    )
    primary_color: str | None = Field(
        default=None,
        description="Hex colour code, e.g. '#3B82F6'",
    )
    citations_visible: bool = Field(
        default=True,
        description="Show RAG source citations to learners in feedback reports",
    )
    allowed_modules: list[str] = Field(
        default_factory=list,
        description=(
            "Whitelist of module keys visible to this tenant. "
            "Empty list = all published modules are visible."
        ),
    )
    default_language: str = Field(
        default="en",
        max_length=10,
        description="BCP-47 language tag, e.g. 'en', 'fr'",
    )
    ai_model_override: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Ollama model name to use for this tenant instead of the platform "
            "default. e.g. 'qwen3:4b', 'llama3:8b'."
        ),
    )

    @field_validator("primary_color")
    @classmethod
    def validate_hex_color(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^#[0-9A-Fa-f]{6}$", v):
            raise ValueError(
                "primary_color must be a 6-digit hex code, e.g. '#3B82F6'"
            )
        return v

    @field_validator("logo_url")
    @classmethod
    def validate_logo_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("logo_url must be an absolute HTTP/HTTPS URL")
        return v


# ── Request schemas ───────────────────────────────────────────────────────────

class TenantSettingsUpdate(BaseModel):
    """
    PATCH /tenants/{tenant_id}/settings

    Sends a full or partial settings payload. Fields not included keep
    their current values (merging is handled by the service layer).
    """

    settings: TenantSettingsPayload


class TenantSettingsReplace(BaseModel):
    """
    PUT /tenants/{tenant_id}/settings

    Replaces the entire settings blob. All keys revert to default
    values unless explicitly provided.
    """

    settings: TenantSettingsPayload


# ── Response schemas ──────────────────────────────────────────────────────────

class TenantSettingsResponse(BaseModel):
    """
    Response for GET /tenants/{tenant_id}/settings.

    Returns the tenant_id alongside the parsed settings object so
    callers can correlate the response without an extra request.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    settings: TenantSettingsPayload
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def parse_settings_blob(cls, data):
        """
        When data comes from an ORM TenantSettings object, the `settings`
        attribute is a raw dict (JSONB). Parse it into TenantSettingsPayload.
        """
        if hasattr(data, "settings") and isinstance(data.settings, dict):
            data.__dict__["settings"] = TenantSettingsPayload.model_validate(
                data.settings
            )
        return data
