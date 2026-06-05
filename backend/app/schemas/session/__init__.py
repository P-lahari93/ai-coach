"""
Session schemas package.

Exports all public schemas for coaching sessions, roleplay sessions,
and AI-generated feedback reports.
"""
from app.schemas.session.coaching_session import (
    CoachingSessionBase,
    CoachingSessionCreate,
    CoachingSessionDetail,
    CoachingSessionResponse,
    CoachingSessionSummary,
    CoachingSessionUpdate,
    ConversationMessageSchema,
    FeedbackReportEmbedded,
    IntakeDataSchema,
    SessionCompleteRequest,
)
from app.schemas.session.feedback_report import (
    CitationSchema,
    FeedbackRatingRequest,
    FeedbackReportResponse,
    ImprovementRecommendationSchema,
    OverallScoreSchema,
    ScoreDimensionSchema,
)
from app.schemas.session.roleplay_session import (
    RoleplayFeedbackEmbedded,
    RoleplayMessageSchema,
    RoleplaySessionBase,
    RoleplaySessionCreate,
    RoleplaySessionDetail,
    RoleplaySessionResponse,
    RoleplaySessionSummary,
    RoleplaySessionUpdate,
    RoleplayTurnRequest,
    RoleplayTurnResponse,
)

__all__ = [
    # coaching_session
    "CoachingSessionBase",
    "CoachingSessionCreate",
    "CoachingSessionUpdate",
    "CoachingSessionResponse",
    "CoachingSessionSummary",
    "CoachingSessionDetail",
    "SessionCompleteRequest",
    "IntakeDataSchema",
    "ConversationMessageSchema",
    "FeedbackReportEmbedded",
    # roleplay_session
    "RoleplaySessionBase",
    "RoleplaySessionCreate",
    "RoleplaySessionUpdate",
    "RoleplaySessionResponse",
    "RoleplaySessionSummary",
    "RoleplaySessionDetail",
    "RoleplayTurnRequest",
    "RoleplayTurnResponse",
    "RoleplayMessageSchema",
    "RoleplayFeedbackEmbedded",
    # feedback_report
    "FeedbackReportResponse",
    "FeedbackRatingRequest",
    "CitationSchema",
    "ScoreDimensionSchema",
    "OverallScoreSchema",
    "ImprovementRecommendationSchema",
]
