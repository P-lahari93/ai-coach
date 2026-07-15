# FILE: backend/app/core/exceptions.py
"""
Domain exceptions — all business logic raises these.
FastAPI exception handlers map them to HTTP responses.
Keeps HTTP concerns out of the service layer (Clean Architecture).
"""
from __future__ import annotations


class AppError(Exception):
    """Base application error."""
    status_code: int = 500
    detail: str = "An unexpected error occurred."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


# ── 400 ──────────────────────────────────────────────────────────────────────

class ValidationError(AppError):
    status_code = 400
    detail = "Validation error."


class BadRequestError(AppError):
    status_code = 400
    detail = "Bad request."


class ContentSafetyError(AppError):
    status_code = 400
    detail = "Content did not pass safety checks."

class ContentSafetyError(AppError):
    status_code = 400
    detail = "Content did not pass safety checks."


class CrisisContentDetectedError(AppError):
    """
    Raised when SafetyEngine flags content as crisis/self-harm language.

    Deliberately NOT a generic 400 — main.py registers a dedicated
    handler for this exception that returns a supportive message with
    resources instead of a bare rejection. See app/main.py.
    """
    status_code = 200
    detail = "We noticed something in your message we wanted to check in about."


# ── 401 ──────────────────────────────────────────────────────────────────────

class AuthenticationError(AppError):
    status_code = 401
    detail = "Authentication failed."


class InvalidTokenError(AppError):
    status_code = 401
    detail = "Invalid or expired token."


# ── 403 ──────────────────────────────────────────────────────────────────────

class PermissionDeniedError(AppError):
    status_code = 403
    detail = "You do not have permission to perform this action."


# ── 404 ──────────────────────────────────────────────────────────────────────

class NotFoundError(AppError):
    """
    Supports two calling conventions used across the codebase:

        NotFoundError("Resource not found message")       # 1 arg — detail string
        NotFoundError("EntityName", identifier)           # 2 args — entity + id
    """
    status_code = 404
    detail = "Resource not found."

    def __init__(self, resource: str | None = None, identifier: object = None) -> None:
        if identifier is not None:
            # Called as NotFoundError("User", user_id)
            detail = f"{resource} '{identifier}' not found."
        elif resource is not None:
            # Called as NotFoundError("User not found") or NotFoundError("User")
            detail = resource
        else:
            detail = self.__class__.detail
        self.detail = detail
        Exception.__init__(self, detail)


# ── 409 ──────────────────────────────────────────────────────────────────────

class ConflictError(AppError):
    status_code = 409
    detail = "Resource already exists."


# ── 422 ──────────────────────────────────────────────────────────────────────

class UnprocessableError(AppError):
    status_code = 422
    detail = "Could not process entity."


# ── 429 ──────────────────────────────────────────────────────────────────────

class RateLimitError(AppError):
    status_code = 429
    detail = "Too many requests."


# ── Domain-specific ───────────────────────────────────────────────────────────

class ModulePublishedError(AppError):
    """Raised when attempting to mutate an immutable published module version."""
    status_code = 409
    detail = "Published module versions are immutable. Create a new version instead."


class InsufficientKnowledgeError(AppError):
    """Raised when RAG retrieval finds no usable content."""
    status_code = 422
    detail = "No relevant knowledge found for this query."


class IngestionError(AppError):
    status_code = 422
    detail = "Document ingestion failed."