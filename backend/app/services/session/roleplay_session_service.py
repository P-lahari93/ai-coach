# FILE: app/services/session/roleplay_session_service.py
"""
RoleplaySessionService — roleplay session lifecycle, messages, status transitions.

Responsibilities:
- Create roleplay sessions (pin current module version + persona)
- Add roleplay messages
- Pause/resume/complete/abandon sessions
- Update mutable context bag

Tenant scoping:
  Every method accepts tenant_id and passes it to UnitOfWork(tenant_id=...)
  so RLS (migration 011) is active. UnitOfWork() with no args is fail-closed.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.database.unit_of_work import UnitOfWork
from app.models.session import RoleplayMessage, RoleplaySession
from app.repositories.base import Page
from app.repositories.session.roleplay_session_repository import (
    RoleplayMessageCreate,
    RoleplaySessionCreate,
)


class RoleplaySessionService:
    """
    Roleplay session lifecycle management.

    Each method opens its own UnitOfWork, scoped to the caller's tenant.
    """

    # ── Session creation ──────────────────────────────────────────────────────

    async def create_session(
        self,
        user_id: UUID,
        module_id: UUID,
        tenant_id: UUID | None = None,
        persona_id: UUID | None = None,
        scenario_prompt: str | None = None,
    ) -> RoleplaySession:
        """
        Create a new roleplay session.

        Pins the current module version and optional persona.

        Raises:
            NotFoundError — module has no current version
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            version = await uow.module_versions.get_current_version(module_id)
            if version is None:
                raise NotFoundError(
                    "ModuleVersion",
                    f"No current version for module {module_id}",
                )

            session = await uow.roleplay_sessions.create(
                RoleplaySessionCreate(
                    user_id=user_id,
                    module_id=module_id,
                    module_version_id=version.id,
                    tenant_id=tenant_id,
                    persona_id=persona_id,
                    scenario_prompt=scenario_prompt,
                )
            )
            await uow.commit()
            return session

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def get_session(
        self,
        session_id: UUID,
        user_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """
        Fetch a roleplay session by id.

        When user_id is provided, validates ownership.
        tenant_id scopes the UnitOfWork (RLS) AND is passed to the
        repository as defence-in-depth.

        Raises:
            NotFoundError       — session not found
            PermissionDeniedError — session does not belong to user_id
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.roleplay_sessions.get_by_id(
                session_id, tenant_id=tenant_id
            )
            if session is None:
                raise NotFoundError("RoleplaySession", session_id)

            if user_id is not None and session.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to access this session."
                )

            return session

    async def get_session_detail(
        self,
        session_id: UUID,
        user_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """
        Fetch a session with messages eagerly loaded.

        When user_id is provided, validates that the session belongs to them.

        Raises:
            NotFoundError — session not found
            PermissionDeniedError — session does not belong to user_id
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.roleplay_sessions.get_with_messages(
                session_id, tenant_id=tenant_id
            )
            if session is None:
                raise NotFoundError("RoleplaySession", session_id)

            if user_id is not None and session.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to access this session."
                )

            return session

    # ── Listing ───────────────────────────────────────────────────────────────

    async def list_sessions(
        self,
        user_id: UUID,
        tenant_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[RoleplaySession]:
        """
        List roleplay sessions for a user, newest first.

        Optionally filtered by tenant_id and/or status.
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            return await uow.roleplay_sessions.list_by_user(
                user_id,
                tenant_id=tenant_id,
                status=status,
                page=page,
                page_size=page_size,
            )

    # ── Message management ────────────────────────────────────────────────────

    async def add_message(
        self,
        session_id: UUID,
        role: str,
        content: str,
        turn_number: int,
        emotion_detected: str | None = None,
        coaching_note: str | None = None,
        tenant_id: UUID | None = None,
    ) -> RoleplayMessage:
        """
        Append a turn message to a roleplay session.

        turn_number must be explicitly provided (computed by caller).

        Raises:
            NotFoundError — session not found
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.roleplay_sessions.get_by_id(
                session_id, tenant_id=tenant_id
            )
            if session is None:
                raise NotFoundError("RoleplaySession", session_id)

            message = await uow.roleplay_sessions.append_message(
                RoleplayMessageCreate(
                    session_id=session_id,
                    turn_number=turn_number,
                    role=role,
                    content=content,
                    emotion_detected=emotion_detected,
                    coaching_note=coaching_note,
                )
            )
            await uow.commit()
            return message

    # ── Status transitions ────────────────────────────────────────────────────

    async def pause_session(
        self, session_id: UUID, user_id: UUID, tenant_id: UUID | None = None
    ) -> RoleplaySession:
        """
        Transition active → paused.

        Raises:
            NotFoundError       — session not found
            PermissionDeniedError — session does not belong to user_id
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.roleplay_sessions.get_by_id(
                session_id, tenant_id=tenant_id
            )
            if session is None:
                raise NotFoundError("RoleplaySession", session_id)

            if session.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to pause this session."
                )

            session = await uow.roleplay_sessions.pause_session(
                session_id, expected_version=session.version
            )
            await uow.commit()
            return session

    async def resume_session(
        self, session_id: UUID, user_id: UUID, tenant_id: UUID | None = None
    ) -> RoleplaySession:
        """
        Transition paused → active.

        Note: repository does not have an explicit resume method;
        this is a status update via raw update.

        Raises:
            NotFoundError       — session not found
            PermissionDeniedError — session does not belong to user_id
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.roleplay_sessions.get_by_id(
                session_id, tenant_id=tenant_id
            )
            if session is None:
                raise NotFoundError("RoleplaySession", session_id)

            if session.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to resume this session."
                )

            # Direct status update is not supported by the repository yet.
            raise NotImplementedError("Resume not yet implemented in repository.")

    async def complete_session(
        self,
        session_id: UUID,
        final_score: Decimal | None,
        user_id: UUID,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """
        Transition active/paused → completed.

        Raises:
            NotFoundError       — session not found
            PermissionDeniedError — session does not belong to user_id
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.roleplay_sessions.get_by_id(
                session_id, tenant_id=tenant_id
            )
            if session is None:
                raise NotFoundError("RoleplaySession", session_id)

            if session.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to complete this session."
                )

            session = await uow.roleplay_sessions.complete_session(
                session_id,
                final_score=final_score,
                expected_version=session.version,
            )
            await uow.commit()
            return session

    async def abandon_session(
        self, session_id: UUID, user_id: UUID, tenant_id: UUID | None = None
    ) -> RoleplaySession:
        """
        Transition active/paused → abandoned.

        Raises:
            NotFoundError       — session not found
            PermissionDeniedError — session does not belong to user_id
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.roleplay_sessions.get_by_id(
                session_id, tenant_id=tenant_id
            )
            if session is None:
                raise NotFoundError("RoleplaySession", session_id)

            if session.user_id != user_id:
                raise PermissionDeniedError(
                    "You do not have permission to abandon this session."
                )

            session = await uow.roleplay_sessions.abandon_session(
                session_id, expected_version=session.version
            )
            await uow.commit()
            return session

    # ── Context update ────────────────────────────────────────────────────────

    async def update_context(
        self,
        session_id: UUID,
        context_updates: dict,
        tenant_id: UUID | None = None,
    ) -> RoleplaySession:
        """
        Update the mutable context JSONB bag on a roleplay session.

        Used by the AI engine to track scenario state across turns.

        Raises:
            NotFoundError — session not found
        """
        async with UnitOfWork(tenant_id=tenant_id) as uow:
            session = await uow.roleplay_sessions.get_by_id(
                session_id, tenant_id=tenant_id
            )
            if session is None:
                raise NotFoundError("RoleplaySession", session_id)

            session = await uow.roleplay_sessions.update_context(
                session_id,
                context=context_updates,
                expected_version=session.version,
            )
            await uow.commit()
            return session