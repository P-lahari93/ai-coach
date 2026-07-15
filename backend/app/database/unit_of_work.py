"""
Unit of Work — transaction boundary and repository access point.

Architecture:
  The UnitOfWork (UoW) owns the AsyncSession lifetime.
  Services receive a UoW instance and access all repositories through it.
  No repository ever calls commit() or rollback() directly — only UoW does.

Tenant scoping (RLS):
  On __aenter__, the UoW tells PostgreSQL which tenant this transaction
  is allowed to see, via SET LOCAL app.current_tenant_id / app.is_superadmin.
  RLS policies (migration 011) enforce isolation using these GUCs.

  - UnitOfWork(tenant_id=x)   → RLS active, only tenant x's rows visible.
  - UnitOfWork.system()       → explicit, greppable superadmin bypass —
                                 for genuine platform/background contexts
                                 only (migrations, cron, cross-tenant admin).
  - UnitOfWork()              → NEITHER of the above. This is intentionally
                                 fail-closed: with RLS forced and no GUC
                                 set, every tenant-scoped table returns zero
                                 rows. This is the safe default — request
                                 handlers must pass tenant_id explicitly.

  Do NOT reach for UnitOfWork.system() to make an error go away — a bare
  UnitOfWork() silently returning nothing usually means a real tenant_id
  was never threaded through; fix the call site.

  Pattern:
      async with UnitOfWork(tenant_id=current_tenant_id) as uow:
          user = await uow.users.get_by_email("alice@example.com")
          await uow.commit()

  The context manager rolls back automatically on unhandled exception.

Repository access:
  Repositories are created lazily on first access (cached on the instance).

Transaction methods:
  commit()   — flush + commit the current transaction
  rollback() — roll back the current transaction
  flush()    — flush pending changes without committing (materialises PKs)
  close()    — close the session (called automatically on __aexit__)

Nesting:
  The UoW does not support nested transactions (no SAVEPOINT).

FastAPI integration:
  See app/api/v1/dependencies/uow.py — request handlers should receive a
  tenant-scoped UoW via dependency injection, not construct their own.
"""
from __future__ import annotations

import uuid
from types import TracebackType
from typing import TYPE_CHECKING

from sqlalchemy import text
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
        async with UnitOfWork(tenant_id=current_tenant_id) as uow:
            module = await uow.coaching_modules.get_by_key("sbi_feedback")
            await uow.commit()

    System / background contexts (migrations, cron, cross-tenant admin):
        async with UnitOfWork.system() as uow:
            ...
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        tenant_id: "uuid.UUID | str | None" = None,
        superadmin: bool = False,
    ) -> None:
        """
        Parameters:
            session: optional externally-managed AsyncSession.
                     When provided, the UoW does NOT close it on exit.
            tenant_id: the authenticated caller's tenant. When set, RLS
                       scopes every query in this transaction to that
                       tenant only. Validated via uuid.UUID() before use —
                       invalid input raises ValueError, never reaches SQL.
            superadmin: explicit, rare escape hatch for genuine
                        platform/background contexts. Prefer
                        UnitOfWork.system() over passing this directly.

        If neither tenant_id nor superadmin is given, the transaction is
        NOT elevated — RLS (once FORCE-enabled, see migration 011) will
        make tenant-scoped tables return zero rows. This is intentional:
        a bare UnitOfWork() must never silently see everything.
        """
        if tenant_id is not None and superadmin:
            raise ValueError(
                "UnitOfWork: pass either tenant_id or superadmin=True, not both."
            )

        self._external_session = session is not None
        self._session: AsyncSession = session or AsyncSessionLocal()

        self._tenant_id: str | None = (
            str(uuid.UUID(str(tenant_id))) if tenant_id is not None else None
        )
        self._superadmin = superadmin
        self._rls_applied = False

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

    @classmethod
    def system(cls, session: AsyncSession | None = None) -> "UnitOfWork":
        """
        Explicit, greppable superadmin/system context.

        Use ONLY for genuine platform-level operations that must see
        across all tenants: migrations, scheduled/cron jobs, superadmin
        tooling, cross-tenant analytics. Do NOT use this in a normal
        request handler just to make a missing tenant_id error go away.
        """
        return cls(session=session, superadmin=True)

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "UnitOfWork":
        await self._apply_rls_context()
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

    async def _apply_rls_context(self) -> None:
        """
        Tell PostgreSQL which tenant (or superadmin) this transaction is,
        via SET LOCAL. This only lasts for the current transaction and
        must be re-applied on every new UnitOfWork.

        tenant_id was already validated as a real UUID in __init__, so
        it is safe to interpolate directly — SET LOCAL does not support
        bind parameters in PostgreSQL's wire protocol.
        """
        if self._tenant_id is not None:
            await self._session.execute(
                text(f"SET LOCAL app.current_tenant_id = '{self._tenant_id}'")
            )
            await self._session.execute(text("SET LOCAL app.is_superadmin = 'false'"))
        elif self._superadmin:
            await self._session.execute(text("SET LOCAL app.is_superadmin = 'true'"))
        else:
            # Fail-closed: no GUC set at all. RLS policies will treat this
            # as "no tenant, not superadmin" — tenant-scoped tables return
            # zero rows rather than everything.
            await self._session.execute(text("SET LOCAL app.is_superadmin = 'false'"))
        self._rls_applied = True

    # ── Transaction control ───────────────────────────────────────────────────

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self._session.commit()

    async def rollback(self) -> None:
        """Roll back the current transaction."""
        await self._session.rollback()

    async def flush(self) -> None:
        """Flush pending ORM changes to the DB without committing."""
        await self._session.flush()

    async def close(self) -> None:
        """Close the session. Safe to call manually."""
        if not self._external_session:
            await self._session.close()

    # ── Session access (for advanced use) ────────────────────────────────────

    @property
    def session(self) -> AsyncSession:
        """Direct session access for edge cases (raw SQL, bulk ops)."""
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