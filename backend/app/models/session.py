"""
Session domain models: coaching sessions, roleplay sessions, and feedback.

Architecture decisions:
────────────────────────

  CoachingSession
    Root aggregate for a structured coaching practice session.
    Driven by a module's intake_schema — the learner fills in
    the intake form, submits, and receives AI feedback.
    Stores the intake_data as JSONB keyed by field_key from
    the module's intake_schema definition.
    Inherits OptimisticLockMixin — status transitions (in_progress
    -> completed) are protected against race conditions.

  ConversationMessage
    Stores the full message history for a CoachingSession.
    role: 'user' | 'assistant' | 'system'
    message_index: 0-based, unique per session — provides
    deterministic ordering without relying on created_at.
    Pure append-only table — no updates, no soft-delete.

  RoleplaySession
    Root aggregate for a free-form AI roleplay session.
    The AI adopts a ModulePersona and the learner practices
    a real-world conversation scenario.
    context JSONB carries running state (emotion tags, scenario
    variables, turn-by-turn coaching flags) that the AI engine
    reads and writes as the conversation progresses.
    Inherits OptimisticLockMixin — concurrent turn submissions
    on the same session are serialised.

  RoleplayMessage
    Individual turn in a roleplay conversation.
    role: 'user' | 'persona'
    turn_number: monotonically increasing per session.
    emotion_detected: optional tag from AI analysis
    coaching_note: inline hint attached by the AI, revealed
    post-session (not shown during the roleplay).
    Pure append-only table.

  FeedbackReport
    AI-generated assessment linked to EITHER a CoachingSession
    OR a RoleplaySession (exactly one, enforced by XOR CHECK).

    XOR constraint (FIX DB-02 from validation report):
      (session_id IS NOT NULL AND roleplay_id IS NULL) OR
      (session_id IS NULL AND roleplay_id IS NOT NULL)

    scores JSONB schema:
      {
        "Situation Clarity": {
          "score": 3,
          "rationale": "Clear description of the Monday meeting..."
        },
        "Behaviour Specificity": {
          "score": 4,
          "rationale": "Named the exact behaviour observed..."
        }
      }

    citations JSONB schema:
      [
        {
          "source_title": "Manager Playbook 2024",
          "kb_id": "uuid-string",
          "source_id": "uuid-string",
          "snippet": "When delivering feedback, always...",
          "relevance": 0.87
        }
      ]

    recommendations JSONB schema:
      [
        {
          "priority": 1,
          "area": "Behaviour Specificity",
          "suggestion": "Name the specific action, not the outcome",
          "example": "Instead of 'you were late', try..."
        }
      ]

Circular import strategy:
──────────────────────────
  session.py imports Tenant, User, CoachingModule, ModuleVersion,
  ModulePersona, Rubric under TYPE_CHECKING only. SQLAlchemy
  resolves all relationship() targets by string name at mapper-
  configuration time, after all modules are imported.
  module.py back_populates ('coaching_sessions', 'roleplay_sessions')
  resolve symmetrically — no runtime circular dependency.

Fixes applied (from validation report):
  SA-01  — No lazy="dynamic"; all large collections use "write_only"
  SA-03  — FeedbackReport relationships use explicit foreign_keys=[...]
           on both sides to disambiguate the two FK columns
  DB-02  — FeedbackReport CHECK enforces XOR (not just OR)
  CRITICAL-08 — All Mapped[datetime] use direct datetime type (no string)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    BusinessBase,
    OptimisticLockMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.module import (
        CoachingModule,
        ModuleVersion,
        ModulePersona,
        Rubric,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CoachingSession
# ─────────────────────────────────────────────────────────────────────────────

class CoachingSession(BusinessBase, OptimisticLockMixin, Base):
    """
    A single structured coaching practice session.

    Lifecycle:
        in_progress → completed  (feedback generated, score set)
        in_progress → abandoned  (user left without completing)

    intake_data JSONB:
        Keyed by ModuleVersion.intake_schema[].field_key.
        Example for SBI module:
        {
          "situation": "In our Monday team meeting...",
          "behaviour": "You interrupted me three times...",
          "impact": "I felt unable to finish my point..."
        }

    module_version_id:
        Pinned to the CURRENT version at session creation time.
        If the module is updated mid-program, sessions started before
        the update continue to use the old version's rubric and prompts.
        This is the core immutability guarantee.

    final_score:
        Populated by the scoring engine after feedback generation.
        Numeric(5,2) supports scores like 87.50 (0.00 to 100.00 range)
        or 3.75 (rubric band scale).

    duration_seconds:
        Calculated at completion: completed_at - created_at (seconds).
        Stored for analytics dashboards.
    """

    __tablename__ = "coaching_sessions"
    __table_args__ = (
        Index(
            "idx_coaching_sessions_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "idx_coaching_sessions_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "idx_coaching_sessions_module_status",
            "module_id",
            "status",
        ),
        # Composite covering index for the most common dashboard query:
        # "completed sessions for user X in tenant Y, newest first"
        Index(
            "idx_coaching_sessions_user_tenant_status",
            "user_id",
            "tenant_id",
            "status",
            "created_at",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'abandoned')",
            name="ck_coaching_session_status",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_modules.id", ondelete="RESTRICT"),
        nullable=False,
        comment="RESTRICT: cannot delete a module with active sessions",
    )
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("module_versions.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Pinned at session creation; never changes",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="in_progress",
        server_default=text("'in_progress'"),
        comment="in_progress | completed | abandoned",
    )
    intake_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Filled intake form keyed by field_key",
    )
    final_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Populated after feedback generation; 0.00-100.00",
    )
    duration_seconds: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Calculated at completion time",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="select",
    )
    module: Mapped[CoachingModule] = relationship(
        "CoachingModule",
        back_populates="coaching_sessions",
        lazy="select",
    )
    module_version: Mapped[ModuleVersion] = relationship(
        "ModuleVersion",
        foreign_keys=[module_version_id],
        lazy="select",
    )
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )
    messages: Mapped[list[ConversationMessage]] = relationship(
        "ConversationMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.message_index",
        lazy="write_only",      # load explicitly with selectinload() when needed
    )
    feedback_report: Mapped[Optional[FeedbackReport]] = relationship(
        "FeedbackReport",
        back_populates="coaching_session",
        foreign_keys="FeedbackReport.session_id",   # FIX SA-03
        uselist=False,
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @property
    def is_abandoned(self) -> bool:
        return self.status == "abandoned"

    def __repr__(self) -> str:
        return (
            f"<CoachingSession id={self.id} "
            f"status={self.status!r} user={self.user_id}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ConversationMessage
# ─────────────────────────────────────────────────────────────────────────────

class ConversationMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Individual message in a coaching session conversation.

    Append-only: rows are only ever INSERTed, never updated.
    No soft-delete: messages are deleted via CASCADE when the
    parent CoachingSession is hard-deleted.

    role values (enforced by CHECK):
        user      — learner's submitted text
        assistant — AI-generated response
        system    — engine-injected system message (not shown to user)

    message_index:
        0-based monotonically increasing per session_id.
        UNIQUE (session_id, message_index) ensures ordering
        is deterministic and gaps are detectable.

    token_count:
        Populated by the AI engine after generation. Used for
        cost tracking via ai_generations table.

    metadata_ JSONB keys:
        latency_ms    int  — time to generate this message
        model_name    str  — model that generated this message
        cached        bool — whether response was served from cache
        retrieval_ids list — chunk IDs used for this message (RAG)
    """

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "message_index",
            name="uq_conversation_message_index",
        ),
        Index("idx_conv_messages_session", "session_id", "message_index"),
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_conv_message_role",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="user | assistant | system",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    message_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="0-based position in this session's conversation",
    )
    token_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Token count for cost tracking",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="latency_ms, model_name, cached, retrieval_ids",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    session: Mapped[CoachingSession] = relationship(
        "CoachingSession",
        back_populates="messages",
    )

    def __repr__(self) -> str:
        return (
            f"<ConversationMessage session={self.session_id} "
            f"idx={self.message_index} role={self.role!r}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RoleplaySession
# ─────────────────────────────────────────────────────────────────────────────

class RoleplaySession(BusinessBase, OptimisticLockMixin, Base):
    """
    A free-form AI roleplay conversation session.

    The AI adopts a ModulePersona and the learner practices a
    real-world scenario (e.g. delivering feedback to a hostile manager,
    handling a difficult customer call).

    status lifecycle (enforced by CHECK):
        active    → roleplay in progress
        paused    → learner saved and paused mid-roleplay
        completed → learner ended the session; feedback generated
        abandoned → session timed out or was discarded

    context JSONB:
        Mutable bag maintained by the AI engine per turn.
        Keys managed by the roleplay engine:
          emotion_state    str   — current detected emotion of persona
          scenario_phase   str   — e.g. "opening", "escalation", "resolution"
          coaching_flags   list  — issues flagged for post-session review
          turn_scores      list  — per-turn quality scores (stored for analytics)
          custom           dict  — module-specific context variables

    scenario_prompt:
        Optional setup text describing the scenario context,
        shown to the learner before the roleplay starts.
        If NULL, the persona's system_prompt handles setup.

    turn_count:
        Incremented by the session service on each user turn.
        Denormalized for quick stats (avoids COUNT on roleplay_messages).

    persona_id:
        Set at session creation from the module version's personas list.
        NULL = the engine uses the module's default persona system prompt
        directly (backward compatible with modules without persona rows).
    """

    __tablename__ = "roleplay_sessions"
    __table_args__ = (
        Index(
            "idx_roleplay_sessions_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "idx_roleplay_sessions_tenant_created",
            "tenant_id",
            "created_at",
        ),
        # Composite for dashboard: user's active roleplay sessions
        Index(
            "idx_roleplay_sessions_user_status",
            "user_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'abandoned')",
            name="ck_roleplay_session_status",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_modules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("module_versions.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Pinned at session creation; never changes",
    )
    persona_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("module_personas.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL = use module default persona",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default=text("'active'"),
        comment="active | paused | completed | abandoned",
    )
    context: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Mutable engine state bag; updated each turn",
    )
    scenario_prompt: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Setup text shown to learner before roleplay starts",
    )
    final_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    turn_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Denormalized; incremented per user turn",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="select",
    )
    module: Mapped[CoachingModule] = relationship(
        "CoachingModule",
        back_populates="roleplay_sessions",
        lazy="select",
    )
    module_version: Mapped[ModuleVersion] = relationship(
        "ModuleVersion",
        foreign_keys=[module_version_id],
        lazy="select",
    )
    persona: Mapped[Optional[ModulePersona]] = relationship(
        "ModulePersona",
        foreign_keys=[persona_id],
        lazy="select",
    )
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )
    messages: Mapped[list[RoleplayMessage]] = relationship(
        "RoleplayMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="RoleplayMessage.turn_number",
        lazy="write_only",
    )
    feedback_report: Mapped[Optional[FeedbackReport]] = relationship(
        "FeedbackReport",
        back_populates="roleplay_session",
        foreign_keys="FeedbackReport.roleplay_id",  # FIX SA-03
        uselist=False,
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    def __repr__(self) -> str:
        return (
            f"<RoleplaySession id={self.id} "
            f"status={self.status!r} turns={self.turn_count}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RoleplayMessage
# ─────────────────────────────────────────────────────────────────────────────

class RoleplayMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Individual turn in a roleplay conversation.

    Append-only: rows are only ever INSERTed, never updated.

    role values (enforced by CHECK):
        user    — learner's message
        persona — AI persona's response

    turn_number:
        1-based counter per session. Each (session, turn, role)
        is UNIQUE — one user message and one persona message per turn.

    emotion_detected:
        Optional tag set by the AI engine when analysing the persona's
        response. Examples: "frustrated", "curious", "resistant".
        Used by the scoring engine and shown in the post-session report.

    coaching_note:
        Optional inline coaching hint generated by the AI during the
        turn. NOT shown during the roleplay — revealed only in the
        post-session FeedbackReport to avoid breaking immersion.

    metadata_ JSONB keys:
        latency_ms      int   — generation latency for this turn
        model_name      str   — model that generated this response
        retrieval_ids   list  — chunk IDs used for context (RAG)
        intent_detected str   — inferred intent of the user message
    """

    __tablename__ = "roleplay_messages"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "turn_number", "role",
            name="uq_roleplay_message_turn_role",
        ),
        Index("idx_roleplay_messages_session", "session_id", "turn_number"),
        CheckConstraint(
            "role IN ('user', 'persona')",
            name="ck_roleplay_message_role",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roleplay_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="1-based turn counter within this session",
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="user | persona",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    emotion_detected: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="e.g. 'frustrated', 'curious'; set on persona messages",
    )
    coaching_note: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Hidden hint revealed in post-session report only",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="latency_ms, model_name, retrieval_ids, intent_detected",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    session: Mapped[RoleplaySession] = relationship(
        "RoleplaySession",
        back_populates="messages",
    )

    def __repr__(self) -> str:
        return (
            f"<RoleplayMessage session={self.session_id} "
            f"turn={self.turn_number} role={self.role!r}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# FeedbackReport
# ─────────────────────────────────────────────────────────────────────────────

class FeedbackReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    AI-generated feedback assessment for a session.

    Links to EITHER a CoachingSession OR a RoleplaySession —
    never both, never neither. Enforced by XOR CHECK constraint.
    (FIX DB-02: strengthened from OR to XOR)

    scores JSONB (per rubric dimension):
        {
          "Situation Clarity": {
            "score": 3,
            "rationale": "You described the Monday meeting clearly..."
          },
          "Behaviour Specificity": {
            "score": 2,
            "rationale": "The behaviour described was still somewhat vague..."
          }
        }

    overall_score:
        Weighted average computed by the scoring engine using
        rubric dimension weights. Stored here for fast retrieval
        (avoids re-computing from JSONB each time).

    strengths JSONB list:
        ["Clear situation context", "Empathetic tone used", ...]

    improvements JSONB list:
        ["Be more specific about the behaviour", ...]

    recommendations JSONB list:
        [
          {
            "priority": 1,
            "area": "Behaviour Specificity",
            "suggestion": "Name the exact action you observed",
            "example": "Instead of 'you were dismissive', try..."
          }
        ]

    citations JSONB list:
        [
          {
            "source_title": "Manager Playbook 2024",
            "kb_id": "uuid",
            "source_id": "uuid",
            "snippet": "Effective feedback must reference...",
            "relevance": 0.87
          }
        ]
        Empty list when no KB knowledge was used (knowledge_used=False).
        PRD Addendum B.5: citations build trust by making it obvious
        when the coach is using company material vs. general principle.

    knowledge_used:
        True if the RAG pipeline retrieved at least one relevant chunk.
        Used by the UI to show/hide the citations section.

    model_used:
        The Ollama model name that generated this report, e.g.
        "qwen3:4b". Stored for debugging and quality tracking.

    raw_ai_response:
        Full raw text returned by the LLM before structured parsing.
        Stored for debugging, re-processing, and quality review.
        Never exposed directly in learner-facing API responses.

    user_rating:
        1-5 star rating submitted by the learner after reading the
        feedback. Optional. CHECK constraint enforces 1-5 range.
        NULL = learner has not yet rated this feedback.

    user_notes:
        Free-text annotation the learner can attach to the feedback
        for their own reference (e.g. "disagree with situation score").
        Not used by the AI engine — purely a learner-owned note.

    next_steps:
        Concrete, actionable next-step text generated by the AI coach
        at the end of the feedback pass. Shown prominently in the UI
        as the call-to-action after feedback review.
    """

    __tablename__ = "feedback_reports"
    __table_args__ = (
        Index("idx_feedback_user_created", "user_id", "created_at"),
        Index(
            "idx_feedback_session",
            "session_id",
            postgresql_where=text("session_id IS NOT NULL"),
        ),
        Index(
            "idx_feedback_roleplay",
            "roleplay_id",
            postgresql_where=text("roleplay_id IS NOT NULL"),
        ),
        Index("idx_feedback_tenant_created", "tenant_id", "created_at"),
        # XOR constraint: exactly one of session_id / roleplay_id must be set
        # (FIX DB-02 from validation report)
        CheckConstraint(
            "(session_id IS NOT NULL AND roleplay_id IS NULL) OR "
            "(session_id IS NULL AND roleplay_id IS NOT NULL)",
            name="ck_feedback_report_session_xor",
        ),
        CheckConstraint(
            "user_rating IS NULL OR (user_rating >= 1 AND user_rating <= 5)",
            name="ck_feedback_user_rating",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_sessions.id", ondelete="CASCADE"),
        nullable=True,
        comment="Set when linked to a CoachingSession; NULL otherwise",
    )
    roleplay_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roleplay_sessions.id", ondelete="CASCADE"),
        nullable=True,
        comment="Set when linked to a RoleplaySession; NULL otherwise",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Denormalized from the session for fast per-user queries",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    rubric_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rubrics.id", ondelete="SET NULL"),
        nullable=True,
        comment="The rubric version used for scoring; for audit trail",
    )
    scores: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Per-dimension scores + rationale (see docstring)",
    )
    overall_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Weighted average of dimension scores",
    )
    feedback_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full narrative feedback generated by the AI",
    )
    strengths: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="List of strength strings",
    )
    improvements: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="List of improvement area strings",
    )
    recommendations: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="Priority-ordered list of recommendation objects",
    )
    citations: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="RAG source citations used in this feedback",
    )
    knowledge_used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="True if RAG retrieved at least one relevant chunk",
    )
    model_used: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Ollama model name, e.g. 'qwen3:4b'",
    )
    raw_ai_response: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Raw LLM output for debugging and re-processing",
    )
    user_rating: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="1-5 star rating from learner",
    )
    user_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Free-text learner annotation on the feedback",
    )
    next_steps: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Actionable next steps from the AI coach",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    coaching_session: Mapped[Optional[CoachingSession]] = relationship(
        "CoachingSession",
        back_populates="feedback_report",
        foreign_keys=[session_id],      # FIX SA-03: disambiguate dual FK
        lazy="select",
    )
    roleplay_session: Mapped[Optional[RoleplaySession]] = relationship(
        "RoleplaySession",
        back_populates="feedback_report",
        foreign_keys=[roleplay_id],     # FIX SA-03
        lazy="select",
    )
    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="select",
    )
    rubric: Mapped[Optional[Rubric]] = relationship(
        "Rubric",
        foreign_keys=[rubric_id],
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def session_type(self) -> str:
        """Returns 'coaching' or 'roleplay' based on which FK is set."""
        return "coaching" if self.session_id is not None else "roleplay"

    @property
    def linked_session_id(self) -> uuid.UUID:
        """Returns whichever session UUID is set."""
        return self.session_id or self.roleplay_id  # type: ignore[return-value]

    def __repr__(self) -> str:
        return (
            f"<FeedbackReport id={self.id} "
            f"type={self.session_type!r} score={self.overall_score}>"
        )
