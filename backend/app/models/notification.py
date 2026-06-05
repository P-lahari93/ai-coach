"""
Notification model — in-app notification system.

Architecture decisions:
────────────────────────

  Notification
    One row per notification per user. Append-only during normal
    operations (notifications are created, read, never edited).

    notification_type:
        Categorical tag used by the frontend to choose the correct
        icon and action URL. Enforced by CHECK constraint.

        Values:
          session_feedback_ready  — AI feedback has been generated
          achievement_earned      — gamification award
          module_published        — a new module is available
          kb_processing_complete  — KB ingestion finished
          kb_processing_failed    — KB ingestion failed
          system_message          — platform-level announcement
          streak_reminder         — re-engagement nudge

    entity_type / entity_id:
        Loose reference to the entity this notification is about.
        No FK — allows notifications to reference any entity type
        without a polymorphic FK.
        Frontend uses entity_type + entity_id to build the action URL:
          session_feedback_ready: /sessions/{entity_id}/feedback
          achievement_earned:     /achievements/{entity_id}
          module_published:       /modules/{entity_id}

    is_read / read_at:
        is_read flips to True when the user opens/acknowledges the
        notification. read_at records exactly when.
        Partial index on is_read=false makes unread count O(1).

    Performance strategy for 100k+ users:
    ──────────────────────────────────────
    The unread badge count query is:
        SELECT COUNT(*) FROM notifications
        WHERE user_id = :uid AND is_read = false
    With the partial index idx_notifications_user_unread this is
    an indexed scan over only unread rows, not the full history.
    Historical (read) notifications are in the same table but
    excluded from the hot path. No archival needed for MVP.

    Bulk mark-as-read:
        UPDATE notifications
        SET is_read = true, read_at = now()
        WHERE user_id = :uid AND is_read = false
    Single indexed write, no read-modify-write loop needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.tenant import Tenant


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    In-app notification record.

    Created by the notification service when domain events occur.
    Read by the user via the notifications API.
    Never edited after creation — only is_read/read_at are updated.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # Critical: unread count query — partial index on unread only
        Index(
            "idx_notifications_user_unread",
            "user_id",
            "created_at",
            postgresql_where=text("is_read = false"),
        ),
        # Full history for the notifications panel (all, newest first)
        Index("idx_notifications_user_all", "user_id", "created_at"),
        # Tenant-level notification broadcast support
        Index(
            "idx_notifications_tenant_type",
            "tenant_id",
            "notification_type",
            "created_at",
        ),
        CheckConstraint(
            "notification_type IN ("
            "'session_feedback_ready', "
            "'achievement_earned', "
            "'module_published', "
            "'kb_processing_complete', "
            "'kb_processing_failed', "
            "'system_message', "
            "'streak_reminder'"
            ")",
            name="ck_notification_type",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = platform-level notification",
    )
    notification_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "session_feedback_ready | achievement_earned | "
            "module_published | kb_processing_complete | "
            "kb_processing_failed | system_message | streak_reminder"
        ),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Short headline shown in the notification list",
    )
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full notification body text",
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set when user acknowledges the notification",
    )
    entity_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            "Type of the linked entity, e.g. 'coaching_session', "
            "'achievement', 'module'. Used to build action URL."
        ),
    )
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="UUID of the linked entity — no FK (polymorphic reference)",
    )
    extra: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        comment=(
            "Optional extra payload for the frontend, e.g. "
            "{'score': 87.5, 'module_name': 'SBI Feedback'}"
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="notifications",
        lazy="select",
    )
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    def mark_read(self) -> None:
        """
        Mark this notification as read.
        Caller must flush/commit the session after calling this.
        """
        from datetime import timezone
        if not self.is_read:
            self.is_read = True
            self.read_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (
            f"<Notification type={self.notification_type!r} "
            f"user={self.user_id} read={self.is_read}>"
        )
