"""
Security utilities — re-exported from app.core.security package.

NOTE: app/core/security/ directory takes precedence over this file.
All implementations live in the security/ sub-package.
This file is kept for backwards compatibility with any direct imports.
"""
# Re-export everything so `from app.core.security import X` works
# regardless of whether the .py or the package is resolved.
from app.core.security.jwt import (  # noqa: F401
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.security.password import hash_password, verify_password  # noqa: F401
