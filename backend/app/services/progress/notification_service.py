# FILE: app/services/progress/notification_service.py
"""
NotificationService — in-app notification management.

Responsibilities:
- Create notifications
- List user notifications (paginated)
- Get unread count
- Mark as read (single and bulk)
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.database.unit_of_work import UnitOfWork
from app.models.notification import Notification
from app.repositories.base import Page


class NotificationService:
    """
    In-app notification lifecycle management.

    Each method opens its own UnitOfWork.
    """

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_notification(
        self,
        user_id: UUID,
        notification_type: str,
        title: str,
        message: str,
        tenant_id: UUID | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        extra: dict | None = None,
    ) -> Notification:
        """
        Create a new notification for a user.

        notification_type values:
          session_feedback_ready, achievement_earned, module_published,
          kb_processing_complete, kb_processing_failed, system_message,
          streak_reminder

        entity_type + entity_id: loose reference to the linked entity
        (no FK — polymorphic reference).

        extra: optional JSONB payload for the frontend.
        """
        async with UnitOfWork() as uow:
            notification = Notification(
                user_id=user_id,
                tenant_id=tenant_id,
                notification_type=notification_type,
                title=title,
                message=message,
                is_read=False,
                entity_type=entity_type,
                entity_id=entity_id,
                extra=extra,
            )
            uow.session.add(notification)
            await uow.session.flush()
            await uow.session.refresh(notification)
            await uow.commit()
            return notification

    # ── List ──────────────────────────────────────────────────────────────────

    async def list_notifications(
        self,
        user_id: UUID,
        tenant_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[Notification]:
        """
        List notifications for a user, newest first.

        Optionally filtered by tenant_id.
        """
        async with UnitOfWork() as uow:
            from sqlalchemy import func, select

            from app.models.notification import Notification

            base_stmt = (
                select(Notification)
                .where(Notification.user_id == user_id)
            )
            if tenant_id is not None:
                base_stmt = base_stmt.where(
                    (Notification.tenant_id == tenant_id)
                    | Notification.tenant_id.is_(None)
                )

            # Count
            count_stmt = select(func.count()).select_from(
                base_stmt.subquery()
            )
            total: int = (await uow.session.execute(count_stmt)).scalar_one()

            # Data
            data_stmt = (
                base_stmt
                .order_by(Notification.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await uow.session.execute(data_stmt)
            items = list(result.scalars().all())

            return Page(
                items=items, total=total, page=page, page_size=page_size
            )

    # ── Unread count ──────────────────────────────────────────────────────────

    async def get_unread_count(self, user_id: UUID) -> int:
        """
        Return the count of unread notifications for a user.

        Uses the partial index idx_notifications_user_unread for O(1)
        performance.
        """
        async with UnitOfWork() as uow:
            from sqlalchemy import func, select

            from app.models.notification import Notification

            stmt = (
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id)
                .where(Notification.is_read.is_(False))
            )
            return (await uow.session.execute(stmt)).scalar_one()

    # ── Mark as read ──────────────────────────────────────────────────────────

    async def mark_read(
        self, notification_id: UUID, user_id: UUID
    ) -> Notification:
        """
        Mark a single notification as read.

        Validates ownership to prevent a user from marking another
        user's notification.

        Raises:
            NotFoundError       — notification not found
            PermissionDeniedError — notification does not belong to user_id
        """
        from app.core.exceptions import NotFoundError, PermissionDeniedError

        async with UnitOfWork() as uow:
            notification = await uow.session.get(Notification, notification_id)
            if notification is None:
                raise NotFoundError("Notification", notification_id)

            if notification.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to mark this notification."
                )

            if not notification.is_read:
                notification.is_read = True
                notification.read_at = datetime.now(timezone.utc)

            await uow.commit()
            return notification

    async def mark_all_read(
        self,
        user_id: UUID,
        tenant_id: UUID | None = None,
        notification_type: str | None = None,
    ) -> int:
        """
        Mark all unread notifications as read for a user.

        Optionally filtered by tenant_id and/or notification_type.

        Returns the count of notifications marked read.
        """
        async with UnitOfWork() as uow:
            from sqlalchemy import update

            from app.models.notification import Notification

            stmt = (
                update(Notification)
                .where(Notification.user_id == user_id)
                .where(Notification.is_read.is_(False))
                .values(is_read=True, read_at=datetime.now(timezone.utc))
            )

            if tenant_id is not None:
                stmt = stmt.where(
                    (Notification.tenant_id == tenant_id)
                    | Notification.tenant_id.is_(None)
                )

            if notification_type is not None:
                stmt = stmt.where(
                    Notification.notification_type == notification_type
                )

            result = await uow.session.execute(stmt)
            await uow.commit()
            return result.rowcount
