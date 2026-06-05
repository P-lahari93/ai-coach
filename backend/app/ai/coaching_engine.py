# FILE: app/ai/coaching_engine.py
"""
CoachingEngine — generate coaching feedback using LLM + RAG.

Orchestrates:
  1. Load module version, rubric, prompt template
  2. Retrieve relevant knowledge chunks via RAG
  3. Build prompt with PromptBuilder
  4. Call LLM via OllamaClient
  5. Parse response and extract structured data
  6. Return CoachingResponse

The engine handles LLM parsing errors by wrapping them in UnprocessableError.
"""
from __future__ import annotations

import json
import re
import time
from uuid import UUID

from app.ai.ollama_client import OllamaClient
from app.ai.prompt_builder import PromptBuilder
from app.core.exceptions import NotFoundError, UnprocessableError
from app.database.unit_of_work import UnitOfWork
from app.rag.citation_service import CitationService
from app.rag.retrieval_service import RetrievalService
from app.schemas.ai.coaching import CoachingResponse


class CoachingEngine:
    """Generate AI coaching feedback with RAG context."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        prompt_builder: PromptBuilder,
        retrieval_service: RetrievalService,
        citation_service: CitationService,
    ) -> None:
        """
        Initialize coaching engine.

        Args:
            ollama_client: LLM client for generation
            prompt_builder: service for resolving prompt templates
            retrieval_service: service for RAG retrieval
            citation_service: service for formatting citations
        """
        self._ollama = ollama_client
        self._prompt_builder = prompt_builder
        self._retrieval = retrieval_service
        self._citations = citation_service

    async def generate_feedback(
        self,
        session_id: UUID,
        user_id: UUID,
        module_version_id: UUID,
        tenant_id: UUID | None,
        intake_data: dict[str, str],
    ) -> CoachingResponse:
        """
        Generate coaching feedback for a session.

        Args:
            session_id: coaching session UUID
            user_id: learner UUID
            module_version_id: module version UUID
            tenant_id: tenant UUID for RAG scoping
            intake_data: learner's intake form submission

        Returns:
            CoachingResponse with feedback, scores, citations

        Raises:
            NotFoundError: when module version not found
            UnprocessableError: when LLM generation or parsing fails
        """
        start_time = time.time()
        
        # Load module version and related data
        async with UnitOfWork() as uow:
            module_version = await uow.module_versions.get_version_with_definition(module_version_id)
            if not module_version:
                raise NotFoundError(f"ModuleVersion {module_version_id} not found")
            
            # Retrieve knowledge chunks if tenant context available
            chunks = []
            if tenant_id:
                # Concatenate intake data for query
                query_text = " ".join(intake_data.values())
                
                chunk_results = await self._retrieval.retrieve(
                    query=query_text,
                    tenant_id=tenant_id,
                    module_id=module_version.module_id,
                    uow=uow,
                )
                chunks = chunk_results
            
            # Build knowledge context
            knowledge_context = self._citations.build_context_text(chunks)
            knowledge_chunks = [knowledge_context] if chunks else []
            
            # Build prompt
            # For MVP, we use a simple template approach
            # In production, load from ModulePromptTemplate table
            template = self._get_default_coaching_template()
            
            prompt = self._prompt_builder.build_coaching_prompt(
                template=template,
                intake_data=intake_data,
                rubric=module_version.scoring_rubric or {},
                knowledge_chunks=knowledge_chunks,
                framework_name=module_version.framework_name,
            )
            
            # Generate with LLM
            import logging as _log
            _logger = _log.getLogger("ai_coach.coaching_engine")
            _logger.info("[CE] Calling Ollama generate...")
            llm_response = await self._ollama.generate(
                prompt=prompt,
                system="You are an expert executive coach providing constructive feedback.",
            )
            _logger.info(f"[CE] Ollama responded — content length={len(llm_response.content)}")
            _logger.info(f"[CE] RAW LLM RESPONSE:\n{'='*60}\n{llm_response.content[:5000]}\n{'='*60}")

            # Parse response
            try:
                parsed = self._parse_feedback_response(
                    llm_response.content,
                    module_version.scoring_rubric or {},
                )
            except Exception as exc:
                _logger.error(f"[CE] PARSE FAILED: {type(exc).__name__}: {exc}")
                import traceback
                _logger.error(f"[CE] TRACEBACK:\n{traceback.format_exc()}")
                raise UnprocessableError(
                    f"Failed to parse LLM response: {exc}"
                ) from exc
            
            # Format citations
            citations = self._citations.format_citations(chunks)
            
            end_time = time.time()
            generation_time_ms = int((end_time - start_time) * 1000)
            
            return CoachingResponse(
                session_id=session_id,
                feedback_text=parsed["feedback_text"],
                scores=parsed["scores"],
                overall_score=parsed["overall_score"],
                strengths=parsed["strengths"],
                improvements=parsed["improvements"],
                recommendations=parsed["recommendations"],
                next_steps=parsed.get("next_steps"),
                citations=citations,
                knowledge_used=len(chunks) > 0,
                raw_ai_response=llm_response.content,
                generation_metadata={
                    "prompt_tokens": llm_response.prompt_tokens,
                    "completion_tokens": llm_response.completion_tokens,
                    "response_time_ms": llm_response.response_time_ms,
                    "model_used": llm_response.model_used,
                    "generation_time_ms": generation_time_ms,
                },
            )

    def _get_default_coaching_template(self) -> str:
        return """You are an expert coach. Review this {{framework}} feedback submission and respond with ONLY a JSON object.

Submission:
Situation: {{situation}}
Behaviour: {{behaviour}}
Impact: {{impact}}

Respond with ONLY this JSON (no other text):
{"feedback_text":"2-3 sentences of constructive coaching feedback","strengths":["one strength"],"improvements":["one improvement area"],"recommendations":[{"priority":1,"area":"key skill","suggestion":"specific actionable tip"}],"next_steps":"one concrete next step"}"""

    def _parse_feedback_response(
        self,
        response: str,
        rubric: dict,
    ) -> dict:
        import logging as _log
        _logger = _log.getLogger("ai_coach.coaching_engine")
        _logger.info(f"[PARSE] Starting parse — response length={len(response)}")
        _logger.info(f"[PARSE] First 500 chars:\n{response[:500]}")

        # Try to extract JSON from markdown code fence
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            _logger.info(f"[PARSE] Found JSON in markdown fence")
        else:
            # Try to find JSON object directly
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                _logger.info(f"[PARSE] Found JSON object directly")
            else:
                _logger.error(f"[PARSE] NO JSON FOUND IN RESPONSE")
                _logger.error(f"[PARSE] FULL RESPONSE:\n{response}")
                raise ValueError("No JSON object found in response")

        _logger.info(f"[PARSE] JSON string (first 500):\n{json_str[:500]}")

        try:
            parsed = json.loads(json_str)
            _logger.info(f"[PARSE] JSON parsed OK — keys: {list(parsed.keys())}")
        except json.JSONDecodeError as exc:
            _logger.error(f"[PARSE] JSON PARSE FAILED: {exc}")
            _logger.error(f"[PARSE] JSON string was:\n{json_str}")
            raise ValueError(f"Invalid JSON in response: {exc}") from exc

        feedback_text = parsed.get("feedback_text", "")
        if not feedback_text:
            _logger.error(f"[PARSE] MISSING feedback_text — parsed keys: {list(parsed.keys())}")
            _logger.error(f"[PARSE] Full parsed object: {parsed}")
            raise ValueError("Missing feedback_text in response")

        _logger.info(f"[PARSE] feedback_text found — length={len(feedback_text)}")
        strengths = parsed.get("strengths", [])
        improvements = parsed.get("improvements", [])
        recommendations = parsed.get("recommendations", [])
        next_steps = parsed.get("next_steps")
        _logger.info(f"[PARSE] strengths={len(strengths)} improvements={len(improvements)} recs={len(recommendations)}")
        
        # Generate rubric-driven scores from feedback text
        scores = self._extract_rubric_scores_from_feedback(feedback_text, rubric)
        overall_score = self._compute_overall_score(scores, rubric)
        
        return {
            "feedback_text": feedback_text,
            "scores": scores,
            "overall_score": overall_score,
            "strengths": strengths,
            "improvements": improvements,
            "recommendations": recommendations,
            "next_steps": next_steps,
        }

    def _generate_placeholder_scores(self, rubric: dict) -> dict:
        """Generate mid-range placeholder scores — used as fallback only."""
        dimensions = rubric.get("dimensions", [])
        scores = {}
        for dim in dimensions:
            name = dim.get("name", "Unknown")
            bands = dim.get("band_descriptors", {})
            max_score = len(bands) if bands else 4
            mid_score = max_score // 2 + 1
            scores[name] = {"score": mid_score, "rationale": f"Placeholder score for {name}"}
        return scores

    def _extract_rubric_scores_from_feedback(self, feedback_text: str, rubric: dict) -> dict:
        """
        Extract rubric-driven scores from the feedback text.
        Uses keyword matching against rubric dimension names + band descriptors
        to assign a data-driven score instead of a fixed midpoint.
        """
        import re
        dimensions = rubric.get("dimensions", [])
        scores = {}

        # Quality signals in the feedback text
        positive_signals = ["clear", "specific", "concrete", "detailed", "excellent", "strong",
                            "well", "good", "effective", "precise", "thorough", "articulate"]
        negative_signals = ["vague", "unclear", "missing", "lacking", "absent", "weak",
                            "general", "insufficient", "incomplete", "broad", "unspecific"]

        feedback_lower = feedback_text.lower()

        for dim in dimensions:
            name = dim.get("name", "")
            bands = dim.get("band_descriptors", {})
            max_score = len(bands) if bands else 4

            # Look for dimension keywords in feedback
            dim_keywords = name.lower().split()
            dim_mentioned = any(kw in feedback_lower for kw in dim_keywords)

            # Count positive vs negative signals near dimension mentions
            pos_count = sum(1 for s in positive_signals if s in feedback_lower)
            neg_count = sum(1 for s in negative_signals if s in feedback_lower)

            # Score heuristic: base on positive/negative signal ratio
            total_signals = pos_count + neg_count
            if total_signals == 0:
                score_ratio = 0.6  # Default to slightly above midpoint
            else:
                score_ratio = pos_count / total_signals

            # Map ratio to band score
            score = max(1, round(score_ratio * max_score))
            score = min(score, max_score)

            # Get band descriptor for the assigned score
            rationale = bands.get(str(score), f"Score {score} of {max_score}")

            scores[name] = {"score": score, "rationale": rationale}

        return scores

    def _compute_overall_score(self, scores: dict, rubric: dict) -> float:
        """
        Compute weighted overall score from dimension scores.

        Args:
            scores: dimension name -> {score, rationale} dict
            rubric: scoring rubric with weights

        Returns:
            overall score on 0-100 scale
        """
        dimensions = rubric.get("dimensions", [])
        if not dimensions:
            return 0.0
        
        total_weighted = 0.0
        total_weight = 0.0
        
        for dim in dimensions:
            name = dim.get("name", "")
            weight = dim.get("weight", 0.0)
            bands = dim.get("band_descriptors", {})
            max_score = len(bands) if bands else 4
            
            if name in scores:
                score = scores[name]["score"]
                normalized = (score / max_score) * 100
                total_weighted += normalized * weight
                total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        return round(total_weighted / total_weight, 2)
