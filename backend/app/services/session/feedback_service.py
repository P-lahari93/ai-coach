# FILE: app/services/session/feedback_service.py
"""
FeedbackService — feedback report management, rating submission.

Responsibilities:
- Get feedback reports (by id, for session, for roleplay)
- List user feedback history
- Submit learner rating (1-5 stars)
- Create feedback reports (used by AI engine)

Tenant scoping:
  Every method accepts tenant_id and passes it to UnitOfWork(tenant_id=...)
  so RLS (migration 011) is active. UnitOfWork() with no args is fail-closed.
"""
from __future__ import annotations

from uuid import UUID

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.database.unit_of_work import UnitOfWork
from app.models.session import FeedbackReport
from app.repositories.base import Page
from app.repositories.session.feedback_report_repository import FeedbackReportCreate


class FeedbackService:
    """
    Feedback report lifecycle management.

    Each method opens its own UnitOfWork, scoped to the caller's tenant.
    """

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_feedback(
        self,
        report_id: UUID,
        user_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> FeedbackReport:
        """
        Fetch a feedback report by id.

        When user_id is provided, validates ownership.
        When tenant_id is provided, scopes the UnitOfWork (RLS) AND
        filters the repository read.

        Raises:
            NotFoundError       — report not found
            PermissionDeniedError — report does not belong to user_id
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            report = await uow.feedback_reports.get_by_id(
                report_id, tenant_id=tenant_id
            )
            if report is None:
                raise NotFoundError("FeedbackReport", report_id)

            if user_id is not None and report.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to access this feedback report."
                )

            return report

    async def get_feedback_for_session(
        self, session_id: UUID, tenant_id: UUID | None = None
    ) -> FeedbackReport | None:
        """
        Fetch the feedback report linked to a CoachingSession.

        Returns None if no feedback has been generated yet.

        Note: this method does not itself check ownership — callers must
        already have verified the caller owns `session_id` (both current
        callers in sessions.py do this before calling here).
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            return await uow.feedback_reports.get_by_session(
                session_id, tenant_id=tenant_id
            )

    async def get_feedback_for_roleplay(
        self, roleplay_id: UUID, tenant_id: UUID | None = None
    ) -> FeedbackReport | None:
        """
        Fetch the feedback report linked to a RoleplaySession.

        Returns None if no feedback has been generated yet.

        Note: this method does not itself check ownership — callers must
        already have verified the caller owns `roleplay_id`.
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            return await uow.feedback_reports.get_by_roleplay_session(
                roleplay_id, tenant_id=tenant_id
            )

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_user_feedback(
        self,
        user_id: UUID,
        tenant_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[FeedbackReport]:
        """
        List feedback reports for a user, newest first.

        Optionally filtered by tenant_id.
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            return await uow.feedback_reports.list_by_user(
                user_id, tenant_id=tenant_id, page=page, page_size=page_size
            )

    # ── Rating submission ─────────────────────────────────────────────────────

    async def submit_rating(
        self,
        report_id: UUID,
        user_id: UUID,
        rating: int,
        notes: str | None,
        tenant_id: UUID | None = None,
    ) -> FeedbackReport:
        """
        Submit a 1-5 star rating on a feedback report.

        Validates that rating is in range [1, 5] and that the report
        belongs to user_id.

        Raises:
            NotFoundError       — report not found
            PermissionDeniedError — report does not belong to user_id
            ValidationError     — rating out of range
        """
        if not (1 <= rating <= 5):
            raise ValidationError("Rating must be between 1 and 5.")

        async with UnitOfWork(tenant_id=tenant_id) as uow:
            report = await uow.feedback_reports.get_by_id(
                report_id, tenant_id=tenant_id
            )
            if report is None:
                raise NotFoundError("FeedbackReport", report_id)

            if report.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to rate this feedback report."
                )

            report = await uow.feedback_reports.submit_rating(
                report_id, user_rating=rating, user_notes=notes
            )
            await uow.commit()
            return report

    # ── Report creation (used by AI engine) ───────────────────────────────────

    async def create_feedback_report(
        self, data: FeedbackReportCreate, tenant_id: UUID | None = None
    ) -> FeedbackReport:
        """
        Create a new AI-generated feedback report.

        Used by the AI scoring engine after session completion.

        Exactly one of data.session_id or data.roleplay_id must be set.
        tenant_id here should normally match data.tenant_id — it scopes
        the UnitOfWork's RLS context for the insert.

        Raises:
            ValidationError — XOR constraint violated
        """
        async with UnitOfWork(tenant_id=tenant_id or data.tenant_id) as uow:
            report = await uow.feedback_reports.create_report(data)
            await uow.commit()
            return report