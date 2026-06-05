"""
Central model registry.

Import all models here so Alembic autogenerate discovers every table
when it inspects Base.metadata.

Import order matters: models with FK dependencies must be imported
after the models they reference. TYPE_CHECKING guards in each model
file prevent circular imports at runtime; SQLAlchemy resolves
relationship targets lazily by string name.

STATUS:
  [x] Batch 1: base, tenant, user (Role, Permission, UserRole,
                UserTenant, RefreshToken)
  [x] Batch 2: module models (CoachingModule, ModuleVersion,
                ModuleFrameworkStep, ModulePromptTemplate,
                ModulePersona, Rubric, ModuleKnowledgeBase)
  [x] Batch 3: knowledge models (KnowledgeBase, KnowledgeSource,
                KnowledgeChunk) + session models (CoachingSession,
                ConversationMessage, RoleplaySession,
                RoleplayMessage, FeedbackReport)
  [x] Batch 4: analytics (AnalyticsEvent, AuditLog, APIUsageLog,
                AIGeneration), progress (UserProgress),
                gamification (Achievement, UserAchievement),
                notification (Notification)

Total tables: 29
"""

# ── Batch 1 ───────────────────────────────────────────────────────────────────
from app.models.base import (  # noqa: F401
    Base,
    BusinessBase,
    OptimisticLockMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.tenant import Tenant, TenantSettings  # noqa: F401
from app.models.user import (  # noqa: F401
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
    UserRole,
    UserTenant,
)

# ── Batch 2 ───────────────────────────────────────────────────────────────────
from app.models.module import (  # noqa: F401
    CoachingModule,
    ModuleFrameworkStep,
    ModuleKnowledgeBase,
    ModulePersona,
    ModulePromptTemplate,
    ModuleVersion,
    Rubric,
)

# ── Batch 3 ───────────────────────────────────────────────────────────────────
# knowledge before session: ModuleKnowledgeBase.knowledge_base FK resolves first
from app.models.knowledge import (  # noqa: F401
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeSource,
)
from app.models.session import (  # noqa: F401
    CoachingSession,
    ConversationMessage,
    FeedbackReport,
    RoleplayMessage,
    RoleplaySession,
)

# ── Batch 4 ───────────────────────────────────────────────────────────────────
from app.models.analytics import (  # noqa: F401
    AIGeneration,
    AnalyticsEvent,
    APIUsageLog,
    AuditLog,
)
from app.models.progress import UserProgress  # noqa: F401
from app.models.gamification import Achievement, UserAchievement  # noqa: F401
from app.models.notification import Notification  # noqa: F401

__all__ = [
    # ── Base mixins ───────────────────────────────────────────────────────────
    "Base",
    "BusinessBase",
    "OptimisticLockMixin",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    # ── Tenant ────────────────────────────────────────────────────────────────
    "Tenant",
    "TenantSettings",
    # ── User + RBAC ───────────────────────────────────────────────────────────
    "User",
    "Role",
    "Permission",
    "RolePermission",
    "UserRole",
    "UserTenant",
    "RefreshToken",
    # ── Modules ───────────────────────────────────────────────────────────────
    "CoachingModule",
    "ModuleVersion",
    "ModuleFrameworkStep",
    "ModulePromptTemplate",
    "ModulePersona",
    "Rubric",
    "ModuleKnowledgeBase",
    # ── Knowledge Base ────────────────────────────────────────────────────────
    "KnowledgeBase",
    "KnowledgeSource",
    "KnowledgeChunk",
    # ── Sessions + Feedback ───────────────────────────────────────────────────
    "CoachingSession",
    "ConversationMessage",
    "RoleplaySession",
    "RoleplayMessage",
    "FeedbackReport",
    # ── Analytics ────────────────────────────────────────────────────────────
    "AnalyticsEvent",
    "AuditLog",
    "APIUsageLog",
    "AIGeneration",
    # ── Progress ─────────────────────────────────────────────────────────────
    "UserProgress",
    # ── Gamification ─────────────────────────────────────────────────────────
    "Achievement",
    "UserAchievement",
    # ── Notifications ─────────────────────────────────────────────────────────
    "Notification",
]
