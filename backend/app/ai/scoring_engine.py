# FILE: app/ai/scoring_engine.py
"""
ScoringEngine — parse LLM scoring output into structured ScoreBreakdown.

Orchestrates:
  1. Build scoring prompt from rubric + intake data
  2. Call LLM to score dimensions
  3. Parse JSON response into ScoreDimension objects
  4. Compute weighted overall_score
  5. Return CoachingScoreResponse

Handles markdown code fences in LLM output and validates all required
JSON fields. Raises UnprocessableError on parse failures.
"""
from __future__ import annotations

import json
import re
from uuid import UUID, uuid4

from app.ai.ollama_client import OllamaClient
from app.ai.prompt_builder import PromptBuilder
from app.core.exceptions import UnprocessableError
from app.schemas.ai.scoring import (
    CoachingScoreResponse,
    ScoreBreakdown,
    ScoreDimension,
)


class ScoringEngine:
    """Score coaching sessions against rubric dimensions using LLM."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        prompt_builder: PromptBuilder,
    ) -> None:
        """
        Initialize scoring engine.

        Args:
            ollama_client: LLM client for scoring
            prompt_builder: service for building scoring prompts
        """
        self._ollama = ollama_client
        self._prompt_builder = prompt_builder

    async def score_session(
        self,
        session_id: UUID,
        feedback_text: str,
        rubric: dict,
        intake_data: dict,
        rubric_id: UUID | None = None,
        rubric_version: int = 1,
    ) -> CoachingScoreResponse:
        """
        Score a coaching session against rubric dimensions.

        Args:
            session_id: coaching session UUID
            feedback_text: feedback text to score
            rubric: scoring rubric with dimensions and weights
            intake_data: learner's intake form submission
            rubric_id: optional rubric UUID for audit trail
            rubric_version: rubric version for audit trail

        Returns:
            CoachingScoreResponse with structured scores

        Raises:
            UnprocessableError: when LLM scoring or parsing fails
        """
        # Build scoring prompt
        template = self._get_scoring_template(rubric)
        
        prompt = self._prompt_builder.build_scoring_prompt(
            template=template,
            intake_data=intake_data,
            rubric=rubric,
            feedback_text=feedback_text,
        )
        
        # Generate scoring response from LLM
        try:
            llm_response = await self._ollama.generate(
                prompt=prompt,
                system=(
                    "You are an expert assessor evaluating coaching feedback against rubric criteria. "
                    "Provide precise, objective scores with clear rationale."
                ),
                temperature=0.3,  # Low temperature for consistent scoring
            )
        except Exception as exc:
            raise UnprocessableError(
                f"LLM scoring generation failed: {exc}"
            ) from exc
        
        # Parse LLM response
        try:
            dimensions = self._parse_scoring_response(
                llm_response.content,
                rubric,
            )
        except Exception as exc:
            raise UnprocessableError(
                f"Failed to parse scoring response: {exc}"
            ) from exc
        
        # Compute weighted overall score
        overall_score = self._compute_weighted_score(dimensions)
        
        # Build ScoreBreakdown
        score_breakdown = ScoreBreakdown(
            dimensions=dimensions,
            overall_score=overall_score,
            rubric_id=rubric_id or uuid4(),
            rubric_version=rubric_version,
        )
        
        # Extract strengths and improvements from dimension scores
        strengths = self._extract_strengths(dimensions)
        improvements = self._extract_improvements(dimensions)
        
        return CoachingScoreResponse(
            session_id=session_id,
            score_breakdown=score_breakdown,
            recommendations=[],  # populated by RecommendationEngine
            strengths=strengths,
            improvements=improvements,
            raw_ai_response=llm_response.content,
            generation_metadata={
                "prompt_tokens": llm_response.prompt_tokens,
                "completion_tokens": llm_response.completion_tokens,
                "response_time_ms": llm_response.response_time_ms,
                "model_used": llm_response.model_used,
            },
        )

    def _get_scoring_template(self, rubric: dict) -> str:
        """
        Build scoring prompt template based on rubric structure.

        Returns:
            prompt template string
        """
        return """You are scoring the following feedback submission against this rubric:

RUBRIC:
{{rubric}}

LEARNER'S SUBMISSION:
{{intake_data}}

FEEDBACK TO EVALUATE:
{{feedback}}

Score each rubric dimension based on the feedback quality and submission content.

Respond with ONLY a JSON object in this exact format:
{
    "dimensions": [
        {
            "dimension_name": "Exact Dimension Name from Rubric",
            "score": <integer score within the band range>,
            "max_score": <maximum possible score>,
            "weight": <dimension weight>,
            "rationale": "Clear explanation for this score"
        }
    ],
    "strengths": ["Strength based on high scores", "Another strength"],
    "improvements": ["Area to improve based on low scores", "Another area"]
}

Be objective and reference specific evidence from the submission for each score."""

    def _parse_scoring_response(
        self,
        response: str,
        rubric: dict,
    ) -> list[ScoreDimension]:
        """
        Parse LLM scoring response into ScoreDimension objects.

        Args:
            response: raw LLM response text
            rubric: scoring rubric for validation

        Returns:
            list of ScoreDimension objects

        Raises:
            ValueError: when response cannot be parsed or is missing dimensions
        """
        # Extract JSON from markdown code fences if present
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                raise ValueError("No JSON found in scoring response")
        
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in scoring response: {exc}") from exc
        
        raw_dimensions = data.get("dimensions", [])
        if not raw_dimensions:
            raise ValueError("No dimensions found in scoring response")
        
        # Build rubric lookup for validation
        rubric_dims = {
            d.get("name", ""): d
            for d in rubric.get("dimensions", [])
        }
        
        dimensions: list[ScoreDimension] = []
        
        for raw_dim in raw_dimensions:
            dim_name = raw_dim.get("dimension_name", "")
            if not dim_name:
                continue
            
            # Get rubric values for this dimension
            rubric_dim = rubric_dims.get(dim_name, {})
            bands = rubric_dim.get("band_descriptors", {})
            max_score = raw_dim.get("max_score", len(bands) if bands else 4)
            weight = raw_dim.get("weight", rubric_dim.get("weight", 0.0))
            
            score = raw_dim.get("score", 1)
            # Clamp score to valid range
            score = max(0, min(score, max_score))
            
            rationale = raw_dim.get("rationale", "No rationale provided")
            
            dimensions.append(
                ScoreDimension(
                    dimension_name=dim_name,
                    score=score,
                    max_score=max_score,
                    weight=weight,
                    rationale=rationale,
                )
            )
        
        if not dimensions:
            raise ValueError("No valid dimensions parsed from scoring response")
        
        return dimensions

    def _compute_weighted_score(self, dimensions: list[ScoreDimension]) -> float:
        """
        Compute weighted overall score normalized to 0-100.

        Formula: sum(score * weight * (100 / max_score))

        Args:
            dimensions: list of scored dimensions

        Returns:
            overall score on 0-100 scale
        """
        total_weighted = 0.0
        total_weight = 0.0
        
        for dim in dimensions:
            if dim.max_score > 0:
                normalized = (dim.score / dim.max_score) * 100
                total_weighted += normalized * dim.weight
                total_weight += dim.weight
        
        if total_weight == 0:
            return 0.0
        
        # Normalize if weights don't sum to 1.0
        result = total_weighted / total_weight if total_weight != 1.0 else total_weighted
        return round(min(100.0, max(0.0, result)), 2)

    def _extract_strengths(self, dimensions: list[ScoreDimension]) -> list[str]:
        """
        Extract strengths from high-scoring dimensions.

        A dimension is a strength when score >= max_score - 1.
        """
        strengths = []
        for dim in dimensions:
            if dim.score >= dim.max_score - 1:
                strengths.append(
                    f"{dim.dimension_name}: {dim.rationale}"
                )
        return strengths

    def _extract_improvements(self, dimensions: list[ScoreDimension]) -> list[str]:
        """
        Extract improvement areas from low-scoring dimensions.

        A dimension needs improvement when score < max_score / 2.
        """
        improvements = []
        for dim in dimensions:
            if dim.max_score > 0 and dim.score < dim.max_score / 2:
                improvements.append(
                    f"{dim.dimension_name}: {dim.rationale}"
                )
        return improvements
