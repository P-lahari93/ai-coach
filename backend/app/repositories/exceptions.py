"""
Repository-layer exceptions.

All exceptions raised inside repositories inherit from RepositoryError.
The service layer catches these and maps them to domain errors or
HTTP responses as appropriate.

Design notes:
  - NotFoundError is raised by get_or_raise() and any domain-specific
    method that asserts existence (e.g. get_by_email_or_raise).
  - OptimisticLockError is raised when an UPDATE that includes a
    version check returns rowcount == 0, meaning another request
    committed a change between the read and the write.
  - DuplicateError wraps IntegrityError (unique constraint violations)
    translated by the repository before bubbling up. The service layer
    should never need to catch sqlalchemy.exc.IntegrityError directly.
  - ConflictError is a general-purpose conflict signal for business-rule
    violations detected in the data layer (e.g. publishing an already-
    published module) that don't fit the other categories.
"""
from __future__ import annotations

from uuid import UUID


class RepositoryError(Exception):
    """Base class for all repository-layer errors."""


class NotFoundError(RepositoryError):
    """
    The requested entity does not exist, has been soft-deleted, or is
    inaccessible due to tenant isolation (RLS).

    Callers should treat tenant isolation failures the same as missing
    records to avoid leaking the existence of cross-tenant data.
    """

    def __init__(self, entity: str, id: UUID | str) -> None:
        self.entity = entity
        self.id = id
        super().__init__(f"{entity} '{id}' not found")


class OptimisticLockError(RepositoryError):
    """
    The resource was modified by a concurrent request between the
    time it was read and the time the update was attempted.

    The caller (service layer) must reload the resource and retry
    the operation with the new version number.

    Attributes:
        entity:   ORM model name, e.g. "CoachingModule"
        id:       UUID of the conflicting resource
        expected: the version the caller expected to find
    """

    def __init__(self, entity: str, id: UUID, expected: int) -> None:
        self.entity = entity
        self.id = id
        self.expected = expected
        super().__init__(
            f"{entity} '{id}' has been modified by another request "
            f"(expected version {expected}). "
            f"Reload the resource and retry."
        )


class DuplicateError(RepositoryError):
    """
    A unique constraint was violated.

    Raised by repositories that catch sqlalchemy.exc.IntegrityError
    on INSERT or UPDATE and can identify the conflicting field from
    the constraint name.

    Attributes:
        entity: ORM model name, e.g. "User"
        field:  column or constraint name, e.g. "email"
        value:  the conflicting value (safe to surface — no secrets)
    """

    def __init__(self, entity: str, field: str, value: str) -> None:
        self.entity = entity
        self.field = field
        self.value = value
        super().__init__(
            f"{entity} with {field}='{value}' already exists"
        )


class ConflictError(RepositoryError):
    """
    A business-rule conflict detected at the data layer.

    Used when the repository detects a state inconsistency that
    is not a unique-constraint violation but is still a data-integrity
    concern (e.g. attempting to soft-delete an already-deleted record,
    or assigning a global role to a tenant-scoped context).

    Attributes:
        message: human-readable description of the conflict
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
