# FILE: backend/app/api/v1/routers/feedback.py
from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends

from app.schemas.session.feedback_report import FeedbackReportResponse, FeedbackRatingRequest
from app.services.session.feedback_service import FeedbackService
from app.api.v1.dependencies.auth import get_current_active_user, get_current_tenant_id
from app.models.user import User

router = APIRouter()
_svc = FeedbackService()


@router.get("/{report_id}", response_model=FeedbackReportResponse)
async def get_feedback(
    report_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Get a feedback report by ID securely with tenant validation."""
    report = await _svc.get_feedback(
        report_id=report_id, 
        user_id=current_user.id, 
        tenant_id=tenant_id
    )
    return FeedbackReportResponse.model_validate(report)


@router.post("/{report_id}/rate", response_model=FeedbackReportResponse)
async def rate_feedback(
    report_id: UUID,
    body: FeedbackRatingRequest,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Submit a star rating for a feedback report safely locked within tenant boundary."""
    report = await _svc.submit_rating(
        report_id=report_id,
        user_id=current_user.id,
        rating=body.rating,
        notes=body.notes,
        tenant_id=tenant_id,
    )
    return FeedbackReportResponse.model_validate(report)