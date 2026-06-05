"""
Feedback report schemas.

Covers:
  - CitationSchema                    — RAG knowledge-base source citation
  - ScoreDimensionSchema              — per-rubric-dimension score + rationale
  - OverallScoreSchema                — weighted aggregate with breakdown
  - ImprovementRecommendationSchema   — priority-ordered actionable recommendation
  - FeedbackReportResponse            — full AI-generated feedback response
  - FeedbackRatingRequest             — learner's star-rating + optional notes

FeedbackReport links to EITHER a CoachingSession OR a RoleplaySession
(XOR constraint enforced at DB level and validated here via model_validator).

session_type discriminator:
    'coaching'  — linked via session_id
    'roleplay'  — linked via roleplay_id

JSONB structures (mirrors session.py docstring):

  scores:
    { "Dimension Name": { "score": int, "rationale": str }, ... }

  citations:
    [{ "source_title": str, "kb_id": UUID, "source_id": UUID,
       "snippet": str, "relevance": float }, ...]

  recommendations:
    [{ "priority": int, "area": str, "suggestion": str,
       "example": str | None }, ...]
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── CitationSchema ────────────────────────────────────────────────────────────

class CitationSchema(BaseModel):
    """
    A single RAG knowledge-base citation used in feedback generation.

    Presented in the feedback UI to build learner trust by showing that
    coaching is grounded in company-specific knowledge material (Addendum B.5).

    relevance is a cosine-similarity score from the vector retrieval step,
    normalised to [0.0, 1.0].
    """

    source_title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable title of the knowledge-base source",
    )
    kb_id: UUID = Field(
        ...,
        description="UUID of the KnowledgeBase that owns this source",
    )
    source_id: UUID = Field(
        ...,
        description="UUID of the KnowledgeChunk or source document",
    )
    snippet: str = Field(
        ...,
        min_length=1,
        description="Relevant excerpt from the source used during feedback generation",
    )
    relevance: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Cosine-similarity relevance score from vector retrieval [0.0, 1.0]",
    )


# ── ScoreDimensionSchema ──────────────────────────────────────────────────────

class ScoreDimensionSchema(BaseModel):
    """
    Score and rationale for a single rubric dimension.

    Deserialised from the JSONB scores column:
        { "Situation Clarity": { "score": 3, "rationale": "..." }, ... }

    dimension_name carries the dict key from the JSONB object.
    score is the raw integer awarded by the AI for this dimension.
    max_score is optional context provided by the rubric definition; when
    present it allows the UI to render the score as "3 / 5".
    """

    dimension_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Rubric dimension name, e.g. 'Situation Clarity'",
    )
    score: int = Field(
        ...,
        ge=0,
        description="Raw score awarded for this dimension",
    )
    max_score: int | None = Field(
        default=None,
        ge=1,
        description="Maximum possible score for this dimension; sourced from the rubric",
    )
    rationale: str = Field(
        ...,
        min_length=1,
        description="AI-generated explanation for the score",
    )


# ── OverallScoreSchema ────────────────────────────────────────────────────────

class OverallScoreSchema(BaseModel):
    """
    Weighted overall score with per-dimension breakdown.

    overall_score mirrors FeedbackReport.overall_score (Numeric 5,2).
    dimensions carries the full breakdown for the score visualisation widget.
    """

    overall_score: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("100"),
        description="Weighted average of all dimension scores; 0.00–100.00",
    )
    dimensions: list[ScoreDimensionSchema] = Field(
        ...,
        description="Per-dimension breakdown ordered by rubric definition",
    )


# ── ImprovementRecommendationSchema ──────────────────────────────────────────

class ImprovementRecommendationSchema(BaseModel):
    """
    A single actionable improvement recommendation from the AI coach.

    Deserialised from the JSONB recommendations column:
        [{
            "priority": 1,
            "area": "Behaviour Specificity",
            "suggestion": "Name the exact action you observed",
            "example": "Instead of 'you were dismissive', try..."
        }]

    priority is 1-based (1 = most important).
    example is optional; when present it provides a concrete before/after
    illustration of the suggestion.
    """

    priority: int = Field(
        ...,
        ge=1,
        description="Priority ranking; 1 = most important improvement area",
    )
    area: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Rubric dimension or skill area this recommendation addresses",
    )
    suggestion: str = Field(
        ...,
        min_length=1,
        description="Actionable improvement suggestion from the AI coach",
    )
    example: str | None = Field(
        default=None,
        description="Optional concrete before/after example illustrating the suggestion",
    )


# ── FeedbackReportResponse ────────────────────────────────────────────────────

class FeedbackReportResponse(BaseModel):
    """
    Full AI-generated feedback report response.

    Returned by GET /feedback/{report_id} and embedded in session detail responses.

    session_type discriminates whether this report belongs to a coaching session
    or a roleplay session.

    raw_ai_response is intentionally excluded — it is internal debugging data
    and must never be exposed in learner-facing API responses.

    scores_breakdown provides a structured list view of the JSONB scores dict
    for easy rendering; it is derived from the scores JSONB on the ORM model
    by the service layer before serialisation.
    """

    model_config = ConfigDict(
        from_attributes=True,
        # Suppress Pydantic's default 'model_' namespace protection so that
        # model_used (the Ollama model name field) is accepted without warning.
        protected_namespaces=(),
    )

    id: UUID
    session_id: UUID | None = Field(
        default=None,
        description="Set when linked to a CoachingSession; None for roleplay reports",
    )
    roleplay_id: UUID | None = Field(
        default=None,
        description="Set when linked to a RoleplaySession; None for coaching reports",
    )
    user_id: UUID
    tenant_id: UUID | None = None
    rubric_id: UUID | None = None

    session_type: Literal["coaching", "roleplay"] = Field(
        ...,
        description="Discriminator derived from which FK is populated",
    )

    # ── Scores ────────────────────────────────────────────────────────────────
    overall_score: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("100"),
        description="Weighted average of all dimension scores; 0.00–100.00",
    )
    scores: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "Raw per-dimension scores JSONB. "
            "Keys are dimension names; values contain 'score' and 'rationale'."
        ),
    )
    scores_breakdown: list[ScoreDimensionSchema] = Field(
        default_factory=list,
        description=(
            "Structured list representation of scores for UI rendering. "
            "Populated by the service layer from the scores JSONB."
        ),
    )

    # ── Narrative ─────────────────────────────────────────────────────────────
    feedback_text: str = Field(
        ...,
        description="Full narrative feedback generated by the AI coach",
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="List of observed strength statements",
    )
    improvements: list[str] = Field(
        default_factory=list,
        description="List of improvement area statements",
    )
    next_steps: str | None = Field(
        default=None,
        description="Actionable next-step text from the AI coach; shown as CTA in the UI",
    )

    # ── Structured recommendations ────────────────────────────────────────────
    recommendations: list[ImprovementRecommendationSchema] = Field(
        default_factory=list,
        description="Priority-ordered actionable improvement recommendations",
    )

    # ── Knowledge citations ───────────────────────────────────────────────────
    knowledge_used: bool = Field(
        ...,
        description="True if the RAG pipeline retrieved at least one relevant chunk",
    )
    citations: list[CitationSchema] = Field(
        default_factory=list,
        description=(
            "RAG source citations used during feedback generation. "
            "Empty when knowledge_used is False."
        ),
    )

    # ── Model metadata ────────────────────────────────────────────────────────
    model_used: str | None = Field(
        default=None,
        description="Ollama model name that generated this report, e.g. 'qwen3:4b'",
    )

    # ── Learner interaction ───────────────────────────────────────────────────
    user_rating: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="1-5 star rating submitted by the learner; None if not yet rated",
    )
    user_notes: str | None = Field(
        default=None,
        description="Free-text learner annotation; not used by the AI engine",
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_session_xor(self) -> "FeedbackReportResponse":
        """Enforce XOR: exactly one of session_id / roleplay_id must be set."""
        has_session = self.session_id is not None
        has_roleplay = self.roleplay_id is not None
        if has_session == has_roleplay:
            raise ValueError(
                "Exactly one of session_id or roleplay_id must be set "
                "(got both set or both None)"
            )
        return self

    @model_validator(mode="after")
    def validate_session_type_consistency(self) -> "FeedbackReportResponse":
        """session_type must be consistent with whichever FK is populated."""
        if self.session_id is not None and self.session_type != "coaching":
            raise ValueError(
                "session_type must be 'coaching' when session_id is set"
            )
        if self.roleplay_id is not None and self.session_type != "roleplay":
            raise ValueError(
                "session_type must be 'roleplay' when roleplay_id is set"
            )
        return self

    @field_validator("citations", mode="before")
    @classmethod
    def coerce_citations(cls, v: list) -> list:
        """
        Accept citations either as CitationSchema objects or raw dicts
        (deserialised directly from the JSONB column).
        """
        return v

    @field_validator("recommendations", mode="before")
    @classmethod
    def coerce_recommendations(cls, v: list) -> list:
        """
        Accept recommendations either as ImprovementRecommendationSchema objects
        or raw dicts (deserialised directly from the JSONB column).
        """
        return v


# ── FeedbackRatingRequest ─────────────────────────────────────────────────────

class FeedbackRatingRequest(BaseModel):
    """
    POST /feedback/{report_id}/rate

    Learner submits a star rating and optional notes after reviewing feedback.

    rating:  1–5 stars (1 = not useful, 5 = very useful).
    notes:   Optional free-text annotation stored as user_notes on the report.
             Max 2000 characters to prevent abuse.
    """

    rating: int = Field(
        ...,
        ge=1,
        le=5,
        description="Star rating from 1 (not useful) to 5 (very useful)",
    )
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional learner notes or disagreement annotation",
    )

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            return stripped if stripped else None
        return v
