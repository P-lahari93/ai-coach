"""
Analytics schemas package.
"""
from app.schemas.analytics.events import (
    AIGenerationResponse,
    AnalyticsEventResponse,
    AuditLogResponse,
    TrackEventRequest,
)
from app.schemas.analytics.reports import (
    AnalyticsDashboardResponse,
    APIUsageReport,
    APIUsageReportRow,
    SessionFunnelReport,
    SessionFunnelStep,
    TokenUsageReport,
    TokenUsageRow,
)

__all__ = [
    # events
    "TrackEventRequest",
    "AnalyticsEventResponse",
    "AuditLogResponse",
    "AIGenerationResponse",
    # reports
    "APIUsageReportRow",
    "APIUsageReport",
    "TokenUsageRow",
    "TokenUsageReport",
    "SessionFunnelStep",
    "SessionFunnelReport",
    "AnalyticsDashboardResponse",
]
