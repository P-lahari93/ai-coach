# FILE: app/ai/roleplay_engine.py
"""
RoleplayEngine — generate AI persona turn responses.

Orchestrates:
  1. Load persona definition from module version
  2. Build persona prompt with PromptBuilder
  3. Call LLM via OllamaClient
  4. Parse persona response and extract emotion/coaching notes
  5. Return RoleplayGenerationResponse

The engine manages persona consistency across turns by injecting
the full conversation history into each prompt.
"""
from __future__ import annotations

import json
import re
from uuid import UUID

from app.ai.ollama_client import OllamaClient
from app.ai.prompt_builder import PromptBuilder
from app.core.exceptions import NotFoundError, UnprocessableError
from app.database.unit_of_work import UnitOfWork
from app.schemas.ai.roleplay import (
    PersonaSimulationResponse,
    RoleplayGenerationResponse,
)


class RoleplayEngine:
    """Generate AI persona turn responses for roleplay sessions."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        prompt_builder: PromptBuilder,
    ) -> None:
        """
        Initialize roleplay engine.

        Args:
            ollama_client: LLM client for generation
            prompt_builder: service for resolving prompt templates
        """
        self._ollama = ollama_client
        self._prompt_builder = prompt_builder

    async def generate_turn(
        self,
        session_id: UUID,
        user_message: str,
        persona_id: UUID,
        module_version_id: UUID,
        turn_number: int,
        conversation_history: list[dict],
        session_context: dict,
    ) -> RoleplayGenerationResponse:
        """
        Generate AI persona's next turn response.

        Args:
            session_id: roleplay session UUID
            user_message: learner's current message
            persona_id: persona UUID
            module_version_id: module version UUID
            turn_number: current turn number (1-based)
            conversation_history: prior conversation turns
            session_context: session state including emotion and phase

        Returns:
            RoleplayGenerationResponse with persona content and metadata

        Raises:
            NotFoundError: when persona or module version not found
            UnprocessableError: when LLM generation or parsing fails
        """
        async with UnitOfWork() as uow:
            # Find persona in module version's personas list
            # Use get_version_with_definition for eager loading of personas
            mv_with_def = await uow.module_versions.get_version_with_definition(module_version_id)
            if not mv_with_def:
                raise NotFoundError(f"ModuleVersion {module_version_id} not found")
            
            personas = mv_with_def.personas or []
            persona_obj = next(
                (p for p in personas if p.id == persona_id),
                None,
            )
            
            if not persona_obj:
                raise NotFoundError(f"Persona {persona_id} not found in module version")
            
            # Build persona dict for PromptBuilder
            persona = {
                "name": persona_obj.persona_name,
                "traits": persona_obj.traits or [],
                "system_prompt": persona_obj.system_prompt,
            }
            
            persona_system_prompt = persona_obj.system_prompt
            scenario = session_context.get("scenario_prompt")
            
            prompt_template = self._get_default_roleplay_template()
            
            system_prompt = self._prompt_builder.build_roleplay_prompt(
                template=prompt_template,
                persona=persona,
                scenario=scenario,
                conversation_history=conversation_history,
            )
            
            # Build user message for this turn
            current_emotion = session_context.get("emotion_state", "neutral")
            user_turn_prompt = (
                f"Current emotional state: {current_emotion}\n"
                f"Turn number: {turn_number}\n\n"
                f"Learner says: {user_message}\n\n"
                f"Respond as {persona_obj.persona_name} in JSON format:\n"
                "{\n"
                '    "response": "Your persona response here",\n'
                '    "emotion": "current emotion (one word)",\n'
                '    "coaching_note": "optional coaching note for post-session report or null",\n'
                '    "scenario_phase": "opening|escalation|resolution"\n'
                "}"
            )
            
            # Generate with LLM
            try:
                llm_response = await self._ollama.generate(
                    prompt=user_turn_prompt,
                    system=system_prompt or persona_system_prompt,
                    temperature=0.8,  # Higher for more varied persona responses
                    max_tokens=500,
                )
            except Exception as exc:
                raise UnprocessableError(
                    f"LLM generation failed for persona turn: {exc}"
                ) from exc
            
            # Parse response
            try:
                parsed = self._parse_persona_response(llm_response.content)
            except Exception as exc:
                raise UnprocessableError(
                    f"Failed to parse persona response: {exc}"
                ) from exc
            
            # Build context updates
            updated_context = {
                "emotion_state": parsed.get("emotion", current_emotion),
                "scenario_phase": parsed.get("scenario_phase", "opening"),
            }
            
            # Append coaching flags if note detected
            coaching_note = parsed.get("coaching_note")
            if coaching_note:
                existing_flags = session_context.get("coaching_flags", [])
                updated_context["coaching_flags"] = existing_flags + [
                    {"turn": turn_number, "note": coaching_note}
                ]
            
            # Track turn score (placeholder for MVP)
            turn_scores = session_context.get("turn_scores", [])
            updated_context["turn_scores"] = turn_scores + [
                {"turn": turn_number, "score": None}
            ]
            
            return RoleplayGenerationResponse(
                session_id=session_id,
                turn_number=turn_number,
                persona_content=parsed["response"],
                emotion_detected=parsed.get("emotion"),
                coaching_note=coaching_note,
                updated_context=updated_context,
                raw_ai_response=llm_response.content,
                generation_metadata={
                    "prompt_tokens": llm_response.prompt_tokens,
                    "completion_tokens": llm_response.completion_tokens,
                    "response_time_ms": llm_response.response_time_ms,
                    "model_used": llm_response.model_used,
                },
            )

    async def initialize_persona(
        self,
        session_id: UUID,
        persona_id: UUID,
        module_version_id: UUID,
        scenario_prompt: str | None,
    ) -> PersonaSimulationResponse:
        """
        Initialize a persona for a roleplay session.

        Sets up initial context and optionally generates an opening greeting.

        Args:
            session_id: roleplay session UUID
            persona_id: persona UUID
            module_version_id: module version UUID
            scenario_prompt: optional scenario setup text

        Returns:
            PersonaSimulationResponse with initial context and optional greeting

        Raises:
            NotFoundError: when persona or module version not found
            UnprocessableError: when initialization fails
        """
        async with UnitOfWork() as uow:
            mv_with_def = await uow.module_versions.get_version_with_definition(module_version_id)
            if not mv_with_def:
                raise NotFoundError(f"ModuleVersion {module_version_id} not found")
            
            personas = mv_with_def.personas or []
            persona_obj = next(
                (p for p in personas if p.id == persona_id),
                None,
            )
            
            if not persona_obj:
                raise NotFoundError(f"Persona {persona_id} not found in module version")
            
            # Infer initial emotion from traits
            traits = persona_obj.traits or []
            initial_emotion = self._infer_initial_emotion(traits)
            
            # Build initial context
            initial_context = {
                "emotion_state": initial_emotion,
                "scenario_phase": "opening",
                "coaching_flags": [],
                "turn_scores": [],
                "persona_id": str(persona_id),
                "scenario_prompt": scenario_prompt,
            }
            
            # Generate optional greeting if persona has a greeting template
            persona_greeting = None
            persona_system_prompt = persona_obj.system_prompt
            persona = {
                "name": persona_obj.persona_name,
                "traits": persona_obj.traits or [],
            }
            
            if persona_system_prompt:
                try:
                    greeting_prompt = self._build_greeting_prompt(
                        persona=persona,
                        scenario=scenario_prompt,
                    )
                    
                    llm_response = await self._ollama.generate(
                        prompt=greeting_prompt,
                        system=persona_system_prompt,
                        temperature=0.7,
                        max_tokens=200,
                    )
                    
                    persona_greeting = llm_response.content.strip()
                    
                    return PersonaSimulationResponse(
                        session_id=session_id,
                        persona_id=persona_id,
                        initial_context=initial_context,
                        persona_greeting=persona_greeting,
                        generation_metadata={
                            "prompt_tokens": llm_response.prompt_tokens,
                            "completion_tokens": llm_response.completion_tokens,
                            "response_time_ms": llm_response.response_time_ms,
                            "model_used": llm_response.model_used,
                        },
                    )
                except Exception:
                    # Greeting generation failure is non-fatal
                    pass
            
            return PersonaSimulationResponse(
                session_id=session_id,
                persona_id=persona_id,
                initial_context=initial_context,
                persona_greeting=None,
                generation_metadata={},
            )

    def _get_default_roleplay_template(self) -> str:
        """Return default roleplay system prompt template."""
        return (
            "You are {{persona_name}}, a realistic conversational persona with these traits: {{persona_traits}}. "
            "Respond naturally and consistently with your persona's character. "
            "{{scenario}}\n\n"
            "Conversation so far:\n{{conversation}}"
        )

    def _build_greeting_prompt(
        self,
        persona: dict,
        scenario: str | None,
    ) -> str:
        """Build prompt for generating persona greeting."""
        name = persona.get("name", "Persona")
        traits = ", ".join(persona.get("traits", []))
        
        prompt = (
            f"You are {name} with these traits: {traits}.\n"
            f"Scenario: {scenario or 'Standard roleplay'}\n\n"
            "Generate a natural opening greeting or statement to start the conversation. "
            "Keep it brief (1-3 sentences) and in character."
        )
        return prompt

    def _infer_initial_emotion(self, traits: list[str]) -> str:
        """
        Infer persona's initial emotion from traits.

        Args:
            traits: list of trait adjectives

        Returns:
            initial emotion string
        """
        # Map common traits to emotions
        negative_traits = {
            "impatient", "frustrated", "hostile", "aggressive",
            "dismissive", "skeptical", "demanding", "critical",
        }
        positive_traits = {
            "friendly", "warm", "enthusiastic", "supportive",
            "collaborative", "approachable", "open",
        }
        
        traits_lower = {t.lower() for t in traits}
        
        if traits_lower & negative_traits:
            return "mildly_frustrated"
        elif traits_lower & positive_traits:
            return "neutral"
        else:
            return "neutral"

    def _parse_persona_response(self, response: str) -> dict:
        """
        Parse LLM persona response into structured format.

        Args:
            response: raw LLM response text

        Returns:
            dict with response, emotion, coaching_note, scenario_phase

        Raises:
            ValueError: when response cannot be parsed
        """
        # Try to extract JSON from markdown code fence
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find JSON object directly
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                # No JSON found — treat entire response as persona response
                return {
                    "response": response.strip(),
                    "emotion": "neutral",
                    "coaching_note": None,
                    "scenario_phase": "opening",
                }
        
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            # Fallback to raw response
            return {
                "response": response.strip(),
                "emotion": "neutral",
                "coaching_note": None,
                "scenario_phase": "opening",
            }
        
        persona_response = parsed.get("response", "").strip()
        if not persona_response:
            raise ValueError("Missing response in persona output")
        
        return {
            "response": persona_response,
            "emotion": parsed.get("emotion", "neutral"),
            "coaching_note": parsed.get("coaching_note"),
            "scenario_phase": parsed.get("scenario_phase", "opening"),
        }
