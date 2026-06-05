"""
AI roleplay schemas — persona simulation and turn generation.

Covers:
  - RoleplayGenerationRequest  — generate AI persona's next turn
  - RoleplayGenerationResponse — AI persona response + emotion + coaching note
  - PersonaSimulationRequest   — configure persona for a roleplay session
  - PersonaSimulationResponse  — persona setup confirmation + initial state

These schemas are used by the AI roleplay engine to simulate realistic
conversational personas. They are NOT directly exposed in the public API —
the public-facing roleplay schemas live in session/roleplay_session.py.

RoleplayGenerationRequest flow:
  1. Learner submits a turn via POST /sessions/roleplay/{id}/turn
  2. Service receives RoleplayTurnRequest (public schema)
  3. Service loads persona, session context, conversation history
  4. Service constructs RoleplayGenerationRequest and sends to AI engine
  5. AI engine returns RoleplayGenerationResponse
  6. Service stores persona response as RoleplayMessage
  7. Service updates session context JSONB with emotion state, flags
  8. Service returns RoleplayTurnResponse (public schema) to API

PersonaSimulationRequest:
  Used at roleplay session creation to set up the AI persona's initial
  state, system prompt, and behavioral parameters. The session context
  JSONB is initialized based on the persona traits and scenario prompt.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── RoleplayGenerationRequest ─────────────────────────────────────────────────

class RoleplayGenerationRequest(BaseModel):
    """
    Internal request to generate the AI persona's next turn in a roleplay.

    Carries all context needed by the LLM: persona definition, scenario,
    conversation history, current emotional state, and any coaching flags
    raised by previous turns.

    persona_system_prompt: resolved from ModulePersona.system_prompt +
    ModulePromptTemplate (type='roleplay_system'). Already includes
    persona traits and behavioral guidance.

    scenario_context: optional setup text describing the roleplay scenario,
    e.g. "You are being asked for feedback by your manager after a client
    presentation that went poorly."

    conversation_history: chronological list of user/persona turns.
    Each dict: {role: 'user'|'persona', content: str, turn_number: int}.

    session_context: mutable state bag from RoleplaySession.context JSONB.
    Keys managed by the roleplay engine:
        emotion_state     str   — current emotion: 'neutral', 'frustrated', etc.
        scenario_phase    str   — 'opening', 'escalation', 'resolution'
        coaching_flags    list  — issues to highlight in post-session report
        turn_scores       list  — per-turn quality scores (for analytics)

    turn_number: 1-based counter; used for logging and turn-level telemetry.
    """

    # model_name is a domain field (Ollama model identifier); suppress
    # Pydantic's protected 'model_' namespace warning.
    model_config = {"protected_namespaces": ()}

    session_id: UUID = Field(
        ...,
        description="RoleplaySession UUID; used for telemetry correlation",
    )
    module_key: str = Field(
        ...,
        description="Module key, e.g. 'sales_objection_handling', for routing",
    )
    persona_name: str = Field(
        ...,
        description="Persona display name, e.g. 'Hostile Prospect'",
    )
    persona_traits: list[str] = Field(
        ...,
        description=(
            "Trait adjectives from ModulePersona.traits JSONB, "
            "e.g. ['direct', 'impatient', 'detail-oriented']."
        ),
    )
    persona_system_prompt: str = Field(
        ...,
        description=(
            "Fully resolved system prompt with persona definition and "
            "behavioral guidance. Sent as the LLM system message."
        ),
    )
    scenario_context: str | None = Field(
        default=None,
        description=(
            "Optional scenario setup text shown to the learner before "
            "the roleplay starts."
        ),
    )
    user_message: str = Field(
        ...,
        description="The learner's current turn message to respond to",
    )
    conversation_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Chronological prior turns. Each: "
            "{role: 'user'|'persona', content: str, turn_number: int}."
        ),
    )
    session_context: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Mutable engine state: emotion_state, scenario_phase, "
            "coaching_flags, turn_scores."
        ),
    )
    turn_number: int = Field(
        ...,
        ge=1,
        description="1-based turn counter for this session",
    )
    model_name: str = Field(
        default="qwen2.5:3b",
        description="Ollama model to use for persona generation",
    )
    temperature: float = Field(
        default=0.8,
        ge=0.0,
        le=2.0,
        description="Higher temperature for more varied persona responses",
    )
    max_tokens: int = Field(
        default=500,
        ge=50,
        le=2000,
        description="Max tokens for persona response; shorter than coaching",
    )


# ── RoleplayGenerationResponse ────────────────────────────────────────────────

class RoleplayGenerationResponse(BaseModel):
    """
    AI persona's generated turn response.

    Returned by the AI roleplay engine to the roleplay service layer.
    The service stores this as a RoleplayMessage row and updates the
    session context JSONB before returning a RoleplayTurnResponse
    (public schema) to the API.

    persona_content: the AI persona's spoken response to the learner.
    This is what the learner sees during the roleplay.

    emotion_detected: optional emotion tag set by the AI engine during
    generation. Examples: "frustrated", "curious", "resistant", "pleased".
    Used by the scoring engine and shown in the post-session report.

    coaching_note: optional inline coaching hint generated by the AI
    during the turn. NOT shown during the roleplay — revealed only in
    the post-session FeedbackReport to preserve immersion.

    updated_context: changes to apply to RoleplaySession.context JSONB.
    The service merges these updates into the existing context dict.
    Example: {"emotion_state": "frustrated", "scenario_phase": "escalation"}.

    generation_metadata: LLM telemetry (tokens, latency, model, cache hit).
    """

    session_id: UUID
    turn_number: int = Field(..., ge=1)
    persona_content: str = Field(
        ...,
        description="The AI persona's spoken response shown to the learner",
    )
    emotion_detected: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "Emotion tag on this turn: 'frustrated', 'curious', 'resistant', etc. "
            "Set by the AI engine; used in post-session report."
        ),
    )
    coaching_note: str | None = Field(
        default=None,
        description=(
            "Inline coaching hint; hidden during roleplay, revealed in "
            "post-session report."
        ),
    )
    updated_context: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Context updates to merge into RoleplaySession.context JSONB. "
            "Keys: emotion_state, scenario_phase, coaching_flags, turn_scores."
        ),
    )
    raw_ai_response: str | None = Field(
        default=None,
        description="Full raw LLM output before parsing; stored for debugging",
    )
    generation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Telemetry: prompt_tokens, completion_tokens, response_time_ms, "
            "model_used, was_cached."
        ),
    )


# ── PersonaSimulationRequest ──────────────────────────────────────────────────

class PersonaSimulationRequest(BaseModel):
    """
    Request to initialize a roleplay persona at session creation.

    Used by the roleplay service when a new RoleplaySession is created.
    The AI engine uses this to:
      1. Validate the persona definition is coherent
      2. Generate initial emotional state and scenario phase
      3. Pre-warm the LLM cache with the persona system prompt
      4. Return the initial session context JSONB

    persona_id: UUID of the ModulePersona row.
    scenario_prompt: optional scenario setup text to contextualize the persona.
    If None, the persona's system_prompt handles all setup.

    initial_emotion: optional override for the persona's starting emotion.
    If None, the AI engine infers a default based on persona traits
    (e.g. 'impatient' trait → start as 'neutral' or 'mildly_frustrated').
    """

    # model_name is a domain field (Ollama model identifier); suppress
    # Pydantic's protected 'model_' namespace warning.
    model_config = {"protected_namespaces": ()}

    session_id: UUID = Field(
        ...,
        description="RoleplaySession UUID being initialized",
    )
    module_key: str
    persona_id: UUID = Field(
        ...,
        description="UUID of the ModulePersona to simulate",
    )
    persona_name: str
    persona_traits: list[str] = Field(
        ...,
        description="Trait adjectives from ModulePersona.traits JSONB",
    )
    persona_system_prompt: str = Field(
        ...,
        description="Full system message from ModulePersona.system_prompt",
    )
    scenario_prompt: str | None = Field(
        default=None,
        description=(
            "Optional scenario setup; shown to learner before roleplay starts. "
            "If None, persona system_prompt must handle all context."
        ),
    )
    initial_emotion: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "Optional override for starting emotion. "
            "If None, AI engine infers from persona traits."
        ),
    )
    model_name: str = Field(
        default="qwen2.5:3b",
        description="Ollama model to use for this persona",
    )


# ── PersonaSimulationResponse ─────────────────────────────────────────────────

class PersonaSimulationResponse(BaseModel):
    """
    Persona initialization result.

    Returned by the AI engine after PersonaSimulationRequest.
    The roleplay service uses this to populate RoleplaySession.context
    JSONB at session creation.

    initial_context: the starting state for RoleplaySession.context.
    Keys:
        emotion_state    str   — inferred or provided initial emotion
        scenario_phase   str   — always "opening" at start
        coaching_flags   list  — empty at start
        turn_scores      list  — empty at start
        custom           dict  — module-specific state variables

    persona_greeting: optional opening message from the persona.
    If not None, stored as the first RoleplayMessage (turn_number=0,
    role='persona') to kick off the roleplay without requiring the
    learner to speak first.

    generation_metadata: LLM telemetry from the setup pass.
    """

    session_id: UUID
    persona_id: UUID
    initial_context: dict[str, Any] = Field(
        ...,
        description=(
            "Starting state for RoleplaySession.context JSONB. "
            "Keys: emotion_state, scenario_phase, coaching_flags, turn_scores."
        ),
    )
    persona_greeting: str | None = Field(
        default=None,
        description=(
            "Optional opening message from the persona to start the roleplay. "
            "If set, stored as turn_number=0 persona message."
        ),
    )
    generation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Telemetry from the persona setup pass",
    )
