"""
Notification schemas.

Covers:
  - NotificationResponse  — full notification record
  - NotificationSummary   — lightweight list/bell-panel projection
  - NotificationUpdate    — PATCH body for marking read / bulk-read

Notification type values (enforced by DB CHECK):
    session_feedback_ready | achievement_earned | module_published |
    kb_processing_complete | kb_processing_failed |
    system_message | streak_reminder

entity_type + entity_id are loose polymorphic references used by
the frontend to build action URLs:
    session_feedback_ready  →  /sessions/{entity_id}/feedback
    achievement_earned      →  /achievements/{entity_id}
    module_published        →  /modules/{entity_id}
    kb_processing_complete  →  /knowledge/{entity_id}
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Shared literal ────────────────────────────────────────────────────────────

NotificationType = Literal[
    "session_feedback_ready",
    "achievement_earned",
    "module_published",
    "kb_processing_complete",
    "kb_processing_failed",
    "system_message",
    "streak_reminder",
]


# ── NotificationResponse ──────────────────────────────────────────────────────

class NotificationResponse(BaseModel):
    """
    Full notification record.

    Returned by GET /notifications/{id} and the notification service.

    extra: optional JSONB payload used by the frontend to enrich the
    notification display without an additional API call.
    Example: {'score': 87.5, 'module_name': 'SBI Feedback'}
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    tenant_id: UUID | None = None
    notification_type: NotificationType = Field(
        ...,
        description=(
            "Categorical tag used by the frontend to select the icon "
            "and build the action URL"
        ),
    )
    title: str = Field(..., max_length=255, description="Short headline")
    message: str = Field(..., description="Full notification body text")
    is_read: bool = Field(
        ...,
        description="True once the user has acknowledged this notification",
    )
    read_at: datetime | None = Field(
        default=None,
        description="Timestamp when the notification was marked read",
    )
    entity_type: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "Type of the linked entity, e.g. 'coaching_session', "
            "'achievement', 'module'"
        ),
    )
    entity_id: UUID | None = Field(
        default=None,
        description="UUID of the linked entity; combined with entity_type to build URL",
    )
    extra: dict[str, Any] | None = Field(
        default=None,
        description="Optional JSONB payload for frontend enrichment",
    )
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_read_at_consistency(self) -> "NotificationResponse":
        """read_at must be set when is_read is True."""
        if self.is_read and self.read_at is None:
            # Tolerate missing read_at for legacy rows — do not raise,
            # just leave as None. is_read is the authoritative flag.
            pass
        if not self.is_read and self.read_at is not None:
            raise ValueError("read_at must be None when is_read is False")
        return self


# ── NotificationSummary ───────────────────────────────────────────────────────

class NotificationSummary(BaseModel):
    """
    Lightweight projection for the notification bell panel and badge count.

    Omits message body, extra payload, and timestamps to keep the
    list payload small for high-frequency polling or WebSocket delivery.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    notification_type: NotificationType
    title: str
    is_read: bool
    entity_type: str | None
    entity_id: UUID | None
    created_at: datetime


# ── NotificationUpdate ────────────────────────────────────────────────────────

class NotificationUpdate(BaseModel):
    """
    PATCH /notifications/{id}

    Marks a single notification as read or unread.
    is_read is the only mutable field — notifications are otherwise
    immutable after creation.

    read_at is set automatically by the service layer when
    is_read transitions to True; it is not accepted from clients.
    """

    is_read: bool = Field(
        ...,
        description="Set to true to mark the notification as read",
    )


# ── BulkNotificationUpdate (convenience for mark-all-read) ───────────────────

class BulkNotificationMarkRead(BaseModel):
    """
    POST /notifications/mark-all-read

    Marks all unread notifications for the authenticated user as read.
    Optionally scoped to a specific notification_type.
    """

    notification_type: NotificationType | None = Field(
        default=None,
        description=(
            "If provided, only notifications of this type are marked read. "
            "If None, all unread notifications are marked read."
        ),
    )


# ── UnreadCountResponse ───────────────────────────────────────────────────────

class UnreadCountResponse(BaseModel):
    """
    Response for GET /notifications/unread-count.

    count: number of unread notifications for the authenticated user.
    Uses the partial index idx_notifications_user_unread for O(1) reads.
    """

    count: int = Field(..., ge=0, description="Total unread notification count")
