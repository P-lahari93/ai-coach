"""
Authentication request schemas — login, registration, password operations.

These schemas cover the inbound request bodies for auth endpoints only.
Response schemas live in token.py (TokenPair) and user.py (UserResponse).
"""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    """
    POST /auth/login

    email is normalised to lowercase before lookup so that
    'Alice@Example.com' and 'alice@example.com' resolve to the same account.
    """

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.lower().strip()


class RegisterRequest(BaseModel):
    """
    POST /auth/register

    Used for self-service registration flows (when enabled).
    Admin-created users go through a separate invite endpoint.
    password is plain text here — bcrypt-hashed by the auth service.
    """

    email: EmailStr
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Minimum 8 characters",
    )
    full_name: str = Field(..., min_length=1, max_length=255)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.lower().strip()


class PasswordResetRequest(BaseModel):
    """
    POST /auth/password-reset/request

    Initiates a password reset flow. The service sends a reset link
    to the email address if an active account exists.
    No information is returned about whether the account exists
    (prevents user enumeration).
    """

    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.lower().strip()


class PasswordResetConfirm(BaseModel):
    """
    POST /auth/password-reset/confirm

    Completes the password reset flow using the one-time token from the
    reset email. token is a signed JWT or opaque string issued by the
    auth service — validated there, not here.
    """

    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordChangeRequest(BaseModel):
    """
    POST /auth/password — authenticated password change.

    Requires the current password to prevent session-hijacking attacks
    where an attacker with a valid session changes the victim's password.
    """

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
