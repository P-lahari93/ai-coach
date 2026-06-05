# FILE: app/ai/recommendation_engine.py
"""
RecommendationEngine — generate improvement recommendations from scores.

Identifies low-scoring rubric dimensions and generates priority-ranked
improvement suggestions based on:
  - Score gap (how far below maximum)
  - Dimension weight (impact on overall score)
  - Feasibility of improvement

Recommendations are generated from ScoreBreakdown dimensions where
score < threshold (default: 75% of max score).
"""
from __future__ import annotations

from app.schemas.ai.scoring import RecommendationItem, ScoreBreakdown, ScoreDimension


# Threshold below which a dimension is considered below target (as % of max)
_BELOW_TARGET_RATIO = 0.75

# Default action verbs for recommendation suggestions
_IMPROVEMENT_VERBS = {
    "specificity": "Use specific, observable language",
    "situation": "Describe the context more precisely",
    "behaviour": "Focus on observable actions, not interpretations",
    "impact": "Quantify the impact where possible",
    "clarity": "Structure your message more clearly",
    "depth": "Provide more supporting evidence",
    "tone": "Adjust your tone to be more constructive",
    "structure": "Follow the framework structure more closely",
}


class RecommendationEngine:
    """Generate improvement recommendations from scoring breakdown."""

    def __init__(self) -> None:
        """Initialize recommendation engine."""
        self._below_target_ratio = _BELOW_TARGET_RATIO

    def extract_recommendations(
        self,
        score_breakdown: ScoreBreakdown,
        top_n: int = 3,
    ) -> list[RecommendationItem]:
        """
        Identify low-scoring dimensions and generate recommendations.

        Dimensions below the target threshold are ranked by:
          1. Score gap (larger gap = higher priority)
          2. Dimension weight (higher weight = higher priority for overall score impact)

        Args:
            score_breakdown: full scoring breakdown from ScoringEngine
            top_n: maximum recommendations to return (default 3)

        Returns:
            list of RecommendationItem sorted by priority (1 = most important)
        """
        # Identify below-target dimensions
        below_target = [
            dim for dim in score_breakdown.dimensions
            if dim.max_score > 0
            and (dim.score / dim.max_score) < self._below_target_ratio
        ]
        
        if not below_target:
            return []
        
        # Rank by priority: weighted score gap (larger = higher priority)
        ranked = sorted(
            below_target,
            key=lambda d: self._priority_score(d),
            reverse=True,
        )
        
        # Take top N
        top_dims = ranked[:top_n]
        
        # Build RecommendationItem for each
        recommendations: list[RecommendationItem] = []
        
        for priority_idx, dim in enumerate(top_dims, start=1):
            suggestion = self._generate_suggestion(dim)
            example = self._generate_example(dim)
            
            recommendations.append(
                RecommendationItem(
                    priority=priority_idx,
                    area=dim.dimension_name,
                    suggestion=suggestion,
                    example=example,
                )
            )
        
        return recommendations

    def _priority_score(self, dim: ScoreDimension) -> float:
        """
        Compute priority score for a dimension.

        Higher score = should be recommended first.
        Considers: score gap and dimension weight.

        Args:
            dim: scored dimension

        Returns:
            float priority score (higher = more important)
        """
        if dim.max_score == 0:
            return 0.0
        
        # Score gap as fraction of max (0 = perfect, 1 = zero score)
        gap_ratio = 1.0 - (dim.score / dim.max_score)
        
        # Combine gap with weight impact
        return gap_ratio * dim.weight

    def _generate_suggestion(self, dim: ScoreDimension) -> str:
        """
        Generate actionable improvement suggestion for a dimension.

        Derives suggestion from dimension name keywords and rationale.

        Args:
            dim: below-target scored dimension

        Returns:
            actionable suggestion string
        """
        name_lower = dim.dimension_name.lower()
        
        # Check for keyword matches in name
        for keyword, suggestion in _IMPROVEMENT_VERBS.items():
            if keyword in name_lower:
                return suggestion
        
        # Fallback: generate suggestion from rationale
        if dim.rationale and len(dim.rationale) > 20:
            # Extract a suggestion phrase from rationale
            rationale = dim.rationale.rstrip(".")
            return f"Improve {dim.dimension_name}: {rationale}"
        
        return (
            f"Review your approach to {dim.dimension_name}. "
            f"Aim for a score of {dim.max_score} by addressing the key criteria."
        )

    def _generate_example(self, dim: ScoreDimension) -> str | None:
        """
        Generate an example for the recommendation.

        For MVP, returns a generic example based on dimension name.
        In production, this would be populated by the LLM during scoring.

        Args:
            dim: below-target scored dimension

        Returns:
            example string or None
        """
        name_lower = dim.dimension_name.lower()
        
        # Provide targeted examples for common rubric dimensions
        examples = {
            "situation": (
                "Instead of 'in the meeting', say 'during the Tuesday team stand-up "
                "with 5 direct reports present'."
            ),
            "behaviour": (
                "Instead of 'you were dismissive', say 'you interrupted me three times "
                "while I was presenting the proposal'."
            ),
            "impact": (
                "Instead of 'it caused problems', say 'it delayed the project by two weeks "
                "and reduced team morale'."
            ),
            "clarity": (
                "Structure your feedback as: '1. What happened, 2. How it affected the team, "
                "3. What I need going forward'."
            ),
            "specificity": (
                "Replace vague phrases like 'do better' with specific observable actions: "
                "'submit reports by the agreed Friday 5pm deadline'."
            ),
        }
        
        for keyword, example in examples.items():
            if keyword in name_lower:
                return example
        
        return None
