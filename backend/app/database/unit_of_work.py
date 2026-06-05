"""
Unit of Work — transaction boundary and repository access point.

Architecture:
  The UnitOfWork (UoW) owns the AsyncSession lifetime.
  Services receive a UoW instance and access all repositories through it.
  No repository ever calls commit() or rollback() directly — only UoW does.

  Pattern:
      async with UnitOfWork() as uow:
          user = await uow.users.get_by_email("alice@example.com")
          await uow.commit()

  The context manager rolls back automatically on unhandled exception.

Repository access:
  Repositories are created lazily on first access (cached on the instance).
  This avoids constructing unused repositories on every request.

Transaction methods:
  commit()   — flush + commit the current transaction
  rollback() — roll back the current transaction
  flush()    — flush pending changes without committing (materialises PKs)
  close()    — close the session (called automatically on __aexit__)

Nesting:
  The UoW does not support nested transactions (no SAVEPOINT).
  For nested operations, use the same UoW and call flush() to detect
  constraint violations before the outer commit.

FastAPI integration:
  The recommended pattern for route handlers is via a FastAPI dependency
  that yields a UoW instance (see app/api/v1/dependencies/uow.py).
  Do NOT share a UoW across concurrent request handlers.

Design decisions:
  - Repositories are injected with the same session so all writes
    within a single UoW are in the same transaction.
  - expire_on_commit=False on the session factory means loaded ORM
    objects remain accessible after commit without an extra SELECT.
  - autoflush=False means no implicit flushes during query execution;
    the service layer controls when to flush.
"""
from __future__ import annotations

from types import TracebackType
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import AsyncSessionLocal
from app.repositories.analytics.analytics_repository import AnalyticsRepository
from app.repositories.auth.permission_repository import PermissionRepository
from app.repositories.auth.refresh_token_repository import RefreshTokenRepository
from app.repositories.auth.role_repository import RoleRepository
from app.repositories.auth.user_repository import UserRepository
from app.repositories.knowledge.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge.knowledge_chunk_repository import KnowledgeChunkRepository
from app.repositories.module.coaching_module_repository import CoachingModuleRepository
from app.repositories.module.module_version_repository import ModuleVersionRepository
from app.repositories.progress.user_progress_repository import UserProgressRepository
from app.repositories.session.coaching_session_repository import CoachingSessionRepository
from app.repositories.session.feedback_report_repository import FeedbackReportRepository
from app.repositories.session.roleplay_session_repository import RoleplaySessionRepository


class UnitOfWork:
    """
    Async Unit of Work.

    Owns the AsyncSession and exposes all repositories as lazy properties.
    Must be used as an async context manager.

    Usage:
        async with UnitOfWork() as uow:
            module = await uow.coaching_modules.get_by_key("sbi_feedback")
            await uow.commit()

    Or with an existing session (e.g. in tests or FastAPI dependencies):
        async with UnitOfWork(session=existing_session) as uow:
            ...
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
    ) -> None:
        """
        Parameters:
            session: optional externally-managed AsyncSession.
                     When provided, the UoW does NOT close it on exit —
                     the caller owns the session lifetime.
                     When None (default), the UoW creates and owns its
                     own session from AsyncSessionLocal.
        """
        self._external_session = session is not None
        self._session: AsyncSession = session or AsyncSessionLocal()

        # Lazy repository cache — populated on first access
        self._users: UserRepository | None = None
        self._roles: RoleRepository | None = None
        self._permissions: PermissionRepository | None = None
        self._refresh_tokens: RefreshTokenRepository | None = None
        self._coaching_modules: CoachingModuleRepository | None = None
        self._module_versions: ModuleVersionRepository | None = None
        self._knowledge_bases: KnowledgeBaseRepository | None = None
        self._knowledge_chunks: KnowledgeChunkRepository | None = None
        self._coaching_sessions: CoachingSessionRepository | None = None
        self._roleplay_sessions: RoleplaySessionRepository | None = None
        self._feedback_reports: FeedbackReportRepository | None = None
        self._user_progress: UserProgressRepository | None = None
        self._analytics: AnalyticsRepository | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "UnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
        await self.close()

    # ── Transaction control ───────────────────────────────────────────────────

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self._session.commit()

    async def rollback(self) -> None:
        """Roll back the current transaction."""
        await self._session.rollback()

    async def flush(self) -> None:
        """
        Flush pending ORM changes to the DB without committing.

        Use this to materialise PKs and trigger DB-level constraint
        checks before the end of the transaction.
        """
        await self._session.flush()

    async def close(self) -> None:
        """
        Close the session.

        Called automatically by __aexit__. Safe to call manually.
        When using an external session, this is a no-op.
        """
        if not self._external_session:
            await self._session.close()

    # ── Session access (for advanced use) ────────────────────────────────────

    @property
    def session(self) -> AsyncSession:
        """
        Direct session access for edge cases (e.g. raw SQL, bulk ops).
        Prefer using repositories over direct session access.
        """
        return self._session

    # ── Auth repositories ─────────────────────────────────────────────────────

    @property
    def users(self) -> UserRepository:
        if self._users is None:
            self._users = UserRepository(self._session)
        return self._users

    @property
    def roles(self) -> RoleRepository:
        if self._roles is None:
            self._roles = RoleRepository(self._session)
        return self._roles

    @property
    def permissions(self) -> PermissionRepository:
        if self._permissions is None:
            self._permissions = PermissionRepository(self._session)
        return self._permissions

    @property
    def refresh_tokens(self) -> RefreshTokenRepository:
        if self._refresh_tokens is None:
            self._refresh_tokens = RefreshTokenRepository(self._session)
        return self._refresh_tokens

    # ── Module repositories ───────────────────────────────────────────────────

    @property
    def coaching_modules(self) -> CoachingModuleRepository:
        if self._coaching_modules is None:
            self._coaching_modules = CoachingModuleRepository(self._session)
        return self._coaching_modules

    @property
    def module_versions(self) -> ModuleVersionRepository:
        if self._module_versions is None:
            self._module_versions = ModuleVersionRepository(self._session)
        return self._module_versions

    # ── Knowledge repositories ────────────────────────────────────────────────

    @property
    def knowledge_bases(self) -> KnowledgeBaseRepository:
        if self._knowledge_bases is None:
            self._knowledge_bases = KnowledgeBaseRepository(self._session)
        return self._knowledge_bases

    @property
    def knowledge_chunks(self) -> KnowledgeChunkRepository:
        if self._knowledge_chunks is None:
            self._knowledge_chunks = KnowledgeChunkRepository(self._session)
        return self._knowledge_chunks

    # ── Session repositories ──────────────────────────────────────────────────

    @property
    def coaching_sessions(self) -> CoachingSessionRepository:
        if self._coaching_sessions is None:
            self._coaching_sessions = CoachingSessionRepository(self._session)
        return self._coaching_sessions

    @property
    def roleplay_sessions(self) -> RoleplaySessionRepository:
        if self._roleplay_sessions is None:
            self._roleplay_sessions = RoleplaySessionRepository(self._session)
        return self._roleplay_sessions

    @property
    def feedback_reports(self) -> FeedbackReportRepository:
        if self._feedback_reports is None:
            self._feedback_reports = FeedbackReportRepository(self._session)
        return self._feedback_reports

    # ── Progress repository ───────────────────────────────────────────────────

    @property
    def user_progress(self) -> UserProgressRepository:
        if self._user_progress is None:
            self._user_progress = UserProgressRepository(self._session)
        return self._user_progress

    # ── Analytics repository ──────────────────────────────────────────────────

    @property
    def analytics(self) -> AnalyticsRepository:
        if self._analytics is None:
            self._analytics = AnalyticsRepository(self._session)
        return self._analytics
