"""
AI engine schemas package.

Internal schemas used by the AI engine, RAG pipeline, and scoring engine.
These are NOT directly exposed in the public API — public-facing schemas
live in session/, progress/, and analytics/ packages.
"""
from app.schemas.ai.coaching import (
    CoachingFeedbackRequest,
    CoachingFeedbackResponse,
    CoachingRequest,
    CoachingResponse,
    ConversationAnalysisRequest,
    ConversationAnalysisResponse,
)
from app.schemas.ai.roleplay import (
    PersonaSimulationRequest,
    PersonaSimulationResponse,
    RoleplayGenerationRequest,
    RoleplayGenerationResponse,
)
from app.schemas.ai.scoring import (
    CoachingScoreResponse,
    RecommendationItem,
    ScoreBreakdown,
    ScoreDimension,
)
from app.schemas.ai.rag import (
    ChunkReference,
    CitationResponse,
    KnowledgeContext,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalResult,
)

__all__ = [
    # coaching
    "CoachingRequest",
    "CoachingResponse",
    "CoachingFeedbackRequest",
    "CoachingFeedbackResponse",
    "ConversationAnalysisRequest",
    "ConversationAnalysisResponse",
    # roleplay
    "RoleplayGenerationRequest",
    "RoleplayGenerationResponse",
    "PersonaSimulationRequest",
    "PersonaSimulationResponse",
    # scoring
    "ScoreDimension",
    "ScoreBreakdown",
    "CoachingScoreResponse",
    "RecommendationItem",
    # rag
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalResponse",
    "CitationResponse",
    "ChunkReference",
    "KnowledgeContext",
]
