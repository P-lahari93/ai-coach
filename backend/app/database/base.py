"""
Database base re-export.

The canonical Base and all mixins live in app/models/base.py.
This module re-exports them so existing imports from app.database.base
continue to work without modification.

Architecture note:
  app/models/base.py   → owns Base, mixins (single source of truth)
  app/database/base.py → re-exports for backwards compatibility
  app/database/engine.py → async engine + session factory
"""

from app.models.base import (  # noqa: F401
    Base,
    BusinessBase,
    OptimisticLockMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# Legacy alias used by older model files (role.py, etc.)
UUIDMixin = UUIDPrimaryKeyMixin  # noqa: F401
