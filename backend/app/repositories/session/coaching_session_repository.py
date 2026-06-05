"""
CoachingSessionRepository — async SQLAlchemy 2.0 implementation.

Model: CoachingSession (BusinessBase + OptimisticLockMixin)
  Soft-delete:     yes (deleted_at from BusinessBase)
  Optimistic lock: yes (version from OptimisticLockMixin)
  Tenant:          nullable (tenant_id may be NULL for platform sessions)

Status lifecycle:  in_progress → completed | abandoned

Transaction contract: no commit() or rollback() here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import CoachingSession, ConversationMessage
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    OptimisticLockError,
)


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class CoachingSessionCreate:
    user_id: UUID
    module_id: UUID
    module_version_id: UUID
    tenant_id: Optional[UUID] = None
    intake_data: Optional[dict] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "user_id": self.user_id,
            "module_id": self.module_id,
            "module_version_id": self.module_version_id,
            "tenant_id": self.tenant_id,
            "intake_data": self.intake_data or {},
            "status": "in_progress",
        }


@dataclass
class CoachingSessionUpdate:
    intake_data: Optional[dict] = None
    final_score: Optional[Decimal] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result: dict = {}
        if self.intake_data is not None:
            result["intake_data"] = self.intake_data
        if self.final_score is not None:
            result["final_score"] = self.final_score
        return result


@dataclass
class ConversationMessageCreate:
    session_id: UUID
    role: str           # user | assistant | system
    content: str
    message_index: int
    token_count: Optional[int] = None
    metadata: Optional[dict] = None


# ── Repository ────────────────────────────────────────────────────────────────

class CoachingSessionRepository(
    BaseRepository[CoachingSession, CoachingSessionCreate, CoachingSessionUpdate]
):
    """
    Database operations for CoachingSession and ConversationMessage.

    ConversationMessage is append-only and managed here because messages
    have no independent access pattern outside their parent session.
    """

    model = CoachingSession

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_by_id(
        self,
        session_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> CoachingSession | None:
        """Fetch a session by id, with optional tenant guard."""
        stmt = (
            select(CoachingSession)
            .where(CoachingSession.id == session_id)
            .where(CoachingSession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(CoachingSession.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_messages(
        self, session_id: UUID
    ) -> CoachingSession | None:
        """Load a session, then separately load its conversation messages."""
        from sqlalchemy import select as sa_select
        from app.models.session import ConversationMessage

        stmt = (
            select(CoachingSession)
            .where(CoachingSession.id == session_id)
            .where(CoachingSession.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        session = result.scalar_one_or_none()
        if session is None:
            return None

        # Load messages separately (write_only doesn't support selectinload)
        msgs_stmt = (
            sa_select(ConversationMessage)
            .where(ConversationMessage.session_id == session_id)
            .order_by(ConversationMessage.message_index)
        )
        msgs_result = await self._session.execute(msgs_stmt)
        # Store on instance for access — the router reads session directly
        session.__dict__['_messages_loaded'] = list(msgs_result.scalars().all())
        return session

    async def get_with_feedback(
        self, session_id: UUID
    ) -> CoachingSession | None:
        """Load a session with its feedback report eagerly."""
        stmt = (
            select(CoachingSession)
            .where(CoachingSession.id == session_id)
            .where(CoachingSession.deleted_at.is_(None))
            .options(selectinload(CoachingSession.feedback_report))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_by_user(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[CoachingSession]:
        """
        List sessions for a user, newest first.
        Optionally filtered by tenant_id and/or status.
        Uses idx_coaching_sessions_user_tenant_status.
        """
        base = (
            select(CoachingSession)
            .where(CoachingSession.user_id == user_id)
            .where(CoachingSession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            base = base.where(CoachingSession.tenant_id == tenant_id)
        if status is not None:
            base = base.where(CoachingSession.status == status)

        total: int = (
            await self._session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        data_stmt = (
            base
            .order_by(CoachingSession.created_at.desc())
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

    async def list_by_module(
        self,
        module_id: UUID,
        *,
        tenant_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[CoachingSession]:
        """
        List sessions for a module.
        Uses idx_coaching_sessions_module_status.
        """
        base = (
            select(CoachingSession)
            .where(CoachingSession.module_id == module_id)
            .where(CoachingSession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            base = base.where(CoachingSession.tenant_id == tenant_id)
        if status is not None:
            base = base.where(CoachingSession.status == status)

        total: int = (
            await self._session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        data_stmt = (
            base
            .order_by(CoachingSession.created_at.desc())
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

    async def list_active_sessions(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> list[CoachingSession]:
        """
        Return all in_progress sessions for a user.
        Used to detect / resume incomplete sessions.
        """
        stmt = (
            select(CoachingSession)
            .where(CoachingSession.user_id == user_id)
            .where(CoachingSession.status == "in_progress")
            .where(CoachingSession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(CoachingSession.tenant_id == tenant_id)
        stmt = stmt.order_by(CoachingSession.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_completed(
        self,
        user_id: UUID,
        *,
        module_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> int:
        """Count completed sessions for a user, optionally per module."""
        stmt = (
            select(func.count())
            .select_from(CoachingSession)
            .where(CoachingSession.user_id == user_id)
            .where(CoachingSession.status == "completed")
            .where(CoachingSession.deleted_at.is_(None))
        )
        if module_id is not None:
            stmt = stmt.where(CoachingSession.module_id == module_id)
        if tenant_id is not None:
            stmt = stmt.where(CoachingSession.tenant_id == tenant_id)
        return (await self._session.execute(stmt)).scalar_one()

    # ── Status transitions ────────────────────────────────────────────────────

    async def complete_session(
        self,
        session_id: UUID,
        *,
        final_score: Decimal,
        duration_seconds: int,
        expected_version: int,
    ) -> CoachingSession:
        """
        Transition in_progress → completed with score and duration.
        Version-gated to prevent double-completion races.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(CoachingSession)
            .where(CoachingSession.id == session_id)
            .where(CoachingSession.deleted_at.is_(None))
            .where(CoachingSession.version == expected_version)
            .where(CoachingSession.status == "in_progress")
            .values(
                status="completed",
                final_score=final_score,
                duration_seconds=duration_seconds,
                completed_at=now,
                version=CoachingSession.version + 1,
            )
            .returning(CoachingSession)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            check = await self.get_by_id(session_id)
            if check is None:
                raise NotFoundError("CoachingSession", session_id)
            if check.status == "completed":
                raise ConflictError(f"CoachingSession '{session_id}' is already completed.")
            raise OptimisticLockError("CoachingSession", session_id, expected_version)
        return row

    async def abandon_session(
        self,
        session_id: UUID,
        *,
        expected_version: int,
    ) -> CoachingSession:
        """Transition in_progress → abandoned."""
        stmt = (
            update(CoachingSession)
            .where(CoachingSession.id == session_id)
            .where(CoachingSession.deleted_at.is_(None))
            .where(CoachingSession.version == expected_version)
            .where(CoachingSession.status == "in_progress")
            .values(status="abandoned", version=CoachingSession.version + 1)
            .returning(CoachingSession)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            await self._raise_update_failure(session_id, expected_version)
        return row  # type: ignore[return-value]

    # ── Message management ────────────────────────────────────────────────────

    async def append_message(
        self, data: ConversationMessageCreate
    ) -> ConversationMessage:
        """
        Append a message to a coaching session conversation.
        Append-only — never updates existing messages.
        """
        try:
            msg = ConversationMessage(
                session_id=data.session_id,
                role=data.role,
                content=data.content,
                message_index=data.message_index,
                token_count=data.token_count,
                metadata_=data.metadata or {},
            )
            self._session.add(msg)
            await self._session.flush()
            await self._session.refresh(msg)
            return msg
        except IntegrityError as exc:
            raise DuplicateError(
                entity="ConversationMessage",
                field="(session_id, message_index)",
                value=f"{data.session_id}:{data.message_index}",
            ) from exc

    async def get_message_count(self, session_id: UUID) -> int:
        """Return the number of messages in a session."""
        stmt = (
            select(func.count())
            .select_from(ConversationMessage)
            .where(ConversationMessage.session_id == session_id)
        )
        return (await self._session.execute(stmt)).scalar_one()

    # ── Override create ───────────────────────────────────────────────────────

    async def create(  # type: ignore[override]
        self, data: CoachingSessionCreate
    ) -> CoachingSession:
        try:
            session_obj = CoachingSession(
                user_id=data.user_id,
                module_id=data.module_id,
                module_version_id=data.module_version_id,
                tenant_id=data.tenant_id,
                intake_data=data.intake_data or {},
                status="in_progress",
            )
            self._session.add(session_obj)
            await self._session.flush()
            await self._session.refresh(session_obj)
            return session_obj
        except IntegrityError as exc:
            raise DuplicateError(
                entity="CoachingSession",
                field="id",
                value="conflict",
            ) from exc
