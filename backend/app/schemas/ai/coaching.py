"""
AI coaching schemas — feedback generation and conversation analysis.

Covers:
  - CoachingRequest             — inbound request to generate feedback
  - CoachingResponse            — AI-generated coaching feedback response
  - CoachingFeedbackRequest     — intake submission + metadata for scoring
  - CoachingFeedbackResponse    — scored feedback with citations + next steps
  - ConversationAnalysisRequest — analyze a multi-turn conversation
  - ConversationAnalysisResponse— sentiment + quality insights

These schemas are used by the AI engine service layer to interface with
the LLM orchestration layer. They are NOT directly exposed in the public
API — the public-facing feedback schemas live in session/feedback_report.py.

CoachingRequest flow:
  1. Service receives CoachingFeedbackRequest from API layer
  2. Service loads module version, rubric, knowledge context
  3. Service constructs CoachingRequest and sends to AI engine
  4. AI engine returns CoachingResponse
  5. Service maps CoachingResponse → FeedbackReportResponse (public schema)

ConversationAnalysisRequest:
  Used for advanced analytics and quality monitoring — analyzes the
  full session conversation for sentiment, off-topic detection, and
  coaching effectiveness signals.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── CoachingRequest ───────────────────────────────────────────────────────────

class CoachingRequest(BaseModel):
    """
    Internal request passed from the service layer to the AI engine for
    coaching feedback generation.

    Carries all context needed by the LLM: module definition, learner
    intake data, rubric, retrieved knowledge, and conversation history.

    prompt_template: resolved from ModulePromptTemplate.template_body
    with all {{variable}} slots filled by the PromptBuilder.

    knowledge_context: list of retrieved chunk texts from RAG; already
    filtered and ranked. Empty list when no relevant knowledge found.

    conversation_history: chronological list of {role, content} dicts
    from ConversationMessage rows. Used for multi-turn coaching sessions
    where the AI needs to reference prior dialogue.
    """

    # model_name is a domain field (Ollama model identifier); suppress
    # Pydantic's protected 'model_' namespace warning.
    model_config = {"protected_namespaces": ()}

    session_id: UUID = Field(
        ...,
        description="CoachingSession UUID; used for telemetry correlation",
    )
    module_key: str = Field(
        ...,
        description="Module key, e.g. 'sbi_feedback', for logging and routing",
    )
    framework_name: str = Field(
        ...,
        description="Framework name from ModuleVersion, e.g. 'SBI'",
    )
    intake_data: dict[str, str] = Field(
        ...,
        description=(
            "Learner's filled intake form keyed by field_key. "
            "Example: {'situation': '...', 'behaviour': '...', 'impact': '...'}"
        ),
    )
    rubric_dimensions: list[dict[str, Any]] = Field(
        ...,
        description=(
            "Rubric dimensions from ModuleVersion.scoring_rubric['dimensions']. "
            "Each dict: {name, weight, band_descriptors}."
        ),
    )
    prompt_template: str = Field(
        ...,
        description=(
            "Fully resolved prompt text with all {{variable}} slots filled. "
            "This is the final system message sent to the LLM."
        ),
    )
    knowledge_context: list[str] = Field(
        default_factory=list,
        description=(
            "Retrieved knowledge chunk texts from RAG, pre-filtered and ranked. "
            "Empty when no relevant knowledge found (knowledge_used=False)."
        ),
    )
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Chronological list of {role: 'user'|'assistant'|'system', "
            "content: str} dicts from prior conversation turns."
        ),
    )
    model_name: str = Field(
        default="qwen2.5:3b",
        description="Ollama model to use for generation, e.g. 'qwen2.5:3b'",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="LLM temperature; 0.0 = deterministic, 2.0 = very creative",
    )
    max_tokens: int = Field(
        default=2000,
        ge=100,
        le=8000,
        description="Maximum completion tokens for the feedback response",
    )


# ── CoachingResponse ──────────────────────────────────────────────────────────

class CoachingResponse(BaseModel):
    """
    AI engine's coaching feedback generation result.

    Returned by the AI engine service to the coaching service layer.
    The service layer then maps this into a FeedbackReportResponse
    (public schema) before storing and returning to the API.

    scores: dict keyed by dimension name, values are {score: int, rationale: str}.
    overall_score: weighted average computed by the AI engine using rubric weights.
    strengths/improvements: plain string lists extracted from the LLM output.
    recommendations: structured list of {priority, area, suggestion, example?}.
    citations: list of {source_title, kb_id, source_id, snippet, relevance}.

    generation_metadata: telemetry from the LLM call:
        prompt_tokens, completion_tokens, response_time_ms, model_used,
        was_cached, error_message (if any).
    """

    session_id: UUID
    feedback_text: str = Field(
        ...,
        description="Full narrative feedback generated by the AI coach",
    )
    scores: dict[str, dict[str, Any]] = Field(
        ...,
        description=(
            "Per-dimension scores. Keys: dimension names. "
            "Values: {score: int, rationale: str}."
        ),
    )
    overall_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Weighted average of dimension scores; 0.00–100.00",
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Observed strengths extracted from the feedback",
    )
    improvements: list[str] = Field(
        default_factory=list,
        description="Improvement areas extracted from the feedback",
    )
    recommendations: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Structured recommendations. Each: "
            "{priority: int, area: str, suggestion: str, example?: str}."
        ),
    )
    next_steps: str | None = Field(
        default=None,
        description="Actionable next-step text from the AI coach",
    )
    citations: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "RAG citations used. Each: {source_title, kb_id, source_id, "
            "snippet, relevance}. Empty when knowledge_used=False."
        ),
    )
    knowledge_used: bool = Field(
        ...,
        description="True if RAG retrieved at least one relevant chunk",
    )
    raw_ai_response: str | None = Field(
        default=None,
        description=(
            "Full raw LLM output before parsing. Stored for debugging; "
            "never exposed in learner-facing API responses."
        ),
    )
    generation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Telemetry: prompt_tokens, completion_tokens, response_time_ms, "
            "model_used, was_cached, error_message."
        ),
    )


# ── CoachingFeedbackRequest ───────────────────────────────────────────────────

class CoachingFeedbackRequest(BaseModel):
    """
    Service-layer request to generate feedback for a completed session.

    This is the input to the coaching service's generate_feedback() method.
    The service uses this to construct a CoachingRequest for the AI engine.

    session_id: identifies which CoachingSession to score.
    user_id: for telemetry and user-scoped RAG retrieval weighting.
    tenant_id: for tenant-scoped knowledge base resolution.
    module_version_id: pinned module version — immutable after session start.

    The service layer loads all necessary context (module, rubric, knowledge)
    based on these IDs before calling the AI engine.
    """

    session_id: UUID
    user_id: UUID
    tenant_id: UUID | None = None
    module_version_id: UUID = Field(
        ...,
        description="Pinned module version; never changes after session creation",
    )
    intake_data: dict[str, str] = Field(
        ...,
        description="Learner's completed intake form submission",
    )
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
        description="Optional: prior conversation turns for context",
    )


# ── CoachingFeedbackResponse ──────────────────────────────────────────────────

class CoachingFeedbackResponse(BaseModel):
    """
    Service-layer response after successful feedback generation.

    Returned by the coaching service after storing the FeedbackReport
    row in the DB. This schema is an internal contract between the
    service and API layers — the public-facing schema is
    FeedbackReportResponse (from session/feedback_report.py).

    feedback_report_id: UUID of the newly created FeedbackReport row.
    overall_score: for quick access without fetching the full report.
    knowledge_used: indicates whether RAG was used (affects UI display).
    generation_time_ms: end-to-end time from request to DB write.
    """

    feedback_report_id: UUID
    session_id: UUID
    overall_score: float = Field(..., ge=0.0, le=100.0)
    knowledge_used: bool
    generation_time_ms: int = Field(
        ...,
        ge=0,
        description="End-to-end feedback generation time in milliseconds",
    )


# ── ConversationAnalysisRequest ───────────────────────────────────────────────

class ConversationAnalysisRequest(BaseModel):
    """
    Request to analyze a full coaching session conversation for quality
    and sentiment insights.

    Used for advanced analytics dashboards and coaching quality monitoring.
    Not part of the learner-facing feedback flow — this is admin/analytics
    tooling for programme effectiveness measurement.

    conversation_turns: chronological list of {role, content, timestamp}.
    module_context: optional module metadata for domain-aware analysis.
    """

    session_id: UUID
    conversation_turns: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            "Chronological conversation turns. Each: "
            "{role: str, content: str, timestamp: str}."
        ),
    )
    module_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional module metadata for domain-aware analysis",
    )
    analysis_types: list[str] = Field(
        default_factory=lambda: ["sentiment", "quality", "engagement"],
        description=(
            "Which analysis passes to run. Options: "
            "'sentiment', 'quality', 'engagement', 'off_topic_detection'."
        ),
    )


# ── ConversationAnalysisResponse ──────────────────────────────────────────────

class ConversationAnalysisResponse(BaseModel):
    """
    Conversation analysis result from the AI engine.

    sentiment_summary: overall sentiment trend across the conversation.
        Examples: "positive", "neutral", "frustrated", "confused".

    quality_score: 0–100 score indicating conversation quality based on:
        - coherence (does the conversation flow logically?)
        - depth (are topics explored thoroughly?)
        - relevance (is the conversation on-topic for the module?)

    engagement_indicators: signals of learner engagement:
        {
          "turn_count": 8,
          "avg_turn_length": 45,
          "question_count": 3,
          "affirmation_count": 2
        }

    off_topic_turns: list of turn indices where the conversation drifted
        off-topic. Used to flag sessions needing human review.

    insights: free-form text summary of notable patterns or concerns
        identified by the LLM during analysis.
    """

    session_id: UUID
    sentiment_summary: str = Field(
        ...,
        description=(
            "Overall sentiment trend: 'positive', 'neutral', 'frustrated', "
            "'confused', etc."
        ),
    )
    quality_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Conversation quality score based on coherence, depth, relevance",
    )
    engagement_indicators: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Engagement signals: turn_count, avg_turn_length, "
            "question_count, affirmation_count."
        ),
    )
    off_topic_turns: list[int] = Field(
        default_factory=list,
        description="Turn indices (0-based) where conversation drifted off-topic",
    )
    insights: str | None = Field(
        default=None,
        description="Free-form summary of notable patterns or concerns",
    )
    generation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="LLM telemetry: model_used, response_time_ms, tokens",
    )
