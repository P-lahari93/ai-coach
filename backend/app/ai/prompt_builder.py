# FILE: app/ai/prompt_builder.py
"""
PromptBuilder — resolve variable slots in prompt templates.

Resolves {{variable}} placeholders in ModulePromptTemplate.template_body
with actual values from:
  - intake_data: learner's form submission
  - rubric: scoring dimensions
  - knowledge_chunks: retrieved RAG context
  - framework_name: module framework
  - conversation_history: prior turns
  - persona: roleplay persona definition
  - scenario: roleplay scenario

Variables are case-insensitive and whitespace-tolerant:
  {{situation}}, {{ situation }}, {{SITUATION}} all resolve to the same value.
"""
from __future__ import annotations

import re
from typing import Any


class PromptBuilder:
    """Resolve template variables in LLM prompts."""

    def __init__(self) -> None:
        """Initialize prompt builder with variable pattern."""
        # Match {{variable_name}} with optional whitespace
        self._variable_pattern = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

    def build_coaching_prompt(
        self,
        template: str,
        intake_data: dict[str, Any],
        rubric: dict[str, Any],
        knowledge_chunks: list[str],
        framework_name: str,
    ) -> str:
        """
        Build coaching prompt by resolving template variables.

        Available variables:
          - {{framework}}: framework_name
          - {{knowledge}}: formatted knowledge context
          - {{rubric}}: formatted rubric dimensions
          - Any key from intake_data (e.g. {{situation}}, {{behaviour}}, {{impact}})

        Args:
            template: template text with {{variable}} placeholders
            intake_data: learner's intake form submission
            rubric: scoring rubric dict with 'dimensions' key
            knowledge_chunks: list of retrieved chunk texts
            framework_name: module framework name

        Returns:
            resolved prompt text
        """
        # Build variable map
        variables = {
            "framework": framework_name,
            "knowledge": self._format_knowledge(knowledge_chunks),
            "rubric": self._format_rubric(rubric),
        }
        
        # Add intake data fields (lowercase keys for case-insensitive matching)
        for key, value in intake_data.items():
            variables[key.lower()] = str(value)
        
        # Resolve variables
        return self._resolve_variables(template, variables)

    def build_roleplay_prompt(
        self,
        template: str,
        persona: dict[str, Any],
        scenario: str | None,
        conversation_history: list[dict[str, str]],
    ) -> str:
        """
        Build roleplay prompt by resolving template variables.

        Available variables:
          - {{persona_name}}: persona display name
          - {{persona_traits}}: comma-separated trait list
          - {{scenario}}: scenario context (or empty if None)
          - {{conversation}}: formatted conversation history

        Args:
            template: template text with {{variable}} placeholders
            persona: persona dict with 'name' and 'traits' keys
            scenario: optional scenario description
            conversation_history: list of {role, content} dicts

        Returns:
            resolved prompt text
        """
        variables = {
            "persona_name": persona.get("name", "Unknown"),
            "persona_traits": ", ".join(persona.get("traits", [])),
            "scenario": scenario or "",
            "conversation": self._format_conversation(conversation_history),
        }
        
        return self._resolve_variables(template, variables)

    def build_scoring_prompt(
        self,
        template: str,
        intake_data: dict[str, Any],
        rubric: dict[str, Any],
        feedback_text: str,
    ) -> str:
        """
        Build scoring prompt by resolving template variables.

        Available variables:
          - {{rubric}}: formatted rubric dimensions
          - {{feedback}}: the feedback text to score
          - Any key from intake_data

        Args:
            template: template text with {{variable}} placeholders
            intake_data: learner's intake form submission
            rubric: scoring rubric dict
            feedback_text: feedback text to score

        Returns:
            resolved prompt text
        """
        variables = {
            "rubric": self._format_rubric(rubric),
            "feedback": feedback_text,
        }
        
        # Add intake data
        for key, value in intake_data.items():
            variables[key.lower()] = str(value)
        
        return self._resolve_variables(template, variables)

    def _resolve_variables(
        self,
        template: str,
        variables: dict[str, str],
    ) -> str:
        """
        Resolve all {{variable}} placeholders in template.

        Variable names are case-insensitive. Unresolved variables are
        left as-is (not replaced with empty string).

        Args:
            template: template text
            variables: variable name -> value mapping

        Returns:
            resolved template text
        """
        def replacer(match: re.Match) -> str:
            var_name = match.group(1).lower()
            return variables.get(var_name, match.group(0))
        
        return self._variable_pattern.sub(replacer, template)

    def _format_knowledge(self, chunks: list[str]) -> str:
        """
        Format knowledge chunks with source attribution.

        Args:
            chunks: list of chunk texts

        Returns:
            formatted knowledge context or placeholder message
        """
        if not chunks:
            return "No specific knowledge found for this query."
        
        # Chunks are already formatted with [Source: ...] by CitationService
        return "\n\n".join(chunks)

    def _format_rubric(self, rubric: dict[str, Any]) -> str:
        """
        Format rubric dimensions for prompt injection.

        Output format:
            Dimension: Situation Clarity (weight: 0.3)
            Bands:
              1: Vague or missing situation
              2: Some context provided
              3: Clear situation with relevant details
              4: Comprehensive situation description

        Args:
            rubric: rubric dict with 'dimensions' key

        Returns:
            formatted rubric text
        """
        dimensions = rubric.get("dimensions", [])
        if not dimensions:
            return "No rubric dimensions defined."
        
        parts: list[str] = []
        for dim in dimensions:
            name = dim.get("name", "Unknown")
            weight = dim.get("weight", 0.0)
            bands = dim.get("band_descriptors", {})
            
            parts.append(f"Dimension: {name} (weight: {weight})")
            parts.append("Bands:")
            
            for score, descriptor in sorted(bands.items()):
                parts.append(f"  {score}: {descriptor}")
            
            parts.append("")  # blank line between dimensions
        
        return "\n".join(parts).strip()

    def _format_conversation(
        self,
        history: list[dict[str, str]],
    ) -> str:
        """
        Format conversation history for prompt injection.

        Output format:
            User: message text
            Persona: response text
            User: next message

        Args:
            history: list of {role, content} dicts

        Returns:
            formatted conversation text or empty string
        """
        if not history:
            return ""
        
        lines: list[str] = []
        for turn in history:
            role = turn.get("role", "unknown").capitalize()
            content = turn.get("content", "")
            lines.append(f"{role}: {content}")
        
        return "\n".join(lines)
