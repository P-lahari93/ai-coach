"""Token schemas — JWT access token and refresh token envelopes."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ── Issued to client ──────────────────────────────────────────────────────────

class TokenPair(BaseModel):
    """
    Response body returned after a successful login or token refresh.

    access_token:  short-lived JWT (default 30 min).
    refresh_token: long-lived opaque token (default 7 days).
                   Stored client-side (HttpOnly cookie or secure storage).
    token_type:    always 'bearer' per OAuth2 convention.
    expires_in:    access token lifetime in seconds.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(
        ..., description="Access token lifetime in seconds", examples=[1800]
    )


class AccessTokenResponse(BaseModel):
    """
    Response body returned when only a new access token is issued
    (e.g. silent refresh where the refresh token is kept in a cookie).
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ── Inbound requests ──────────────────────────────────────────────────────────

class RefreshRequest(BaseModel):
    """
    Body for POST /auth/refresh when the refresh token is sent in the body
    rather than a cookie. One of the two patterns is used depending on client.
    """

    refresh_token: str


class TokenRevoke(BaseModel):
    """Body for POST /auth/logout — revoke a specific refresh token."""

    refresh_token: str


# ── Internal JWT payload ──────────────────────────────────────────────────────

class AccessTokenPayload(BaseModel):
    """
    Claims decoded from a JWT access token.
    Not exposed directly to API clients — used internally by the auth service.

    sub:         user UUID as string (standard JWT claim)
    type:        token type ('access')
    tenant_id:   active tenant UUID string (optional)
    roles:       list of role names from the token
    exp:         expiry Unix timestamp (standard JWT claim)
    iat:         issued-at Unix timestamp (standard JWT claim)
    """

    sub: str  # UUID stored as string in JWT
    type: str = "access"
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    is_superadmin: bool = False
    exp: int
    iat: int

    @property
    def user_id(self) -> UUID:
        """Return sub as UUID."""
        from uuid import UUID as _UUID
        return _UUID(self.sub)


# ── Active session info ───────────────────────────────────────────────────────

class ActiveSessionResponse(BaseModel):
    """
    Represents one active refresh token / device session.
    Returned by GET /auth/sessions.
    """

    id: UUID
    device_hint: str | None
    ip_address: str | None
    created_at: datetime
    expires_at: datetime
    is_current: bool = False
