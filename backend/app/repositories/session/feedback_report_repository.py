"""
FeedbackReportRepository — async SQLAlchemy 2.0 implementation.

Model: FeedbackReport (UUIDPrimaryKeyMixin + TimestampMixin)
  Soft-delete:     NO — feedback reports are permanent audit records.
  Optimistic lock: NO — reports are written once by the AI engine;
                   only user_rating/user_notes are later updated
                   (low-contention, no locking needed).
  Tenant:          nullable

XOR constraint: exactly one of session_id OR roleplay_id must be set.
This is enforced by ck_feedback_report_session_xor in the DB.

Transaction contract: no commit() or rollback() here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import FeedbackReport
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import DuplicateError, NotFoundError


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class FeedbackReportCreate:
    """
    Data for creating a FeedbackReport.
    Exactly one of session_id or roleplay_id must be set.
    """

    user_id: UUID
    overall_score: Decimal
    feedback_text: str
    scores: dict = field(default_factory=dict)
    strengths: list = field(default_factory=list)
    improvements: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    session_id: Optional[UUID] = None
    roleplay_id: Optional[UUID] = None
    tenant_id: Optional[UUID] = None
    rubric_id: Optional[UUID] = None
    knowledge_used: bool = False
    model_used: Optional[str] = None
    raw_ai_response: Optional[str] = None
    next_steps: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "user_id": self.user_id,
            "overall_score": self.overall_score,
            "feedback_text": self.feedback_text,
            "scores": self.scores,
            "strengths": self.strengths,
            "improvements": self.improvements,
            "recommendations": self.recommendations,
            "citations": self.citations,
            "session_id": self.session_id,
            "roleplay_id": self.roleplay_id,
            "tenant_id": self.tenant_id,
            "rubric_id": self.rubric_id,
            "knowledge_used": self.knowledge_used,
            "model_used": self.model_used,
            "raw_ai_response": self.raw_ai_response,
            "next_steps": self.next_steps,
        }


@dataclass
class FeedbackReportUpdate:
    """Partial update — only learner-submitted fields."""

    user_rating: Optional[int] = None
    user_notes: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result: dict = {}
        if self.user_rating is not None:
            result["user_rating"] = self.user_rating
        if self.user_notes is not None:
            result["user_notes"] = self.user_notes
        return result


# ── Repository ────────────────────────────────────────────────────────────────

class FeedbackReportRepository(
    BaseRepository[FeedbackReport, FeedbackReportCreate, FeedbackReportUpdate]
):
    """
    Database operations for FeedbackReport.

    Reports are write-once (created by AI engine) then read-many.
    The only mutable fields after creation are user_rating and user_notes.
    No soft-delete — reports are permanent records.
    """

    model = FeedbackReport

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_by_id(
        self,
        report_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> FeedbackReport | None:
        """Fetch a feedback report by id, with optional tenant guard."""
        stmt = select(FeedbackReport).where(FeedbackReport.id == report_id)
        if tenant_id is not None:
            stmt = stmt.where(FeedbackReport.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_session(
        self, coaching_session_id: UUID
    ) -> FeedbackReport | None:
        """
        Fetch the most recent feedback report for a CoachingSession.
        Uses ORDER BY created_at DESC + LIMIT 1 to safely handle retries
        that may have produced multiple reports for the same session.
        """
        stmt = (
            select(FeedbackReport)
            .where(FeedbackReport.session_id == coaching_session_id)
            .order_by(FeedbackReport.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_roleplay_session(
        self, roleplay_session_id: UUID
    ) -> FeedbackReport | None:
        """
        Fetch the most recent feedback report for a RoleplaySession.
        Uses ORDER BY created_at DESC + LIMIT 1 to safely handle retries
        that may have produced multiple reports for the same session.
        """
        stmt = (
            select(FeedbackReport)
            .where(FeedbackReport.roleplay_id == roleplay_session_id)
            .order_by(FeedbackReport.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_by_user(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[FeedbackReport]:
        """
        List feedback reports for a user, newest first.
        Uses idx_feedback_user_created.
        """
        base = (
            select(FeedbackReport)
            .where(FeedbackReport.user_id == user_id)
        )
        if tenant_id is not None:
            base = base.where(FeedbackReport.tenant_id == tenant_id)

        total: int = (
            await self._session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        data_stmt = (
            base
            .order_by(FeedbackReport.created_at.desc())
            .offset(self._offset(page, page_size))
            .limit(page_size)
        )
        result = await self._session.execute(data_stmt)
        return Page(
            items=list(result.scalars().all()),
            total=total,
            page=page,
            page_size=page_size,
        )

    async def average_score_for_user(
        self,
        user_id: UUID,
        *,
        module_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> Decimal | None:
        """
        Compute the average overall_score for a user's feedback reports.
        Returns None when the user has no reports yet.
        """
        from app.models.session import CoachingSession

        stmt = select(func.avg(FeedbackReport.overall_score)).where(
            FeedbackReport.user_id == user_id
        )
        if tenant_id is not None:
            stmt = stmt.where(FeedbackReport.tenant_id == tenant_id)
        if module_id is not None:
            # Filter via the coaching session link
            stmt = stmt.where(
                FeedbackReport.session_id.in_(
                    select(CoachingSession.id).where(
                        CoachingSession.module_id == module_id
                    )
                )
            )
        raw = (await self._session.execute(stmt)).scalar_one_or_none()
        return Decimal(str(raw)) if raw is not None else None

    # ── User rating update ────────────────────────────────────────────────────

    async def submit_rating(
        self,
        report_id: UUID,
        *,
        user_rating: int,
        user_notes: str | None = None,
    ) -> FeedbackReport:
        """
        Persist a learner's 1-5 star rating (and optional note).
        The DB CHECK constraint ck_feedback_user_rating enforces 1-5.

        Returns the updated report.
        Raises NotFoundError when the report does not exist.
        """
        values: dict = {"user_rating": user_rating}
        if user_notes is not None:
            values["user_notes"] = user_notes

        stmt = (
            update(FeedbackReport)
            .where(FeedbackReport.id == report_id)
            .values(**values)
            .returning(FeedbackReport)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("FeedbackReport", report_id)
        return row

    # ── Create report ─────────────────────────────────────────────────────────

    async def create_report(
        self, data: FeedbackReportCreate
    ) -> FeedbackReport:
        """
        Persist a new AI-generated feedback report.

        Enforces the XOR invariant at the Python layer before INSERT
        so the error is meaningful rather than a raw CHECK violation.

        Transaction note: no rollback here — session owner handles it.
        """
        if (data.session_id is None) == (data.roleplay_id is None):
            raise ValueError(
                "FeedbackReport requires exactly one of session_id or "
                "roleplay_id to be set."
            )
        return await self.create(data)

    # ── Override create ───────────────────────────────────────────────────────

    async def create(  # type: ignore[override]
        self, data: FeedbackReportCreate
    ) -> FeedbackReport:
        try:
            report = FeedbackReport(
                user_id=data.user_id,
                overall_score=data.overall_score,
                feedback_text=data.feedback_text,
                scores=data.scores,
                strengths=data.strengths,
                improvements=data.improvements,
                recommendations=data.recommendations,
                citations=data.citations,
                session_id=data.session_id,
                roleplay_id=data.roleplay_id,
                tenant_id=data.tenant_id,
                rubric_id=data.rubric_id,
                knowledge_used=data.knowledge_used,
                model_used=data.model_used,
                raw_ai_response=data.raw_ai_response,
                next_steps=data.next_steps,
            )
            self._session.add(report)
            await self._session.flush()
            await self._session.refresh(report)
            return report
        except IntegrityError as exc:
            raise DuplicateError(
                entity="FeedbackReport",
                field="session_id|roleplay_id",
                value="constraint violation",
            ) from exc
