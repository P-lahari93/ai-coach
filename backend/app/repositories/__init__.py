"""
Repository layer exports.

All repositories are exported from here so service-layer imports
never need to reference the nested package path directly.

Usage:
    from app.repositories import UserRepository, RoleRepository
    from app.repositories import PermissionRepository, RefreshTokenRepository
    from app.repositories import CoachingModuleRepository, ModuleVersionRepository
    from app.repositories.base import Page
    from app.repositories.exceptions import NotFoundError, OptimisticLockError
"""
from app.repositories.auth.permission_repository import (
    PermissionCreate,
    PermissionRepository,
    PermissionUpdate,
)
from app.repositories.auth.refresh_token_repository import (
    RefreshTokenCreate,
    RefreshTokenRepository,
    RefreshTokenUpdate,
)
from app.repositories.auth.role_repository import RoleCreate, RoleRepository, RoleUpdate
from app.repositories.auth.user_repository import UserCreate, UserRepository, UserUpdate
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    OptimisticLockError,
    RepositoryError,
)
from app.repositories.knowledge.knowledge_base_repository import (
    KnowledgeBaseCreate,
    KnowledgeBaseRepository,
    KnowledgeBaseUpdate,
    KnowledgeSourceCreate,
)
from app.repositories.knowledge.knowledge_chunk_repository import (
    ChunkSearchResult,
    KnowledgeChunkCreate,
    KnowledgeChunkRepository,
    KnowledgeChunkUpdate,
)
from app.repositories.module.coaching_module_repository import (
    CoachingModuleCreate,
    CoachingModuleRepository,
    CoachingModuleUpdate,
)
from app.repositories.module.module_version_repository import (
    ModuleVersionCreate,
    ModuleVersionRepository,
    ModuleVersionUpdate,
)

__all__ = [
    # Base
    "BaseRepository",
    "Page",
    # Exceptions
    "RepositoryError",
    "NotFoundError",
    "OptimisticLockError",
    "DuplicateError",
    "ConflictError",
    # Auth
    "UserRepository",
    "UserCreate",
    "UserUpdate",
    "RoleRepository",
    "RoleCreate",
    "RoleUpdate",
    "PermissionRepository",
    "PermissionCreate",
    "PermissionUpdate",
    "RefreshTokenRepository",
    "RefreshTokenCreate",
    "RefreshTokenUpdate",
    # Module
    "CoachingModuleRepository",
    "CoachingModuleCreate",
    "CoachingModuleUpdate",
    "ModuleVersionRepository",
    "ModuleVersionCreate",
    "ModuleVersionUpdate",
    # Knowledge
    "KnowledgeBaseRepository",
    "KnowledgeBaseCreate",
    "KnowledgeBaseUpdate",
    "KnowledgeSourceCreate",
    "KnowledgeChunkRepository",
    "KnowledgeChunkCreate",
    "KnowledgeChunkUpdate",
    "ChunkSearchResult",
]
