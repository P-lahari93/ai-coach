"""Permission resolution helpers."""
from __future__ import annotations
from app.core.exceptions import PermissionDeniedError


def has_permission(user_permissions: set[str], resource: str, action: str) -> bool:
    return f"{resource}:{action}" in user_permissions


def require_permission(user_permissions: set[str], resource: str, action: str) -> None:
    if not has_permission(user_permissions, resource, action):
        raise PermissionDeniedError(f"Missing permission: {resource}:{action}")


def is_superadmin(user: object) -> bool:
    return getattr(user, "is_superadmin", False)
