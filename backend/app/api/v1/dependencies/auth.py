from __future__ import annotations
from uuid import UUID
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from app.core.security import decode_token
from app.core.exceptions import InvalidTokenError
from app.schemas.auth.token import AccessTokenPayload
from app.models.user import User
from app.api.v1.dependencies.uow import get_uow
from app.database.unit_of_work import UnitOfWork

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
optional_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_current_user_payload(
    token: str = Depends(oauth2_scheme),
) -> AccessTokenPayload:
    try:
        data = decode_token(token)
        return AccessTokenPayload(**data)
    except (JWTError, Exception):
        raise InvalidTokenError("Invalid or expired token")


async def get_current_user(
    payload: AccessTokenPayload = Depends(get_current_user_payload),
    uow: UnitOfWork = Depends(get_uow),
) -> User:
    # payload.sub is a string UUID — convert before DB lookup
    try:
        user_uuid = UUID(payload.sub)
    except (ValueError, AttributeError):
        raise InvalidTokenError("Invalid token subject")
    user = await uow.users.get_with_roles(user_uuid)
    if user is None or not user.is_active:
        raise InvalidTokenError("User not found or inactive")
    return user


async def get_current_active_user(
    user: User = Depends(get_current_user),
) -> User:
    return user


async def get_optional_user(
    token: str | None = Depends(optional_oauth2),
    uow: UnitOfWork = Depends(get_uow),
) -> User | None:
    if not token:
        return None
    try:
        data = decode_token(token)
        payload = AccessTokenPayload(**data)
        user_uuid = UUID(payload.sub)
        return await uow.users.get_with_roles(user_uuid)
    except Exception:
        return None


async def get_current_tenant_id(
    payload: AccessTokenPayload = Depends(get_current_user_payload),
) -> UUID | None:
    if payload.tenant_id is None:
        return None
    try:
        return UUID(payload.tenant_id)
    except (ValueError, AttributeError):
        return None
