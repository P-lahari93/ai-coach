from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends
from app.schemas.session.feedback_report import FeedbackReportResponse, FeedbackRatingRequest
from app.services.session.feedback_service import FeedbackService
from app.api.v1.dependencies.auth import get_current_active_user
from app.models.user import User

router = APIRouter()
_svc = FeedbackService()


@router.get("/{report_id}", response_model=FeedbackReportResponse)
async def get_feedback(
    report_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """Get a feedback report by ID."""
    report = await _svc.get_feedback(report_id, user_id=current_user.id)
    return FeedbackReportResponse.model_validate(report)


@router.post("/{report_id}/rate", response_model=FeedbackReportResponse)
async def rate_feedback(
    report_id: UUID,
    body: FeedbackRatingRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Submit a star rating for a feedback report."""
    report = await _svc.submit_rating(
        report_id=report_id,
        user_id=current_user.id,
        rating=body.rating,
        notes=body.notes,
    )
    return FeedbackReportResponse.model_validate(report)
