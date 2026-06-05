from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends
from app.schemas.analytics.events import TrackEventRequest
from app.schemas.common import MessageResponse
from app.services.analytics.analytics_service import AnalyticsService
from app.api.v1.dependencies.auth import get_current_active_user, get_current_tenant_id
from app.models.user import User

router = APIRouter()
_svc = AnalyticsService()


@router.post("/events", response_model=MessageResponse)
async def track_event(
    body: TrackEventRequest,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Track a behavioral analytics event."""
    await _svc.track_event(
        event_type=body.event_type,
        user_id=current_user.id,
        tenant_id=tenant_id,
        properties=body.properties,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        session_id_ref=body.session_id_ref,
    )
    return MessageResponse(message="Event tracked")


@router.get("/dashboard")
async def dashboard(
    days: int = 30,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Get analytics dashboard metrics."""
    data = await _svc.get_dashboard(tenant_id=tenant_id, days=days)
    return data
