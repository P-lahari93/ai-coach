"""
AnalyticsRepository — async SQLAlchemy 2.0 implementation.

Models: AnalyticsEvent, AuditLog, APIUsageLog, AIGeneration
Design: Write-optimised append-only tables.

  AnalyticsEvent  — fire-and-forget behavioural events
  AuditLog        — synchronous compliance trail (written in same tx)
  APIUsageLog     — fire-and-forget HTTP request log
  AIGeneration    — synchronous LLM call telemetry

Does NOT extend BaseRepository — all tables are append-only with no
soft-delete, no optimistic lock, and no standard CRUD. The interface
exposes only INSERT methods and targeted aggregate reads.

Transaction contract: no commit() or rollback() here.
  - AuditLog.write() is synchronous — caller must commit.
  - AIGeneration.write() is synchronous — caller must commit.
  - track_event() and write_api_log() are fire-and-forget by convention
    (callers wrap them in asyncio.create_task).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import (
    AIGeneration,
    AnalyticsEvent,
    AuditLog,
    APIUsageLog,
)


# ── Data carriers ─────────────────────────────────────────────────────────────

@dataclass
class AnalyticsEventCreate:
    event_type: str
    user_id: Optional[UUID] = None
    tenant_id: Optional[UUID] = None
    event_name: Optional[str] = None
    properties: dict = field(default_factory=dict)
    entity_type: Optional[str] = None
    entity_id: Optional[UUID] = None
    session_id_ref: Optional[UUID] = None
    occurred_at: Optional[datetime] = None


@dataclass
class AuditLogCreate:
    action: str
    entity_type: str
    actor_user_id: Optional[UUID] = None
    tenant_id: Optional[UUID] = None
    entity_id: Optional[UUID] = None
    before_state: Optional[dict] = None
    after_state: Optional[dict] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


@dataclass
class APIUsageLogCreate:
    endpoint: str
    method: str
    status_code: int
    latency_ms: int
    user_id: Optional[UUID] = None
    tenant_id: Optional[UUID] = None
    request_size_bytes: Optional[int] = None
    response_size_bytes: Optional[int] = None
    ip_address: Optional[str] = None


@dataclass
class AIGenerationCreate:
    generation_type: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    user_id: Optional[UUID] = None
    tenant_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    session_type: Optional[str] = None
    response_time_ms: Optional[int] = None
    was_cached: bool = False
    error_message: Optional[str] = None


# ── Repository ────────────────────────────────────────────────────────────────

class AnalyticsRepository:
    """
    Write-only insert methods + targeted aggregate reads for all
    analytics and audit models.

    Does not inherit BaseRepository — none of the standard CRUD
    patterns apply to append-only tables.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── AnalyticsEvent ────────────────────────────────────────────────────────

    async def track_event(self, data: AnalyticsEventCreate) -> AnalyticsEvent:
        """
        Insert a behavioural event record.

        Typically called via asyncio.create_task() (fire-and-forget).
        occurred_at defaults to now() when not provided.
        """
        now = datetime.now(timezone.utc)
        event = AnalyticsEvent(
            user_id=data.user_id,
            tenant_id=data.tenant_id,
            event_type=data.event_type,
            event_name=data.event_name,
            properties=data.properties,
            entity_type=data.entity_type,
            entity_id=data.entity_id,
            session_id_ref=data.session_id_ref,
            occurred_at=data.occurred_at or now,
            created_at=now,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def count_events_by_type(
        self,
        event_type: str,
        *,
        tenant_id: UUID,
        since: datetime,
        until: datetime | None = None,
    ) -> int:
        """Count events of a given type for a tenant in a time range."""
        stmt = (
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(AnalyticsEvent.event_type == event_type)
            .where(AnalyticsEvent.tenant_id == tenant_id)
            .where(AnalyticsEvent.occurred_at >= since)
        )
        if until is not None:
            stmt = stmt.where(AnalyticsEvent.occurred_at < until)
        return (await self._session.execute(stmt)).scalar_one()

    async def session_funnel(
        self,
        tenant_id: UUID,
        *,
        since: datetime,
    ) -> dict[str, int]:
        """
        Return started / completed / abandoned counts for coaching
        sessions in a tenant since a given date.

        Uses AnalyticsEvent rows — no direct session table scan.
        """
        results: dict[str, int] = {}
        for event_type in ("session_started", "session_completed", "session_abandoned"):
            results[event_type] = await self.count_events_by_type(
                event_type, tenant_id=tenant_id, since=since
            )
        return results

    # ── AuditLog ──────────────────────────────────────────────────────────────

    async def write_audit_log(self, data: AuditLogCreate) -> AuditLog:
        """
        Insert a compliance audit log entry.

        Synchronous — must be committed as part of the triggering
        operation's transaction so audit and mutation are atomic.
        """
        entry = AuditLog(
            actor_user_id=data.actor_user_id,
            tenant_id=data.tenant_id,
            action=data.action,
            entity_type=data.entity_type,
            entity_id=data.entity_id,
            before_state=data.before_state,
            after_state=data.after_state,
            ip_address=data.ip_address,
            user_agent=data.user_agent,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def audit_trail_for_entity(
        self,
        entity_type: str,
        entity_id: UUID,
        *,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Return audit history for a specific entity, newest first."""
        stmt = (
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type)
            .where(AuditLog.entity_id == entity_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def audit_trail_for_actor(
        self,
        actor_user_id: UUID,
        *,
        tenant_id: UUID | None = None,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Return recent audit actions by a specific actor."""
        stmt = (
            select(AuditLog)
            .where(AuditLog.actor_user_id == actor_user_id)
        )
        if tenant_id is not None:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── APIUsageLog ───────────────────────────────────────────────────────────

    async def write_api_log(self, data: APIUsageLogCreate) -> None:
        """
        Insert an API request usage log.

        Fire-and-forget — no return value needed.
        Callers wrap this in asyncio.create_task() to avoid blocking
        the response path.
        """
        log = APIUsageLog(
            user_id=data.user_id,
            tenant_id=data.tenant_id,
            endpoint=data.endpoint,
            method=data.method,
            status_code=data.status_code,
            latency_ms=data.latency_ms,
            request_size_bytes=data.request_size_bytes,
            response_size_bytes=data.response_size_bytes,
            ip_address=data.ip_address,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(log)
        await self._session.flush()

    # ── AIGeneration ──────────────────────────────────────────────────────────

    async def write_ai_generation(
        self, data: AIGenerationCreate
    ) -> AIGeneration:
        """
        Insert an LLM generation telemetry record.

        Synchronous — committed with the parent operation's transaction
        so cost tracking is always consistent with what was generated.
        """
        gen = AIGeneration(
            user_id=data.user_id,
            tenant_id=data.tenant_id,
            session_id=data.session_id,
            session_type=data.session_type,
            generation_type=data.generation_type,
            model_name=data.model_name,
            prompt_tokens=data.prompt_tokens,
            completion_tokens=data.completion_tokens,
            total_tokens=data.total_tokens,
            response_time_ms=data.response_time_ms,
            was_cached=data.was_cached,
            error_message=data.error_message,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(gen)
        await self._session.flush()
        return gen

    async def token_usage_by_tenant(
        self,
        tenant_id: UUID,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> dict[str, int]:
        """
        Total tokens consumed per generation_type for a tenant.

        Returns {generation_type: total_tokens}.
        Used for per-tenant quota enforcement and billing dashboards.
        """
        stmt = (
            select(
                AIGeneration.generation_type,
                func.sum(AIGeneration.total_tokens).label("total"),
            )
            .where(AIGeneration.tenant_id == tenant_id)
            .where(AIGeneration.created_at >= since)
            .group_by(AIGeneration.generation_type)
        )
        if until is not None:
            stmt = stmt.where(AIGeneration.created_at < until)
        rows = (await self._session.execute(stmt)).all()
        return {row[0]: int(row[1]) for row in rows}

    async def total_tokens_for_tenant(
        self,
        tenant_id: UUID,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> int:
        """Sum of all tokens consumed by a tenant in a time range."""
        stmt = (
            select(func.coalesce(func.sum(AIGeneration.total_tokens), 0))
            .where(AIGeneration.tenant_id == tenant_id)
            .where(AIGeneration.created_at >= since)
        )
        if until is not None:
            stmt = stmt.where(AIGeneration.created_at < until)
        return int((await self._session.execute(stmt)).scalar_one())
