from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from app.schemas.common import MessageResponse
from app.services.progress.progress_service import ProgressService
from app.services.progress.notification_service import NotificationService
from app.api.v1.dependencies.auth import get_current_active_user, get_current_tenant_id
from app.models.user import User
from pydantic import BaseModel

router = APIRouter()
_progress_svc = ProgressService()
_notif_svc = NotificationService()


class NotifUpdateRequest(BaseModel):
    is_read: bool


@router.get("/")
async def list_progress(
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """List the current user's progress across all modules."""
    items = await _progress_svc.list_user_progress(user_id=current_user.id, tenant_id=tenant_id)
    return [{"id": str(p.id), "module_id": str(p.module_id), "completion_percent": float(p.completion_percent), "sessions_completed": p.sessions_completed, "best_score": float(p.best_score) if p.best_score else None, "streak_days": p.streak_days} for p in items]


@router.get("/module/{module_id}")
async def get_module_progress(
    module_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Get the current user's progress for a specific module."""
    p = await _progress_svc.get_progress(user_id=current_user.id, module_id=module_id, tenant_id=tenant_id)
    if p is None:
        return {"module_id": str(module_id), "completion_percent": 0, "sessions_completed": 0, "streak_days": 0}
    return {"id": str(p.id), "module_id": str(p.module_id), "completion_percent": float(p.completion_percent), "sessions_completed": p.sessions_completed, "best_score": float(p.best_score) if p.best_score else None, "streak_days": p.streak_days}


@router.get("/leaderboard/{module_id}")
async def leaderboard(
    module_id: UUID,
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Get leaderboard for a module."""
    items = await _progress_svc.get_leaderboard(module_id=module_id, tenant_id=tenant_id, limit=limit)
    return [{"rank": i + 1, "user_id": str(p.user_id), "average_score": float(p.average_score) if p.average_score else None, "sessions_completed": p.sessions_completed} for i, p in enumerate(items)]


@router.get("/notifications")
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """List notifications for the current user."""
    result = await _notif_svc.list_notifications(user_id=current_user.id, tenant_id=tenant_id, page=page, page_size=page_size)
    return {"items": [{"id": str(n.id), "title": n.title, "message": n.message, "type": n.notification_type, "is_read": n.is_read, "created_at": n.created_at.isoformat()} for n in result.items], "total": result.total}


@router.get("/notifications/unread-count")
async def unread_count(current_user: User = Depends(get_current_active_user)):
    """Get count of unread notifications."""
    count = await _notif_svc.get_unread_count(current_user.id)
    return {"count": count}


@router.patch("/notifications/{notification_id}")
async def update_notification(
    notification_id: UUID,
    body: NotifUpdateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Mark a notification as read or unread."""
    n = await _notif_svc.mark_read(notification_id=notification_id, user_id=current_user.id)
    return {"id": str(n.id), "is_read": n.is_read}


@router.post("/notifications/mark-all-read", response_model=MessageResponse)
async def mark_all_read(
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Mark all notifications as read."""
    count = await _notif_svc.mark_all_read(user_id=current_user.id, tenant_id=tenant_id)
    return MessageResponse(message=f"Marked {count} notifications as read")
