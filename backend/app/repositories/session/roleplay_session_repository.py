# FILE: backend/app/repositories/session/roleplay_session_repository.py
"""
RoleplaySessionRepository — async SQLAlchemy 2.0 implementation.

Model: RoleplaySession (BusinessBase + OptimisticLockMixin)
  Soft-delete:     yes
  Optimistic lock: yes (version)
  Tenant:          nullable

Status lifecycle: active → paused | completed | abandoned

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

from app.models.session import RoleplayMessage, RoleplaySession
from app.repositories.base import BaseRepository, Page
from app.repositories.exceptions import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    OptimisticLockError,
)


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class RoleplaySessionCreate:
    user_id: UUID
    module_id: UUID
    module_version_id: UUID
    tenant_id: Optional[UUID] = None
    persona_id: Optional[UUID] = None
    scenario_prompt: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = False) -> dict:  # noqa: ARG002
        return {
            "user_id": self.user_id,
            "module_id": self.module_id,
            "module_version_id": self.module_version_id,
            "tenant_id": self.tenant_id,
            "persona_id": self.persona_id,
            "scenario_prompt": self.scenario_prompt,
            "status": "active",
            "turn_count": 0,
            "context": {},
        }


@dataclass
class RoleplaySessionUpdate:
    context: Optional[dict] = None
    scenario_prompt: Optional[str] = None

    def model_dump(self, *, exclude_unset: bool = True) -> dict:
        result: dict = {}
        if self.context is not None:
            result["context"] = self.context
        if self.scenario_prompt is not None:
            result["scenario_prompt"] = self.scenario_prompt
        return result


@dataclass
class RoleplayMessageCreate:
    session_id: UUID
    turn_number: int
    role: str          # user | persona
    content: str
    emotion_detected: Optional[str] = None
    coaching_note: Optional[str] = None
    metadata: Optional[dict] = None


# ── Repository ────────────────────────────────────────────────────────────────

class RoleplaySessionRepository(
    BaseRepository[RoleplaySession, RoleplaySessionCreate, RoleplaySessionUpdate]
):
    """Database operations for RoleplaySession and RoleplayMessage."""

    model = RoleplaySession

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_by_id(
        self,
        session_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession | None:
        stmt = (
            select(RoleplaySession)
            .where(RoleplaySession.id == session_id)
            .where(RoleplaySession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleplaySession.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_messages(
        self,
        session_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession | None:
        """Fetch roleplay session with conversation history attached under a strict tenant view."""
        from sqlalchemy import select as sa_select
        from app.models.session import RoleplayMessage

        stmt = (
            select(RoleplaySession)
            .where(RoleplaySession.id == session_id)
            .where(RoleplaySession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleplaySession.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        session = result.scalar_one_or_none()
        if session is None:
            return None

        msgs_stmt = (
            sa_select(RoleplayMessage)
            .where(RoleplayMessage.session_id == session_id)
            .order_by(RoleplayMessage.turn_number)
        )
        msgs_result = await self._session.execute(msgs_stmt)
        session.__dict__['_messages_loaded'] = list(msgs_result.scalars().all())
        return session

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_by_user(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[RoleplaySession]:
        """List roleplay sessions for a user, newest first."""
        base = (
            select(RoleplaySession)
            .where(RoleplaySession.user_id == user_id)
            .where(RoleplaySession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            base = base.where(RoleplaySession.tenant_id == tenant_id)
        if status is not None:
            base = base.where(RoleplaySession.status == status)

        total: int = (
            await self._session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        data_stmt = (
            base
            .order_by(RoleplaySession.created_at.desc())
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
    ) -> Page[RoleplaySession]:
        """List roleplay sessions for a module."""
        base = (
            select(RoleplaySession)
            .where(RoleplaySession.module_id == module_id)
            .where(RoleplaySession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            base = base.where(RoleplaySession.tenant_id == tenant_id)
        if status is not None:
            base = base.where(RoleplaySession.status == status)

        total: int = (
            await self._session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        data_stmt = (
            base
            .order_by(RoleplaySession.created_at.desc())
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
    ) -> list[RoleplaySession]:
        """Return all active or paused roleplay sessions for a user."""
        stmt = (
            select(RoleplaySession)
            .where(RoleplaySession.user_id == user_id)
            .where(RoleplaySession.status.in_(["active", "paused"]))
            .where(RoleplaySession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleplaySession.tenant_id == tenant_id)
        stmt = stmt.order_by(RoleplaySession.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Status transitions ────────────────────────────────────────────────────

    async def start_session(
        self, data: RoleplaySessionCreate
    ) -> RoleplaySession:
        """Create and return a new active roleplay session."""
        return await self.create(data)

    async def complete_session(
        self,
        session_id: UUID,
        *,
        final_score: Decimal | None,
        expected_version: int,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """Transition active/paused → completed."""
        now = datetime.now(timezone.utc)
        stmt = (
            update(RoleplaySession)
            .where(RoleplaySession.id == session_id)
            .where(RoleplaySession.deleted_at.is_(None))
            .where(RoleplaySession.version == expected_version)
            .where(RoleplaySession.status.in_(["active", "paused"]))
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleplaySession.tenant_id == tenant_id)
            
        stmt = stmt.values(
            status="completed",
            final_score=final_score,
            completed_at=now,
            version=RoleplaySession.version + 1,
        ).returning(RoleplaySession)
        
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            check = await self.get_by_id(session_id, tenant_id=tenant_id)
            if check is None:
                raise NotFoundError("RoleplaySession", session_id)
            if check.status == "completed":
                raise ConflictError(f"RoleplaySession '{session_id}' is already completed.")
            raise OptimisticLockError("RoleplaySession", session_id, expected_version)
        return row

    async def pause_session(
        self,
        session_id: UUID,
        *,
        expected_version: int,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """Transition active → paused."""
        stmt = (
            update(RoleplaySession)
            .where(RoleplaySession.id == session_id)
            .where(RoleplaySession.deleted_at.is_(None))
            .where(RoleplaySession.version == expected_version)
            .where(RoleplaySession.status == "active")
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleplaySession.tenant_id == tenant_id)
            
        stmt = stmt.values(status="paused", version=RoleplaySession.version + 1).returning(RoleplaySession)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            await self._raise_update_failure(session_id, expected_version)
        return row  # type: ignore[return-value]

    async def abandon_session(
        self,
        session_id: UUID,
        *,
        expected_version: int,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """Transition active/paused → abandoned."""
        stmt = (
            update(RoleplaySession)
            .where(RoleplaySession.id == session_id)
            .where(RoleplaySession.deleted_at.is_(None))
            .where(RoleplaySession.version == expected_version)
            .where(RoleplaySession.status.in_(["active", "paused"]))
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleplaySession.tenant_id == tenant_id)
            
        stmt = stmt.values(status="abandoned", version=RoleplaySession.version + 1).returning(RoleplaySession)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            await self._raise_update_failure(session_id, expected_version)
        return row  # type: ignore[return-value]

    async def increment_turn_count(
        self,
        session_id: UUID,
        *,
        expected_version: int,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """Atomically increment turn_count + bump version."""
        stmt = (
            update(RoleplaySession)
            .where(RoleplaySession.id == session_id)
            .where(RoleplaySession.version == expected_version)
            .where(RoleplaySession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleplaySession.tenant_id == tenant_id)
            
        stmt = stmt.values(
            turn_count=RoleplaySession.turn_count + 1,
            version=RoleplaySession.version + 1,
        ).returning(RoleplaySession)
        
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            await self._raise_update_failure(session_id, expected_version)
        return row  # type: ignore[return-value]

    async def update_context(
        self,
        session_id: UUID,
        context: dict,
        *,
        expected_version: int,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """Update the mutable engine context bag on a session."""
        stmt = (
            update(RoleplaySession)
            .where(RoleplaySession.id == session_id)
            .where(RoleplaySession.version == expected_version)
            .where(RoleplaySession.deleted_at.is_(None))
        )
        if tenant_id is not None:
            stmt = stmt.where(RoleplaySession.tenant_id == tenant_id)
            
        stmt = stmt.values(context=context, version=RoleplaySession.version + 1).returning(RoleplaySession)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            await self._raise_update_failure(session_id, expected_version)
        return row  # type: ignore[return-value]

    # ── Message management ────────────────────────────────────────────────────

    async def append_message(
        self, data: RoleplayMessageCreate, tenant_id: UUID | None = None
    ) -> RoleplayMessage:
        """Append a turn message to a roleplay session validation pipeline."""
        try:
            msg = RoleplayMessage(
                session_id=data.session_id,
                turn_number=data.turn_number,
                role=data.role,
                content=data.content,
                emotion_detected=data.emotion_detected,
                coaching_note=data.coaching_note,
                metadata_=data.metadata or {},
            )
            self._session.add(msg)
            await self._session.flush()
            await self._session.refresh(msg)
            return msg
        except IntegrityError as exc:
            raise DuplicateError(
                entity="RoleplayMessage",
                field="(session_id, turn_number, role)",
                value=f"{data.session_id}:{data.turn_number}:{data.role}",
            ) from exc

    # ── Override create ───────────────────────────────────────────────────────

    async def create(  # type: ignore[override]
        self, data: RoleplaySessionCreate
    ) -> RoleplaySession:
        try:
            rs = RoleplaySession(
                user_id=data.user_id,
                module_id=data.module_id,
                module_version_id=data.module_version_id,
                tenant_id=data.tenant_id,
                persona_id=data.persona_id,
                scenario_prompt=data.scenario_prompt,
                status="active",
                turn_count=0,
                context={},
            )
            self._session.add(rs)
            await self._session.flush()
            await self._session.refresh(rs)
            return rs
        except IntegrityError as exc:
            raise DuplicateError(
                entity="RoleplaySession",
                field="id",
                value="conflict",
            ) from exc