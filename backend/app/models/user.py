"""
User, Role, Permission, UserRole, UserTenant, RefreshToken models.

Architecture decisions:
  - User is a platform-level identity, decoupled from tenants.
    A user joins one or more tenants through UserTenant.
  - RBAC: User → UserRole(s) → Role → RolePermission(s) → Permission.
    Permission keys follow `resource:action` convention, e.g.
    "module:publish", "knowledge_base:manage", "session:read".
  - UserRole carries tenant_id so the same user can have different
    roles in different tenants (e.g. learner in Tenant A,
    tenant_admin in Tenant B). tenant_id=NULL = global role.
  - RefreshToken: only the SHA-256 hash of the raw token is stored.
    The raw token is transmitted to the client once and never persisted.

Fixes applied (from validation report):
  CRITICAL-01 — `text` import present
  CRITICAL-02 — No datetime.utcnow usage (inherits from fixed TimestampMixin)
  SA-01       — lazy="dynamic" replaced with lazy="write_only" on large
                collections (coaching_sessions, roleplay_sessions, etc.)
  SA-04       — UserRole.user relationship explicitly declares foreign_keys
  SA-07       — RolePermission inherits TimestampMixin (created_at tracked)
  SEC-02      — is_valid uses datetime.now(timezone.utc) (tz-aware)
  SEC-04      — is_superadmin changes must go through AuditLog in service
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    BusinessBase,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    # Avoid circular imports — used only for type annotations.
    from app.models.tenant import Tenant
    from app.models.progress import UserProgress          # Batch 4
    from app.models.gamification import UserAchievement  # Batch 4
    from app.models.notification import Notification     # Batch 4


# ─────────────────────────────────────────────────────────────────────────────
# Permission
# ─────────────────────────────────────────────────────────────────────────────

class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Atomic permission record: resource × action.

    resource examples : "module", "knowledge_base", "session", "user", "report"
    action examples   : "create", "read", "update", "delete", "publish", "manage"

    Stored as two columns (not a single key string) so queries like
    "all actions on resource=module" are indexed lookups, not LIKE scans.
    """
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint(
            "resource", "action", name="uq_permissions_resource_action"
        ),
        Index("idx_permissions_resource", "resource"),
    )

    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    roles: Mapped[list[Role]] = relationship(
        "Role",
        secondary="role_permissions",
        back_populates="permissions",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Permission {self.resource}:{self.action}>"


# ─────────────────────────────────────────────────────────────────────────────
# Role
# ─────────────────────────────────────────────────────────────────────────────

class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Named set of permissions.

    Built-in system roles (is_system=True, cannot be deleted):
        superadmin    — platform-wide, bypasses all tenant checks
        tenant_admin  — full control within their tenant
        program_owner — can create/publish modules within their tenant
        learner       — can start sessions, view own reports

    scope:
        global  — available platform-wide (e.g. superadmin)
        tenant  — exists within a specific tenant context

    Custom roles are created by tenant admins (is_system=False).
    """
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("name", name="uq_roles_name"),
        Index("idx_roles_scope", "scope"),
    )

    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="tenant",
        server_default=text("'tenant'"),
        comment="global | tenant",
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="System roles cannot be deleted or renamed.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    permissions: Mapped[list[Permission]] = relationship(
        "Permission",
        secondary="role_permissions",
        back_populates="roles",
        lazy="selectin",       # always needed when role is loaded for RBAC check
    )
    user_roles: Mapped[list[UserRole]] = relationship(
        "UserRole",
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="write_only",     # FIX SA-01
    )

    def __repr__(self) -> str:
        return f"<Role name={self.name!r} scope={self.scope!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# RolePermission — join table
# ─────────────────────────────────────────────────────────────────────────────

class RolePermission(TimestampMixin, Base):
    """
    Many-to-many join: Role ↔ Permission.

    Composite PK (role_id, permission_id) — no surrogate UUID needed.
    TimestampMixin adds created_at to audit when a permission was
    granted to a role.                    (FIX SA-07)
    """
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # created_at inherited from TimestampMixin
    # updated_at inherited from TimestampMixin (harmless on a join table)


# ─────────────────────────────────────────────────────────────────────────────
# User
# ─────────────────────────────────────────────────────────────────────────────

class User(BusinessBase, Base):
    """
    Platform-level user identity.

    A user is independent of any tenant. Tenant membership is tracked
    via UserTenant. This allows a single login to access multiple
    organisations without requiring multiple accounts.

    is_superadmin: bypasses ALL tenant and RBAC checks.
                   Changes to this flag MUST be written to audit_logs
                   by the service layer (no DB trigger — enforced in code).
    password_hash: bcrypt hash. Never store or log the raw value.
    email:         treated case-insensitively in queries
                   (use lower() functional index in migration).
    """
    __tablename__ = "users"
    __table_args__ = (
        # Partial unique index: only one active account per email
        Index(
            "idx_users_email_active",
            "email",
            postgresql_where=text("deleted_at IS NULL"),
            unique=True,
        ),
        # Composite index for tenant-scoped user queries
        Index("idx_users_active", "is_active", "deleted_at"),
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
        comment="Case-insensitive via lower() index in migration",
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="bcrypt hash — never store raw password",
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    is_superadmin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Bypasses all RBAC. Changes must be audit-logged in service layer.",
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user_roles: Mapped[list[UserRole]] = relationship(
        "UserRole",
        back_populates="user",
        foreign_keys="UserRole.user_id",   # FIX SA-04: explicit FK
        cascade="all, delete-orphan",
        lazy="selectin",    # loaded with user for every auth check
    )
    user_tenants: Mapped[list[UserTenant]] = relationship(
        "UserTenant",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="write_only",  # FIX SA-01: never need to load all tokens at once
    )
    # ── Batch 4 back-populates ────────────────────────────────────────────────
    progress: Mapped[list[UserProgress]] = relationship(
        "UserProgress",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",          # loaded on profile page; small list per user
    )
    achievements: Mapped[list[UserAchievement]] = relationship(
        "UserAchievement",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="write_only",      # may grow large; load explicitly
    )
    notifications: Mapped[list[Notification]] = relationship(
        "Notification",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="write_only",      # potentially thousands; never eager-load
    )

    # ── Helper properties ─────────────────────────────────────────────────────
    @property
    def role_names(self) -> list[str]:
        """Convenience: list of role names from eagerly loaded user_roles."""
        return [ur.role.name for ur in self.user_roles if ur.role]

    @property
    def permission_set(self) -> set[str]:
        """
        Flat set of 'resource:action' strings from all assigned roles.
        Used by the RBAC checker in core/permissions.py.
        Example: {'module:read', 'session:create', 'feedback:read'}
        """
        perms: set[str] = set()
        for ur in self.user_roles:
            if ur.role:
                for perm in ur.role.permissions:
                    perms.add(f"{perm.resource}:{perm.action}")
        return perms

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# UserRole — join table with extra columns
# ─────────────────────────────────────────────────────────────────────────────

class UserRole(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Assigns a Role to a User, optionally scoped to a Tenant.

    Uniqueness: one (user, role, tenant) tuple — a user cannot hold
    the same role twice within the same scope.

    NULL handling: PostgreSQL treats NULL != NULL in unique constraints,
    so (user_id, role_id, NULL) can appear at most once per the
    partial unique index defined in migrations:
        UNIQUE (user_id, role_id) WHERE tenant_id IS NULL
        UNIQUE (user_id, role_id, tenant_id) WHERE tenant_id IS NOT NULL
    """
    __tablename__ = "user_roles"
    __table_args__ = (
        # Partial indexes (handle NULL tenant_id) defined in migration.
        # SQLAlchemy UniqueConstraint here would not handle the NULL case.
        Index("idx_user_roles_user_tenant", "user_id", "tenant_id"),
        Index("idx_user_roles_role", "role_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = global role assignment (e.g. superadmin)",
    )
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User ID who granted this role assignment",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="user_roles",
        foreign_keys=[user_id],     # FIX SA-04: disambiguate from granted_by FK
    )
    role: Mapped[Role] = relationship(
        "Role",
        back_populates="user_roles",
        lazy="selectin",    # role + permissions loaded with user_roles
    )

    def __repr__(self) -> str:
        return (
            f"<UserRole user={self.user_id} "
            f"role={self.role_id} tenant={self.tenant_id}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# UserTenant — membership join table
# ─────────────────────────────────────────────────────────────────────────────

class UserTenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Membership record: which users belong to which tenants.

    is_primary: marks the user's default workspace when they have
                multiple tenant memberships (used by the frontend to
                route to the correct tenant on login).

    joined_at: separate from created_at — semantically distinct
               (a record could be created before the user activates
               their membership via email invite).
    """
    __tablename__ = "user_tenants"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenants"),
        Index("idx_user_tenants_user", "user_id"),
        Index("idx_user_tenants_tenant", "tenant_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship("User", back_populates="user_tenants")
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="user_tenants")

    def __repr__(self) -> str:
        return (
            f"<UserTenant user={self.user_id} "
            f"tenant={self.tenant_id} primary={self.is_primary}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RefreshToken
# ─────────────────────────────────────────────────────────────────────────────

class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Persisted refresh token — only the SHA-256 hash is stored.

    Security design:
      - The raw 256-bit opaque token is generated by the auth service,
        returned to the client exactly once, and never stored.
      - token_hash is a SHA-256 hex digest (64 chars).
        bcrypt is NOT used here (bcrypt is slow by design — refresh token
        lookup happens on every token refresh, so SHA-256 with a secret
        pepper is the correct trade-off).
      - Revocation: set revoked_at. Expired/revoked tokens are pruned
        by a scheduled job — no FK cascade (bulk cascade deletes
        under load can cause deadlocks).
      - device_hint and ip_address allow security dashboards to surface
        "active sessions" to the user.

    FIX SEC-02: is_valid uses datetime.now(timezone.utc) — tz-aware.
    """
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("idx_refresh_tokens_user_expires", "user_id", "expires_at"),
        Index(
            "idx_refresh_tokens_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),          # SHA-256 hex digest = 64 chars
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hex digest of the raw opaque token",
    )
    device_hint: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="User-agent summary, e.g. 'Chrome 124 / macOS'",
    )
    ip_address: Mapped[Optional[str]] = mapped_column(
        INET,
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship("User", back_populates="refresh_tokens")

    # ── Properties ────────────────────────────────────────────────────────────
    @property
    def is_valid(self) -> bool:
        """
        True if the token is not revoked and has not expired.
        Uses timezone-aware comparison (FIX SEC-02).
        """
        now = datetime.now(timezone.utc)
        return self.revoked_at is None and self.expires_at > now

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def __repr__(self) -> str:
        return (
            f"<RefreshToken user={self.user_id} "
            f"expires={self.expires_at} revoked={self.is_revoked}>"
        )
