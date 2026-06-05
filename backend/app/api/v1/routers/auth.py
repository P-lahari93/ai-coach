from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, status
from app.schemas.auth.auth_request import LoginRequest, RegisterRequest, PasswordChangeRequest
from app.schemas.auth.token import TokenPair, RefreshRequest, TokenRevoke, ActiveSessionResponse
from app.schemas.auth.user import UserResponse
from app.schemas.common import MessageResponse
from app.services.auth.auth_service import AuthService
from app.api.v1.dependencies.auth import get_current_active_user
from app.models.user import User
from app.core.config import settings

router = APIRouter()
_svc = AuthService()


def _to_token_pair(svc_result) -> TokenPair:
    """Convert service TokenPair dataclass → schema TokenPair with expires_in."""
    return TokenPair(
        access_token=svc_result.access_token,
        refresh_token=svc_result.refresh_token,
        token_type=svc_result.token_type,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest):
    """Register a new user account."""
    user = await _svc.register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
    )
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest):
    """Login with email and password, receive JWT token pair."""
    result = await _svc.login(email=body.email, password=body.password)
    return _to_token_pair(result)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest):
    """Refresh access token using a valid refresh token."""
    result = await _svc.refresh_access_token(body.refresh_token)
    return _to_token_pair(result)


@router.post("/logout", response_model=MessageResponse)
async def logout(body: TokenRevoke):
    """Revoke refresh token (logout)."""
    await _svc.logout(body.refresh_token)
    return MessageResponse(message="Logged out successfully")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    body: PasswordChangeRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Change the authenticated user's password."""
    await _svc.change_password(
        user_id=current_user.id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return MessageResponse(message="Password changed successfully")


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_active_user)):
    """Get the current authenticated user."""
    return UserResponse.model_validate(current_user)


@router.get("/sessions", response_model=list[ActiveSessionResponse])
async def list_sessions(current_user: User = Depends(get_current_active_user)):
    """List active refresh token sessions for current user."""
    sessions = await _svc.get_active_sessions(current_user.id)
    return [ActiveSessionResponse.model_validate(s) for s in sessions]


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def revoke_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """Revoke a specific session by token ID."""
    await _svc.revoke_session(current_user.id, session_id)
    return MessageResponse(message="Session revoked")
