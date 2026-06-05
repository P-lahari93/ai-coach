"""
Progress, gamification, and notification schemas package.
"""
from app.schemas.progress.user_progress import (
    LeaderboardEntry,
    UserProgressDetail,
    UserProgressResponse,
    UserProgressSummary,
)
from app.schemas.progress.achievement import (
    AchievementCriteriaSchema,
    AchievementResponse,
    AchievementSummary,
    UserAchievementResponse,
)
from app.schemas.progress.notification import (
    BulkNotificationMarkRead,
    NotificationResponse,
    NotificationSummary,
    NotificationUpdate,
    UnreadCountResponse,
)

__all__ = [
    # user_progress
    "UserProgressResponse",
    "UserProgressSummary",
    "UserProgressDetail",
    "LeaderboardEntry",
    # achievement
    "AchievementCriteriaSchema",
    "AchievementResponse",
    "AchievementSummary",
    "UserAchievementResponse",
    # notification
    "NotificationResponse",
    "NotificationSummary",
    "NotificationUpdate",
    "BulkNotificationMarkRead",
    "UnreadCountResponse",
]
