"""
AI scoring schemas — rubric-based evaluation and recommendations.

Covers:
  - ScoreDimension           — single rubric dimension score + rationale
  - ScoreBreakdown           — full per-dimension breakdown
  - CoachingScoreResponse    — complete scoring result from AI engine
  - RecommendationItem       — priority-ordered improvement suggestion

These schemas are used internally by the AI scoring engine. The public-
facing score schemas live in session/feedback_report.py (ScoreDimensionSchema,
OverallScoreSchema, ImprovementRecommendationSchema).

The AI engine uses these internal schemas during the scoring pass, then
the service layer maps them to the public schemas before storing in the
FeedbackReport row.

Rubric-based scoring flow:
  1. AI engine receives CoachingRequest (from coaching.py)
  2. AI engine extracts rubric dimensions and weights
  3. AI engine runs the scoring prompt template with intake data
  4. LLM returns structured scores per dimension + rationale
  5. AI engine parses into ScoreBreakdown
  6. AI engine computes weighted overall_score
  7. AI engine constructs CoachingScoreResponse
  8. Service layer maps to FeedbackReportResponse (public schema)

Recommendation generation:
  Recommendations are derived from the scoring pass — the LLM identifies
  the lowest-scoring dimensions and generates actionable suggestions with
  concrete examples. Priority ranking (1 = most important) is set by the
  LLM based on impact and feasibility.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── ScoreDimension ────────────────────────────────────────────────────────────

class ScoreDimension(BaseModel):
    """
    Score and rationale for a single rubric dimension.

    dimension_name: matches a key from ModuleVersion.scoring_rubric['dimensions'].
    Example: "Situation Clarity", "Behaviour Specificity".

    score: raw integer awarded by the LLM for this dimension.
    Rubric band descriptors define the mapping (e.g. 1–4 scale).

    max_score: maximum possible score for this dimension. Sourced from
    the rubric band_descriptors (count of bands). Allows the UI to render
    "3 / 4" or a normalized percentage bar.

    rationale: AI-generated explanation for why this score was awarded.
    Extracted from the LLM's structured output during the scoring pass.

    weight: dimension weight from the rubric. Used to compute the weighted
    overall_score. Sum of all weights must equal 1.0 (enforced at rubric
    creation time by the ModuleService and DB CHECK constraint).
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
        description="Raw integer score awarded by the LLM for this dimension",
    )
    max_score: int = Field(
        ...,
        ge=1,
        description="Maximum possible score; sourced from rubric band count",
    )
    weight: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Dimension weight from rubric; used for weighted overall_score",
    )
    rationale: str = Field(
        ...,
        min_length=1,
        description="AI-generated explanation for the awarded score",
    )


# ── ScoreBreakdown ────────────────────────────────────────────────────────────

class ScoreBreakdown(BaseModel):
    """
    Full per-dimension score breakdown for a coaching session.

    dimensions: list of ScoreDimension objects, one per rubric dimension.
    Order matches the rubric definition order for consistent UI rendering.

    overall_score: weighted average of all dimension scores, normalized
    to 0.00–100.00 scale. Computed by the AI engine as:
        sum(dimension.score * dimension.weight * (100 / dimension.max_score))

    rubric_id: UUID of the Rubric row used for scoring. Stored for audit
    trail — allows tracking score drift if rubric wording changes over time.

    rubric_version: content_version from the Rubric row. Incremented when
    dimension descriptors are updated without creating a new ModuleVersion.
    """

    dimensions: list[ScoreDimension] = Field(
        ...,
        min_length=1,
        description="Per-dimension scores ordered by rubric definition",
    )
    overall_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Weighted average normalized to 0.00–100.00",
    )
    rubric_id: UUID = Field(
        ...,
        description="UUID of the Rubric used for scoring (audit trail)",
    )
    rubric_version: int = Field(
        ...,
        ge=1,
        description="Rubric content_version used (tracks wording changes)",
    )


# ── RecommendationItem ────────────────────────────────────────────────────────

class RecommendationItem(BaseModel):
    """
    A single actionable improvement recommendation.

    priority: 1-based ranking (1 = most important). Set by the LLM based on:
      - Impact: will fixing this dimension significantly improve the overall score?
      - Feasibility: is this a quick win vs. a long-term skill development need?

    area: the rubric dimension or skill area this recommendation addresses.
    Usually matches a dimension_name from the rubric, but may be a
    cross-cutting skill like "Active Listening" or "Specificity".

    suggestion: actionable advice in plain language.
    Example: "Name the exact action you observed, not just the outcome."

    example: optional concrete before/after illustration.
    Example: "Instead of 'you were dismissive', try 'you interrupted me
    three times while I was explaining the proposal'."

    The LLM generates recommendations during the scoring pass by identifying
    the lowest-scoring dimensions and extracting coaching guidance from the
    rationale text. The AI engine parses these into structured RecommendationItem
    objects before returning CoachingScoreResponse.
    """

    priority: int = Field(
        ...,
        ge=1,
        description="1-based priority ranking; 1 = most important",
    )
    area: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Rubric dimension or skill area being addressed",
    )
    suggestion: str = Field(
        ...,
        min_length=1,
        description="Actionable improvement advice in plain language",
    )
    example: str | None = Field(
        default=None,
        description="Optional concrete before/after example illustrating the suggestion",
    )


# ── CoachingScoreResponse ─────────────────────────────────────────────────────

class CoachingScoreResponse(BaseModel):
    """
    Complete scoring result from the AI engine.

    Returned by the AI scoring engine after evaluating a coaching session's
    intake data against the module rubric. This is an internal schema used
    between the AI engine and the coaching service layer.

    The service layer maps this into FeedbackReportResponse (public schema)
    and stores it in the FeedbackReport table.

    score_breakdown: full per-dimension scores + overall_score.
    recommendations: priority-ordered improvement suggestions.
    strengths: plain string list of observed strengths extracted from
        dimension rationales where score >= (max_score - 1).
    improvements: plain string list of improvement areas extracted from
        dimension rationales where score < (max_score / 2).

    generation_metadata: LLM telemetry from the scoring pass:
        prompt_tokens, completion_tokens, response_time_ms, model_used,
        was_cached, error_message (if any).

    raw_ai_response: full raw LLM output before parsing. Stored for
    debugging and quality audits; never exposed in learner-facing responses.
    """

    session_id: UUID
    score_breakdown: ScoreBreakdown = Field(
        ...,
        description="Per-dimension scores + weighted overall_score",
    )
    recommendations: list[RecommendationItem] = Field(
        default_factory=list,
        description="Priority-ordered improvement suggestions (max 5)",
        max_length=5,
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Observed strengths extracted from high-scoring dimensions",
    )
    improvements: list[str] = Field(
        default_factory=list,
        description="Improvement areas extracted from low-scoring dimensions",
    )
    raw_ai_response: str | None = Field(
        default=None,
        description=(
            "Full raw LLM output before parsing; stored for debugging. "
            "Never exposed in learner-facing API responses."
        ),
    )
    generation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "LLM telemetry: prompt_tokens, completion_tokens, "
            "response_time_ms, model_used, was_cached, error_message."
        ),
    )
