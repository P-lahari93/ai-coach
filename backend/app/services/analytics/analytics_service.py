# FILE: app/services/analytics/analytics_service.py
"""
AnalyticsService — event tracking and dashboard KPI aggregation.

Responsibilities:
- Track events (fire-and-forget or synchronous)
- Compute dashboard metrics (active users, sessions, avg score)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.database.unit_of_work import UnitOfWork
from app.repositories.analytics.analytics_repository import AnalyticsEventCreate


class AnalyticsService:
    """
    Analytics event tracking and dashboard aggregation.

    Each method opens its own UnitOfWork.
    """

    # ── Event tracking ────────────────────────────────────────────────────────

    async def track_event(
        self,
        event_type: str,
        user_id: UUID | None = None,
        tenant_id: UUID | None = None,
        properties: dict | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        session_id_ref: UUID | None = None,
    ) -> None:
        """
        Track a behavioural event.

        Typically called via asyncio.create_task() (fire-and-forget) so
        event tracking does not block the response path.

        event_type examples:
          session_started, session_completed, session_abandoned,
          feedback_viewed, module_viewed, achievement_earned
        """
        async with UnitOfWork() as uow:
            await uow.analytics.track_event(
                AnalyticsEventCreate(
                    event_type=event_type,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    properties=properties or {},
                    entity_type=entity_type,
                    entity_id=entity_id,
                    session_id_ref=session_id_ref,
                )
            )
            await uow.commit()

    # ── Dashboard KPIs ────────────────────────────────────────────────────────

    async def get_dashboard(
        self, tenant_id: UUID | None = None, days: int = 30
    ) -> dict:
        """
        Compute dashboard KPIs for a tenant.

        When tenant_id is None (superadmin), aggregates across the
        entire platform.

        Returns a dict with keys:
          - active_users:       count of unique users with events in last N days
          - sessions_started:   count of session_started events
          - sessions_completed: count of session_completed events
          - sessions_abandoned: count of session_abandoned events
          - completion_rate:    sessions_completed / sessions_started (%)
          - avg_score:          average overall_score from feedback reports
          - period_days:        the days parameter passed in

        Stub implementation: returns placeholder values. In production,
        this would aggregate across AnalyticsEvent and FeedbackReport
        tables using SQL window functions and CTEs.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with UnitOfWork() as uow:
            # Funnel counts
            funnel = await uow.analytics.session_funnel(
                tenant_id=tenant_id or UUID("00000000-0000-0000-0000-000000000000"),
                since=since,
            )

            sessions_started = funnel.get("session_started", 0)
            sessions_completed = funnel.get("session_completed", 0)
            sessions_abandoned = funnel.get("session_abandoned", 0)

            completion_rate = (
                (sessions_completed / sessions_started * 100)
                if sessions_started > 0
                else 0.0
            )

            # TODO: compute avg_score from FeedbackReport
            # For now, return placeholder
            avg_score = 0.0

            # TODO: compute active_users (distinct user_id from events)
            active_users = 0

            return {
                "active_users": active_users,
                "sessions_started": sessions_started,
                "sessions_completed": sessions_completed,
                "sessions_abandoned": sessions_abandoned,
                "completion_rate": round(completion_rate, 2),
                "avg_score": avg_score,
                "period_days": days,
            }
