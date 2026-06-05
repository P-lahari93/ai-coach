"""
Module domain models — implements "modules as data" from the PRD Addendum.

Architecture decisions:
─────────────────────
  CoachingModule
    Root aggregate. tenant_id=NULL = global/platform module.
    tenant_id set  = tenant-scoped custom module.
    status lifecycle: draft → published → archived.
    Inherits OptimisticLockMixin — concurrent admin edits are detected.

  ModuleVersion
    Immutable snapshot of a module's full definition at a point in time.
    Once is_current=True and published_at is set, this row is NEVER
    mutated. Any edit creates a new version row. This protects learners
    mid-program from definition drift.
    Carries intake_schema + scoring_rubric as JSONB so the full
    "Module Definition" contract (PRD Addendum A.2) is version-pinned.

  ModuleFrameworkStep
    Ordered steps (S→B→I for SBI, G→R→O→W for GROW, etc.).
    Drives both the UI teaching flow and the scoring evaluator.
    Tied to a ModuleVersion — immutable once version is published.

  ModulePromptTemplate
    One row per template_type per ModuleVersion.
    template_body uses {{variable}} slots resolved by PromptBuilder.
    Immutable once version is published.

  ModulePersona
    AI roleplay persona. A version can have multiple personas
    (e.g. "Direct Manager", "Empathetic Manager").
    The engine selects the persona for each roleplay session.

  Rubric
    Scoring dimensions + weights + band descriptors.
    One Rubric per ModuleVersion (1:1). The sum-of-weights=1.0
    invariant is enforced by a DB CHECK in migrations.

  ModuleKnowledgeBase (join table)
    Normalized M:M between CoachingModule and KnowledgeBase.
    Replaces the previously proposed knowledge_base_ids JSONB array.
    weight column drives retrieval ranking (module-specific KB scores
    higher than tenant-wide KB per PRD Addendum B.2).

Circular import strategy:
──────────────────────────
  All cross-batch model references (User, Tenant, KnowledgeBase,
  CoachingSession, RoleplaySession, UserProgress) are imported under
  TYPE_CHECKING only. At runtime, SQLAlchemy resolves relationships
  lazily by string name, so no circular import occurs.

Fixes applied (from validation report):
  SA-01  — No lazy="dynamic"; uses lazy="write_only" for large collections,
            lazy="selectin" for always-needed small collections,
            lazy="select" for on-demand loads.
  DB-01  — Partial unique index ensuring only one is_current=True per
            module is declared in __table_args__ as a comment-noted
            Index; the actual partial unique index is emitted in the
            Alembic migration using op.create_index(..., unique=True,
            postgresql_where=...). It cannot be expressed as a plain
            SQLAlchemy UniqueConstraint due to the WHERE clause.
  PERF-02 — framework_steps/personas/prompt_templates use lazy="select"
             (not "selectin") on ModuleVersion to avoid N+1 loads on
             the module list view. The session startup flow explicitly
             loads them via options(selectinload(...)).
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
    # Runtime imports would cause circular dependency.
    # SQLAlchemy resolves these by string name at mapper-config time.
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.knowledge import KnowledgeBase          # Batch 3
    from app.models.session import CoachingSession, RoleplaySession  # Batch 3
    from app.models.progress import UserProgress            # Batch 4


# ─────────────────────────────────────────────────────────────────────────────
# CoachingModule
# ─────────────────────────────────────────────────────────────────────────────

class CoachingModule(BusinessBase, OptimisticLockMixin, Base):
    """
    Root aggregate for a coaching module.

    Design rule from PRD Addendum A.1:
        "The engine never hardcodes 'SBI' or 'feedback'.
         It reads a Module Definition and behaves accordingly.
         If adding a module requires a code change, the abstraction
         has leaked."

    Adding a module = INSERT a row here + child rows in
    ModuleVersion, ModuleFrameworkStep, ModulePromptTemplate,
    ModulePersona, Rubric. Zero code changes required.

    key: machine-readable slug, e.g. 'sbi_feedback', 'grow_coaching'.
         Unique within a tenant scope (NULL tenant = global).
         Used by the engine and frontend to reference a module
         without depending on its UUID.

    status:
        draft     → being authored; not visible to learners
        published → active; learners can start sessions
        archived  → retired; existing sessions still reference it
                    but no new sessions can be started

    gamification_overrides JSONB keys (optional):
        points_per_session  int
        points_per_score_band  dict {1: 10, 2: 20, 3: 30, 4: 50}
        level_threshold     int

    OptimisticLockMixin: admin publish/archive flows check version
    to prevent two admins clobbering each other's draft changes.
    """

    __tablename__ = "coaching_modules"
    __table_args__ = (
        # Key is unique per (tenant_id) scope.
        # NULL tenant_id = global module. Partial indexes handle
        # the NULL case correctly in the migration:
        #   UNIQUE (key) WHERE tenant_id IS NULL          (global)
        #   UNIQUE (key, tenant_id) WHERE tenant_id IS NOT NULL (tenant)
        Index(
            "idx_modules_tenant_status",
            "tenant_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_modules_key_active",
            "key",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_coaching_module_status",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Machine-readable slug, e.g. 'sbi_feedback'",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    icon: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Icon identifier for the UI, e.g. 'MessageSquare'",
    )
    blurb: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Short marketing description shown in the module library",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="NULL = global platform module; set = tenant-scoped module",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        server_default=text("'draft'"),
        comment="draft | published | archived",
    )
    gamification_overrides: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Optional per-module point/level tuning",
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who authored this module",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Optional[Tenant]] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        lazy="select",
    )
    creator: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[created_by],
        lazy="select",
    )
    versions: Mapped[list[ModuleVersion]] = relationship(
        "ModuleVersion",
        back_populates="module",
        cascade="all, delete-orphan",
        order_by="ModuleVersion.version_number",
        lazy="select",
        # Load explicitly with selectinload() when version list is needed.
        # Avoids loading all versions on every module access.
    )
    knowledge_base_links: Mapped[list[ModuleKnowledgeBase]] = relationship(
        "ModuleKnowledgeBase",
        back_populates="module",
        cascade="all, delete-orphan",
        lazy="selectin",
        # Always loaded — needed at session-start to resolve KB collections.
    )
    coaching_sessions: Mapped[list[CoachingSession]] = relationship(
        "CoachingSession",
        back_populates="module",
        lazy="write_only",     # large collection — never load all at once
    )
    roleplay_sessions: Mapped[list[RoleplaySession]] = relationship(
        "RoleplaySession",
        back_populates="module",
        lazy="write_only",
    )
    user_progress: Mapped[list[UserProgress]] = relationship(
        "UserProgress",
        back_populates="module",
        cascade="all, delete-orphan",
        lazy="write_only",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def current_version(self) -> Optional[ModuleVersion]:
        """
        Return the active version from an already-loaded `versions` list.
        Requires versions to be loaded (use selectinload in the query).
        """
        for v in self.versions:
            if v.is_current:
                return v
        return None

    @property
    def is_published(self) -> bool:
        return self.status == "published"

    def __repr__(self) -> str:
        return (
            f"<CoachingModule key={self.key!r} "
            f"status={self.status!r} tenant={self.tenant_id}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ModuleVersion
# ─────────────────────────────────────────────────────────────────────────────

class ModuleVersion(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticLockMixin, Base):
    """
    Immutable snapshot of a module's full definition.

    Immutability contract:
        Once published_at is set and is_current=True, this row is
        NEVER updated. Any change to the module definition must create
        a new ModuleVersion row and flip is_current on the old one to
        False (and set the new one to True).

        This ensures learners mid-session are always evaluated against
        the rubric and prompts that were active when they started, not
        a silently changed version.

    is_current uniqueness:
        Only ONE version per module may have is_current=True at a time.
        Enforced by a partial unique index in the migration:
            CREATE UNIQUE INDEX uq_module_one_current_version
            ON module_versions (module_id)
            WHERE is_current = true;
        (FIX DB-01 from validation report)

    intake_schema JSONB — list of field definitions:
        [
          {
            "field_key": "situation",
            "label": "Describe the situation",
            "type": "longtext",          // text | longtext | voice
            "required": true,
            "placeholder": "e.g. In our team meeting on Monday..."
          },
          ...
        ]

    scoring_rubric JSONB — rubric dimensions:
        {
          "dimensions": [
            {
              "name": "Situation Clarity",
              "weight": 0.25,
              "band_descriptors": {
                "1": "No situation described",
                "2": "Vague situation reference",
                "3": "Clear situation with context",
                "4": "Specific, detailed situation"
              }
            },
            ...
          ]
        }
        Note: sum of weights must equal 1.0
              Enforced by Rubric.dimensions CHECK in migration.

    OptimisticLockMixin:
        Prevents two simultaneous publish operations from racing.
    """

    __tablename__ = "module_versions"
    __table_args__ = (
        UniqueConstraint(
            "module_id", "version_number",
            name="uq_module_version_number",
        ),
        Index("idx_module_versions_current", "module_id", "is_current"),
        Index("idx_module_versions_module", "module_id"),
        # Partial unique index for is_current=true is defined in migration:
        # CREATE UNIQUE INDEX uq_module_one_current_version
        # ON module_versions (module_id) WHERE is_current = true;
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_modules.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Monotonically increasing per module_id",
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Only one version per module may be True at a time",
    )
    framework_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="e.g. 'SBI', 'GROW', or a custom tenant-defined name",
    )
    intake_schema: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="Ordered list of intake field definitions (see docstring)",
    )
    scoring_rubric: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="Dimension weights + band descriptors (see docstring)",
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),    # FIX CRITICAL-03: explicit type, not string
        nullable=True,
        comment="Set when is_current flips to True; null = draft",
    )
    published_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who published this version",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    module: Mapped[CoachingModule] = relationship(
        "CoachingModule",
        back_populates="versions",
    )
    publisher: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[published_by],
        lazy="select",
    )
    framework_steps: Mapped[list[ModuleFrameworkStep]] = relationship(
        "ModuleFrameworkStep",
        back_populates="module_version",
        cascade="all, delete-orphan",
        order_by="ModuleFrameworkStep.step_order",
        lazy="select",
        # Load with selectinload() in coaching session startup, not by default.
        # FIX PERF-02: was selectin, changed to select to avoid N+1 on list views.
    )
    prompt_templates: Mapped[list[ModulePromptTemplate]] = relationship(
        "ModulePromptTemplate",
        back_populates="module_version",
        cascade="all, delete-orphan",
        lazy="select",  # FIX PERF-02
    )
    personas: Mapped[list[ModulePersona]] = relationship(
        "ModulePersona",
        back_populates="module_version",
        cascade="all, delete-orphan",
        lazy="select",  # FIX PERF-02
    )
    rubric: Mapped[Optional[Rubric]] = relationship(
        "Rubric",
        back_populates="module_version",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="select",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    def get_prompt_template(self, template_type: str) -> Optional[ModulePromptTemplate]:
        """
        Return prompt template by type from an already-loaded list.
        Use selectinload(ModuleVersion.prompt_templates) before calling.
        """
        for t in self.prompt_templates:
            if t.template_type == template_type:
                return t
        return None

    def get_default_persona(self) -> Optional[ModulePersona]:
        """Return the default persona from an already-loaded list."""
        for p in self.personas:
            if p.is_default:
                return p
        return self.personas[0] if self.personas else None

    def __repr__(self) -> str:
        return (
            f"<ModuleVersion module={self.module_id} "
            f"v={self.version_number} current={self.is_current}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ModuleFrameworkStep
# ─────────────────────────────────────────────────────────────────────────────

class ModuleFrameworkStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    One step in a coaching framework (e.g. S, B, I for SBI).

    step_order: 0-based integer. Unique per version.
                Drives the order of teaching cards in the UI and
                the structured evaluation pass in the scoring engine.

    scoring_hints: optional guidance text injected into the scoring
                   prompt to help the LLM evaluate this specific step.
                   Separate from label/description to keep the teaching
                   copy clean.

    Immutable once its ModuleVersion is published.
    """

    __tablename__ = "module_framework_steps"
    __table_args__ = (
        UniqueConstraint(
            "module_version_id", "step_order",
            name="uq_framework_step_order",
        ),
        Index("idx_framework_steps_version", "module_version_id"),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("module_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="0-based display order within this version",
    )
    label: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Short label shown in the UI, e.g. 'Situation'",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full explanation shown to the learner",
    )
    scoring_hints: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Private hints injected into the LLM scoring prompt",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    module_version: Mapped[ModuleVersion] = relationship(
        "ModuleVersion",
        back_populates="framework_steps",
    )

    def __repr__(self) -> str:
        return (
            f"<ModuleFrameworkStep order={self.step_order} "
            f"label={self.label!r}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ModulePromptTemplate
# ─────────────────────────────────────────────────────────────────────────────

class ModulePromptTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    System-prompt template for one phase of the AI coaching flow.

    template_type values (enforced by CHECK constraint):
        coaching         — feedback generation pass
        roleplay_system  — AI persona system message for roleplay
        roleplay_turn    — per-turn instruction injected by the engine
        scoring          — rubric evaluation prompt

    template_body uses {{variable_name}} slots resolved by PromptBuilder.
    Declared slots are listed in the variables JSONB array so the
    validation service can assert all required slots are present
    before the module is published.

    Example template_body (coaching type):
        You are an expert coach evaluating the SBI feedback below.

        Framework: {{framework}}
        Rubric: {{rubric}}
        Company knowledge: {{knowledge}}
        Learner intake: {{intake}}

        Provide structured feedback...

    Canonical slot names (variables JSONB):
        framework   — framework_name + step labels
        rubric      — scoring_rubric dimensions
        knowledge   — RAG-retrieved snippets
        intake      — learner's filled intake_schema data
        persona     — persona name + traits (roleplay only)
        history     — conversation history (roleplay_turn only)

    Immutable once its ModuleVersion is published.
    """

    __tablename__ = "module_prompt_templates"
    __table_args__ = (
        UniqueConstraint(
            "module_version_id", "template_type",
            name="uq_prompt_template_type_per_version",
        ),
        Index("idx_prompt_templates_version", "module_version_id"),
        CheckConstraint(
            "template_type IN "
            "('coaching', 'roleplay_system', 'roleplay_turn', 'scoring')",
            name="ck_prompt_template_type",
        ),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("module_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="coaching | roleplay_system | roleplay_turn | scoring",
    )
    template_body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Prompt text with {{variable}} slots",
    )
    variables: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="Declared slot names, e.g. ['framework','rubric','knowledge']",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    module_version: Mapped[ModuleVersion] = relationship(
        "ModuleVersion",
        back_populates="prompt_templates",
    )

    def __repr__(self) -> str:
        return (
            f"<ModulePromptTemplate type={self.template_type!r} "
            f"version={self.module_version_id}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ModulePersona
# ─────────────────────────────────────────────────────────────────────────────

class ModulePersona(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    AI roleplay persona for a module version.

    A module version can have multiple personas (e.g. a Sales module
    might have "Hostile Prospect", "Curious Prospect", "Technical
    Evaluator"). The learner or the session config selects one.

    system_prompt: injected as the AI's system message at roleplay
                   session start. Combined with the roleplay_system
                   template from ModulePromptTemplate.

    traits JSONB: list of adjectives used by the scoring engine
                  to calibrate its evaluation, e.g.:
                  ["direct", "impatient", "detail-oriented"]

    is_default: if True, this persona is selected when no explicit
                persona_id is specified when creating a roleplay session.
                Only one persona per version should have is_default=True
                (enforced by partial unique index in migration).

    Immutable once its ModuleVersion is published.
    """

    __tablename__ = "module_personas"
    __table_args__ = (
        Index("idx_personas_version", "module_version_id"),
        Index(
            "idx_personas_version_default",
            "module_version_id",
            postgresql_where=text("is_default = true"),
        ),
        # Partial unique index for is_default=true per version
        # defined in migration:
        # CREATE UNIQUE INDEX uq_persona_one_default_per_version
        # ON module_personas (module_version_id)
        # WHERE is_default = true;
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("module_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    persona_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name, e.g. 'Hostile Prospect'",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Shown to the learner before starting the roleplay",
    )
    system_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full system message injected as the AI's persona",
    )
    traits: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="Trait adjectives, e.g. ['direct','impatient']",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Selected when no explicit persona_id is given",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    module_version: Mapped[ModuleVersion] = relationship(
        "ModuleVersion",
        back_populates="personas",
    )

    def __repr__(self) -> str:
        return (
            f"<ModulePersona name={self.persona_name!r} "
            f"default={self.is_default}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Rubric
# ─────────────────────────────────────────────────────────────────────────────

class Rubric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Scoring rubric for a module version (1:1 with ModuleVersion).

    dimensions JSONB schema (list of dimension objects):
        [
          {
            "name": "Situation Clarity",
            "weight": 0.25,
            "band_descriptors": {
              "1": "No situation described",
              "2": "Vague reference only",
              "3": "Clear situation with context",
              "4": "Specific, concrete, detailed"
            }
          },
          {
            "name": "Behaviour Specificity",
            "weight": 0.35,
            ...
          },
          ...
        ]

    Invariant: sum of all dimension weights must equal exactly 1.0.
    This is validated:
      1. By the ModuleService before saving/publishing.
      2. By a DB CHECK constraint in the migration (computed on JSONB).

    content_version: tracks changes to dimension text/descriptors
    independently of the parent ModuleVersion.version counter.
    This supports rubric wording refinements without requiring a full
    module re-publish.

    description: human-readable explanation of the rubric shown in
    the admin UI module editor. NULL for system/seeded rubrics.

    change_notes: explains what changed in this content_version
    (e.g. "clarified Level 3 descriptor for Clarity dimension").
    Useful for content audits when scores drift over time.

    Immutable once its ModuleVersion is published.
    """

    __tablename__ = "rubrics"
    __table_args__ = (
        UniqueConstraint(
            "module_version_id",
            name="uq_rubric_per_module_version",
        ),
        Index("idx_rubrics_version", "module_version_id"),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("module_versions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="1:1 with ModuleVersion",
    )
    dimensions: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="List of {name, weight, band_descriptors} objects",
    )
    content_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Incremented on wording changes; audit trail for score drift",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable rubric description for admin UI display",
    )
    change_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Notes on what changed in this content_version; wording change rationale",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    module_version: Mapped[ModuleVersion] = relationship(
        "ModuleVersion",
        back_populates="rubric",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def dimension_names(self) -> list[str]:
        """Convenience list of dimension names from loaded JSONB."""
        return [d.get("name", "") for d in (self.dimensions or [])]

    @property
    def total_weight(self) -> Decimal:
        """Sum of all dimension weights. Should equal 1.0."""
        return Decimal(
            str(sum(d.get("weight", 0) for d in (self.dimensions or [])))
        )

    def __repr__(self) -> str:
        return (
            f"<Rubric version={self.module_version_id} "
            f"dims={len(self.dimensions or [])}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ModuleKnowledgeBase  (normalized join table)
# ─────────────────────────────────────────────────────────────────────────────

class ModuleKnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Normalized M:M join between CoachingModule and KnowledgeBase.

    Replaces the previously proposed knowledge_base_ids JSONB array
    on CoachingModule. Normalization provides:
      - Referential integrity (CASCADE delete when KB is removed)
      - Joinable for queries like "which modules use KB X?"
      - weight column for retrieval ranking
      - Indexable for the RAG retrieval hot path

    weight: multiplier applied to similarity scores when retrieving
            from this KB for this module. Allows module-specific KBs
            to outrank the tenant-wide KB per PRD Addendum B.2:
            "Module-specific content is weighted higher than general."

            Recommended values:
              Tenant-wide KB attached to a module: weight = 1.0
              Module-specific KB:                  weight = 1.5

    is_primary: marks the single most authoritative KB for this module
                (used by the citation service to label sources).

    Retrieval query pattern (resolved at session start):
        SELECT kb_id FROM module_knowledge_bases
        WHERE module_id = :module_id
        ORDER BY weight DESC, is_primary DESC
    """

    __tablename__ = "module_knowledge_bases"
    __table_args__ = (
        UniqueConstraint(
            "module_id", "knowledge_base_id",
            name="uq_module_knowledge_base",
        ),
        Index("idx_mkb_module", "module_id"),
        Index("idx_mkb_kb", "knowledge_base_id"),
    )

    # ── Columns ───────────────────────────────────────────────────────────────
    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_modules.id", ondelete="CASCADE"),
        nullable=False,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    weight: Mapped[Decimal] = mapped_column(
        Numeric(4, 2),
        nullable=False,
        default=Decimal("1.0"),
        server_default=text("1.0"),
        comment="Retrieval score multiplier; module-specific KB should be > 1.0",
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Most authoritative KB for this module",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    module: Mapped[CoachingModule] = relationship(
        "CoachingModule",
        back_populates="knowledge_base_links",
    )
    knowledge_base: Mapped[KnowledgeBase] = relationship(
        "KnowledgeBase",
        back_populates="module_links",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<ModuleKnowledgeBase module={self.module_id} "
            f"kb={self.knowledge_base_id} weight={self.weight}>"
        )
