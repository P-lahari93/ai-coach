"""
Tenant domain models: Tenant + TenantSettings.

Architecture decisions:
  - Tenant is the top-level isolation unit for multi-tenancy.
    Every business record carries a nullable tenant_id
    (NULL = global / platform-owned record).
  - TenantSettings is a 1:1 extension table rather than JSONB
    columns on tenants. This keeps the tenants row lean for
    fast auth lookups and lets settings grow independently.
  - RLS: all business tables are filtered by tenant_id at the
    DB layer via Row Level Security policies (see migrations).
    The application middleware sets `app.current_tenant_id`
    on each DB connection.

Fixes applied (from validation report):
  CRITICAL-01 — `text` import added explicitly
  MVP-05      — TenantSettings remains separate (future split justified)
  SA-01       — lazy="dynamic" replaced with lazy="write_only"
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BusinessBase, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    # Forward references only — no runtime import to avoid circular imports.
    from app.models.user import UserTenant


class Tenant(BusinessBase, Base):
    """
    Top-level tenant (company / organisation).

    plan: free | starter | pro | enterprise
          Entitlement logic lives in the service layer — the DB stores
          the string and the service enforces limits.
    max_users: soft cap enforced by TenantService.invite_user().
    metadata_: extensible JSONB bag (e.g. Stripe customer_id,
               billing_email) — avoids schema migrations for minor fields.

    Soft-delete inherited from BusinessBase.
    """
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    slug: Mapped[str] = mapped_column(
        String(63),
        nullable=False,
        index=True,
        comment="URL-safe unique identifier, e.g. 'acme-corp'",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="free",
        server_default=text("'free'"),
        comment="free | starter | pro | enterprise",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    max_users: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        server_default=text("10"),
    )
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata",                      # DB column name avoids SA Base.metadata clash
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Extensible bag: billing_id, logo_url, etc.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    settings: Mapped[Optional[TenantSettings]] = relationship(
        "TenantSettings",
        back_populates="tenant",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="select",          # always loaded with Tenant (1:1, cheap JOIN)
    )
    user_tenants: Mapped[list[UserTenant]] = relationship(
        "UserTenant",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="write_only",      # FIX SA-01: replaces deprecated lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r} plan={self.plan!r}>"


class TenantSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    1:1 settings extension for a tenant.

    Kept as a separate table so the hot `tenants` row stays compact.
    All settings are in a single JSONB column for schema flexibility.

    Documented JSONB keys (not DB-enforced — validated in Pydantic schema):
      logo_url           str   — URL for tenant branding
      primary_color      str   — hex colour, e.g. "#3B82F6"
      citations_visible  bool  — show RAG source citations to learners
      allowed_modules    list  — module key whitelist; empty = all allowed
      default_language   str   — BCP-47, e.g. "en", "fr"
      ai_model_override  str   — overrides global Ollama model per tenant
    """
    __tablename__ = "tenant_settings"

    # ── Columns ───────────────────────────────────────────────────────────────
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    settings: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="settings",
    )

    def __repr__(self) -> str:
        return f"<TenantSettings tenant_id={self.tenant_id}>"
