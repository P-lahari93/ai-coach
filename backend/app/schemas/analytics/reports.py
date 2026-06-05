"""
Analytics report schemas.

Covers:
  - APIUsageReportRow         — per-endpoint aggregated usage row
  - APIUsageReport            — full API usage report for a time window
  - TokenUsageRow             — per-model/type aggregated token usage
  - TokenUsageReport          — full token usage report for billing/quotas
  - SessionFunnelStep         — single funnel stage with counts and rates
  - SessionFunnelReport       — full session completion funnel report
  - AnalyticsDashboardResponse— top-level dashboard metrics aggregate

These schemas carry pre-aggregated data computed by the analytics
service/repository layer. They are never mapped directly from ORM
objects (no from_attributes needed for reports), but the individual
row types that reference ORM models do use ConfigDict(from_attributes=True).

Time window convention:
  All report schemas include period_start / period_end (UTC) so the
  frontend can display "data for the last 30 days" accurately.

APIUsageReport:
  Built from SUM/COUNT aggregates over the api_usage_logs table.
  p50/p95/p99 latency percentiles computed via percentile_cont().

TokenUsageReport:
  Built from SUM aggregates over ai_generations.
  Per-tenant quota enforcement uses rolling_total_tokens compared
  against the tenant's token_quota setting.

SessionFunnelReport:
  Tracks the drop-off from sessions_started → completed → feedback_viewed.
  Provides the core conversion metric for coaching programme effectiveness.

AnalyticsDashboardResponse:
  Single endpoint response aggregating the KPIs needed for the admin
  and tenant-admin dashboard overview page.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── APIUsageReport ────────────────────────────────────────────────────────────

class APIUsageReportRow(BaseModel):
    """
    Per-endpoint aggregated usage metrics for a reporting period.

    error_rate: fraction of requests with status_code >= 400.
    p50/p95/p99 latency in milliseconds (percentile_cont from Postgres).
    """

    endpoint: str = Field(..., description="Route path, e.g. '/v1/sessions'")
    method: str = Field(..., description="HTTP method: GET|POST|PUT|PATCH|DELETE")
    total_requests: int = Field(..., ge=0)
    success_requests: int = Field(..., ge=0, description="Requests with status_code < 400")
    error_requests: int = Field(..., ge=0, description="Requests with status_code >= 400")
    error_rate: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="error_requests / total_requests; 0.0–1.0",
    )
    avg_latency_ms: Decimal = Field(..., ge=Decimal("0"))
    p50_latency_ms: int = Field(..., ge=0, description="Median latency in ms")
    p95_latency_ms: int = Field(..., ge=0, description="95th percentile latency in ms")
    p99_latency_ms: int = Field(..., ge=0, description="99th percentile latency in ms")
    total_request_bytes: int = Field(..., ge=0)
    total_response_bytes: int = Field(..., ge=0)


class APIUsageReport(BaseModel):
    """
    Full API usage report for a given tenant and time window.

    rows: aggregated per-endpoint metrics, ordered by total_requests desc.
    overall_* fields carry totals across all endpoints for the period.
    """

    tenant_id: UUID | None = Field(
        default=None,
        description="None = platform-wide report",
    )
    period_start: datetime
    period_end: datetime
    overall_requests: int = Field(..., ge=0)
    overall_errors: int = Field(..., ge=0)
    overall_error_rate: Decimal = Field(..., ge=Decimal("0"), le=Decimal("1"))
    overall_avg_latency_ms: Decimal = Field(..., ge=Decimal("0"))
    rows: list[APIUsageReportRow] = Field(
        default_factory=list,
        description="Per-endpoint breakdown ordered by total_requests descending",
    )


# ── TokenUsageReport ──────────────────────────────────────────────────────────

class TokenUsageRow(BaseModel):
    """
    Per-model and per-generation-type token usage for a reporting period.

    cache_hit_rate: fraction of calls served from Ollama KV cache.
    avg_response_time_ms: mean wall-clock time from request to first token.
    error_rate: fraction of generation calls that returned an error.
    """

    # model_name is a domain field (Ollama model identifier); suppress
    # Pydantic's protected 'model_' namespace warning.
    model_config = ConfigDict(protected_namespaces=())

    model_name: str = Field(..., description="Ollama model identifier, e.g. 'qwen3:4b'")
    generation_type: str = Field(
        ...,
        description="feedback | roleplay_turn | scoring | recommendation | embedding",
    )
    total_calls: int = Field(..., ge=0)
    successful_calls: int = Field(..., ge=0)
    error_rate: Decimal = Field(..., ge=Decimal("0"), le=Decimal("1"))
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
    total_tokens: int = Field(..., ge=0)
    avg_tokens_per_call: Decimal = Field(..., ge=Decimal("0"))
    cache_hit_rate: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Fraction of calls served from cache; 0.0–1.0",
    )
    avg_response_time_ms: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        description="Mean time to first token; None if no timing data available",
    )


class TokenUsageReport(BaseModel):
    """
    Full token usage report for billing, quota enforcement, and cost analysis.

    rolling_total_tokens: total tokens consumed in the rolling quota window
    (typically the current calendar month). Compared against the tenant's
    token_quota to enforce per-tenant limits.

    quota_remaining: tenant quota minus rolling_total_tokens. None when
    the tenant has no quota configured (unlimited).
    """

    tenant_id: UUID | None = Field(
        default=None,
        description="None = platform-wide report",
    )
    period_start: datetime
    period_end: datetime
    total_tokens: int = Field(..., ge=0, description="Total tokens in the report period")
    total_prompt_tokens: int = Field(..., ge=0)
    total_completion_tokens: int = Field(..., ge=0)
    total_calls: int = Field(..., ge=0)
    overall_cache_hit_rate: Decimal = Field(..., ge=Decimal("0"), le=Decimal("1"))
    rolling_total_tokens: int = Field(
        ...,
        ge=0,
        description="Tokens consumed in the current quota window (rolling month)",
    )
    quota_remaining: int | None = Field(
        default=None,
        description="Tokens remaining in quota window; None = unlimited",
    )
    rows: list[TokenUsageRow] = Field(
        default_factory=list,
        description="Per-model and per-type breakdown ordered by total_tokens descending",
    )


# ── SessionFunnelReport ───────────────────────────────────────────────────────

class SessionFunnelStep(BaseModel):
    """
    A single stage in the session completion funnel.

    conversion_rate: fraction of the top-of-funnel (sessions_started)
    that reached this stage. 1.0 for the first step.

    drop_off_rate: fraction of the previous step that did not proceed.
    0.0 for the first step.
    """

    step_name: str = Field(
        ...,
        description=(
            "Funnel stage label, e.g. 'sessions_started', "
            "'intake_submitted', 'feedback_generated', 'feedback_viewed', "
            "'achievement_earned'"
        ),
    )
    count: int = Field(..., ge=0)
    conversion_rate: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Fraction of sessions_started that reached this step; 0.0–1.0",
    )
    drop_off_rate: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Fraction of the previous step that did not proceed",
    )


class SessionFunnelReport(BaseModel):
    """
    Session completion funnel analysis.

    Tracks the learner journey from session creation through to
    feedback review and achievement award. The key metric for
    coaching programme effectiveness.

    module_id: None = aggregate across all modules.
    avg_completion_time_seconds: average time from session creation
    to feedback_viewed event; None if no completed funnel data.
    """

    tenant_id: UUID | None = Field(
        default=None,
        description="None = platform-wide funnel",
    )
    module_id: UUID | None = Field(
        default=None,
        description="None = aggregated across all modules",
    )
    period_start: datetime
    period_end: datetime
    steps: list[SessionFunnelStep] = Field(
        ...,
        description="Ordered funnel stages from top to bottom",
    )
    avg_completion_time_seconds: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Average time from session creation to feedback_viewed "
            "in seconds; None if no completed funnel data exists"
        ),
    )


# ── AnalyticsDashboardResponse ────────────────────────────────────────────────

class AnalyticsDashboardResponse(BaseModel):
    """
    Top-level analytics dashboard aggregate.

    Single response object for GET /analytics/dashboard.
    All metrics are pre-computed by the analytics service for the
    specified period and tenant scope.

    active_users: distinct user_ids with at least one event in the period.
    new_users: users whose first event falls within the period.
    avg_session_score: mean final_score across completed sessions.
    avg_sessions_per_user: mean completed sessions per active user.
    top_modules: up to 5 module_ids ordered by sessions_completed desc.
    achievement_awards: total UserAchievement rows created in the period.
    """

    tenant_id: UUID | None = Field(
        default=None,
        description="None = platform-wide dashboard",
    )
    period_start: datetime
    period_end: datetime

    # ── User metrics ──────────────────────────────────────────────────────────
    active_users: int = Field(
        ...,
        ge=0,
        description="Distinct users with at least one event in the period",
    )
    new_users: int = Field(
        ...,
        ge=0,
        description="Users whose first recorded event is within the period",
    )

    # ── Session metrics ───────────────────────────────────────────────────────
    sessions_started: int = Field(..., ge=0)
    sessions_completed: int = Field(..., ge=0)
    sessions_abandoned: int = Field(..., ge=0)
    completion_rate: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="sessions_completed / sessions_started; 0.0–1.0",
    )
    avg_session_score: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("100"),
        description="Mean final_score across completed sessions; None if no data",
    )
    avg_sessions_per_user: Decimal = Field(
        ...,
        ge=Decimal("0"),
        description="Mean completed sessions per active user",
    )

    # ── Roleplay metrics ──────────────────────────────────────────────────────
    roleplay_sessions_started: int = Field(..., ge=0)
    roleplay_sessions_completed: int = Field(..., ge=0)

    # ── AI metrics ────────────────────────────────────────────────────────────
    total_ai_tokens: int = Field(
        ...,
        ge=0,
        description="Total tokens consumed by the AI engine in the period",
    )
    ai_cache_hit_rate: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Fraction of AI calls served from cache",
    )

    # ── Gamification metrics ──────────────────────────────────────────────────
    achievement_awards: int = Field(
        ...,
        ge=0,
        description="Total achievement awards in the period",
    )

    # ── Top content ───────────────────────────────────────────────────────────
    top_modules: list[UUID] = Field(
        default_factory=list,
        max_length=5,
        description="Up to 5 module UUIDs ordered by sessions_completed descending",
    )
