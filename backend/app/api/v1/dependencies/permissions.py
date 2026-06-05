from __future__ import annotations
from fastapi import Depends
from app.core.exceptions import PermissionDeniedError
from app.models.user import User
from app.api.v1.dependencies.auth import get_current_active_user


def require_permission(resource: str, action: str):
    async def _check(user: User = Depends(get_current_active_user)) -> User:
        if user.is_superadmin:
            return user
        if f"{resource}:{action}" not in user.permission_set:
            raise PermissionDeniedError(f"Missing permission: {resource}:{action}")
        return user
    return _check
