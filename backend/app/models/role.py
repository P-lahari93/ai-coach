"""
DEPRECATED: Role, Permission, RolePermission, UserRole are now defined
in app/models/user.py alongside the User model to keep the RBAC graph
in one file and avoid circular imports.

This file is intentionally empty. It is kept to avoid broken imports
from any external tooling that may reference it.
Do NOT add models here.
"""
