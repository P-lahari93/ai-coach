"""
Canonical base classes for all SQLAlchemy 2.0 ORM models.

Architecture decisions:
  - Single DeclarativeBase (Base) imported by every model.
    Alembic's env.py targets Base.metadata for autogenerate.
  - Mixins are plain Python classes — SQLAlchemy 2.0 supports
    column mixins through the Mapped[] descriptor protocol.
  - TimestampMixin: uses server_default=func.now() so the DB
    clock governs timestamps even for rows inserted outside the ORM.
  - onupdate uses a Python-side lambda returning a timezone-aware
    datetime — NOT datetime.utcnow (deprecated Py 3.12, removed 3.13).
  - SoftDeleteMixin: deleted_at sentinel only (no is_deleted bool)
    to avoid dual-write drift. Repositories filter on
    `WHERE deleted_at IS NULL`.
  - OptimisticLockMixin: version INTEGER for concurrency control on
    mutable aggregates. Service layer checks rowcount after UPDATE.

Fixes applied (from validation report):
  CRITICAL-06 — onupdate uses lambda: datetime.now(timezone.utc)
  SA-07       — RolePermission inherits TimestampMixin (done in user.py)
  SA-06       — VersionMixin (renamed OptimisticLockMixin) is explicit
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ─────────────────────────────────────────────────────────────────────────────
# Declarative base — one per project
# ─────────────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """
    Single declarative base for the entire project.
    All ORM models must inherit from this class so that
    Base.metadata collects every table definition.
    """
    pass


# ─────────────────────────────────────────────────────────────────────────────
# UUID primary key mixin
# ─────────────────────────────────────────────────────────────────────────────

class UUIDPrimaryKeyMixin:
    """
    UUID primary key generated both client-side (default=uuid.uuid4)
    and server-side (server_default=gen_random_uuid()) so the value is
    available before a flush and correct for rows inserted via raw SQL.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp mixin — every table
# ─────────────────────────────────────────────────────────────────────────────

class TimestampMixin:
    """
    created_at: set once at INSERT by the DB, never changed.
    updated_at: set at INSERT and refreshed on every UPDATE.

    server_default=func.now() — DB clock governs, not app clock.
    onupdate lambda          — timezone-aware Python fallback;
                               correct for test environments that
                               bypass DB triggers.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),  # FIX CRITICAL-06
    )


# ─────────────────────────────────────────────────────────────────────────────
# Soft-delete mixin — business entities
# ─────────────────────────────────────────────────────────────────────────────

class SoftDeleteMixin:
    """
    deleted_at IS NULL     → active record
    deleted_at IS NOT NULL → logically deleted

    Design choice: single sentinel column (no redundant is_deleted bool).
    All repository queries must add `.where(Model.deleted_at.is_(None))`.
    Partial indexes `WHERE deleted_at IS NULL` are created in migrations
    for O(1) active-record lookups.

    The deleted_at column itself is NOT indexed here — partial indexes
    in migrations cover every access pattern more efficiently.
    """

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# Optimistic locking mixin — mutable aggregates only
# ─────────────────────────────────────────────────────────────────────────────

class OptimisticLockMixin:
    """
    Prevents lost updates under concurrent writes without row-level DB locks.

    Applied to: CoachingModule, ModuleVersion, CoachingSession,
                RoleplaySession, KnowledgeBase.

    Service-layer pattern:
    ──────────────────────
        result = await db.execute(
            update(CoachingModule)
            .where(CoachingModule.id == module_id)
            .where(CoachingModule.version == expected_version)
            .values(name=new_name, version=CoachingModule.version + 1)
            .returning(CoachingModule.id)
        )
        if result.rowcount == 0:
            raise OptimisticLockError(
                "Resource was modified by another request. "
                "Reload and retry."
            )

    server_default=text("1") ensures rows inserted via raw SQL
    or seeds also start at version 1.
    """

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Composite base for standard business tables
# ─────────────────────────────────────────────────────────────────────────────

class BusinessBase(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """
    Convenience composite base for all standard business entities:
        UUID PK + timestamps + soft-delete

    Usage:
        class CoachingModule(BusinessBase, Base):
            __tablename__ = "coaching_modules"
            ...

    Tables that do NOT use soft-delete (e.g. pure event logs)
    should inherit UUIDPrimaryKeyMixin + TimestampMixin directly
    instead of this composite.
    """
    __abstract__ = True
