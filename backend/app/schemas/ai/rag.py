"""
RAG (Retrieval-Augmented Generation) schemas.

Covers:
  - RetrievalRequest      — request to retrieve relevant knowledge chunks
  - RetrievalResult       — single retrieved chunk with similarity score
  - RetrievalResponse     — full retrieval result with ranked chunks
  - CitationResponse      — formatted citation for learner-facing display
  - ChunkReference        — lightweight chunk metadata reference
  - KnowledgeContext      — aggregated knowledge context for prompt injection

These schemas are used by the RAG pipeline to query the pgvector knowledge
base and construct the knowledge context injected into LLM prompts.

RAG pipeline flow:
  1. Service constructs RetrievalRequest with query text + filters
  2. RAG service generates query embedding (BAAI/bge-small-en-v1.5)
  3. RAG service runs HNSW similarity search on knowledge_chunks table
  4. RAG service filters by similarity_threshold (default 0.65)
  5. RAG service ranks results by module-specific KB weights
  6. RAG service constructs RetrievalResponse with ranked chunks
  7. AI engine extracts chunk texts → KnowledgeContext
  8. Prompt builder injects KnowledgeContext into {{knowledge}} slot
  9. After generation, service maps chunks → CitationResponse (public)

Similarity threshold (PRD Addendum B.5):
  0.65 cosine similarity minimum — chunks below this are DROPPED to
  maintain quality. "No knowledge is better than wrong knowledge."

Module-specific KB weighting (PRD Addendum B.2):
  ModuleKnowledgeBase.weight scores chunks from module-attached KBs
  higher than tenant-wide KBs. Example:
    Module KB weight = 1.5
    Tenant KB weight = 1.0
  A chunk with cosine similarity 0.70 from module KB gets boosted to
  0.70 * 1.5 = 1.05 (clamped to 1.0) for ranking.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── RetrievalRequest ──────────────────────────────────────────────────────────

class RetrievalRequest(BaseModel):
    """
    Request to retrieve relevant knowledge chunks for a given query.

    query_text: the text to embed and search against. Typically this is
    the learner's intake data concatenated, or a focused question extracted
    by the prompt builder (e.g. "How do I deliver feedback to a senior peer?").

    tenant_id: required for multi-tenant isolation. Retrieval query pre-filters
    by tenant_id before the HNSW scan to keep the search set small.

    module_id: optional; if provided the RAG service resolves module-attached
    KBs via ModuleKnowledgeBase and applies KB-specific weights during ranking.

    kb_ids: optional explicit list of KB UUIDs to search. If None, the RAG
    service auto-resolves: tenant-wide KBs + module-attached KBs (if module_id set).

    top_k: number of chunks to return. Default 10 for coaching, 5 for roleplay
    (roleplay prompts are shorter to preserve conversational flow).

    similarity_threshold: minimum cosine similarity to include a chunk in results.
    Default 0.65 per PRD quality controls. Chunks below this are DROPPED.

    rerank: if True, apply a secondary cross-encoder reranking pass after
    the initial HNSW retrieval. This improves precision at the cost of ~50ms
    extra latency. Default False for MVP (v1.1 scope).
    """

    query_text: str = Field(
        ...,
        min_length=1,
        description="Text to embed and search for relevant knowledge chunks",
    )
    tenant_id: UUID = Field(
        ...,
        description="Required for multi-tenant isolation",
    )
    module_id: UUID | None = Field(
        default=None,
        description=(
            "Optional; if set, resolves module-attached KBs and applies "
            "KB-specific ranking weights."
        ),
    )
    kb_ids: list[UUID] | None = Field(
        default=None,
        description=(
            "Optional explicit list of KB UUIDs to search. "
            "If None, auto-resolved from tenant + module scope."
        ),
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of chunks to return; capped at 50",
    )
    similarity_threshold: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum cosine similarity to include a chunk. "
            "Default 0.65 per PRD quality controls."
        ),
    )
    rerank: bool = Field(
        default=False,
        description=(
            "If True, apply cross-encoder reranking after HNSW retrieval. "
            "Improves precision at cost of ~50ms latency (v1.1 scope)."
        ),
    )


# ── RetrievalResult ───────────────────────────────────────────────────────────

class RetrievalResult(BaseModel):
    """
    A single retrieved knowledge chunk with similarity score.

    chunk_id: UUID of the KnowledgeChunk row.
    kb_id: UUID of the parent KnowledgeBase.
    source_id: UUID of the parent KnowledgeSource.
    content: the raw chunk text.
    similarity: cosine similarity score from HNSW query, 0.0–1.0.
    weighted_score: similarity * kb_weight (from ModuleKnowledgeBase.weight).
    Clamped to 1.0. Used for final ranking; original similarity preserved
    for citation display.

    metadata: chunk metadata from KnowledgeChunk.metadata_ JSONB.
    Keys: title, source_url, page_number, section, char_start, char_end.

    Ordered by weighted_score descending in RetrievalResponse.results.
    """

    chunk_id: UUID
    kb_id: UUID
    source_id: UUID
    content: str = Field(
        ...,
        description="Raw chunk text retrieved from the knowledge base",
    )
    similarity: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Cosine similarity score from HNSW query",
    )
    weighted_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "similarity * kb_weight (from ModuleKnowledgeBase); "
            "used for ranking, clamped to 1.0."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Chunk metadata: title, source_url, page_number, section, "
            "char_start, char_end."
        ),
    )


# ── RetrievalResponse ─────────────────────────────────────────────────────────

class RetrievalResponse(BaseModel):
    """
    Full retrieval result with ranked knowledge chunks.

    Returned by the RAG service after running the HNSW similarity search
    and optional reranking pass.

    results: list of RetrievalResult objects ordered by weighted_score
    descending. Length <= top_k from the request; may be shorter if fewer
    chunks passed the similarity_threshold filter.

    total_chunks_searched: count of chunks in the HNSW search set after
    pre-filtering by tenant_id + kb_ids. Useful for debugging recall issues
    ("I searched 1.2M chunks and only found 3 matches").

    kb_ids_searched: list of KB UUIDs actually searched. Matches RetrievalRequest.kb_ids
    if explicitly provided, otherwise shows the auto-resolved set (tenant + module KBs).

    query_embedding: optional 384-dim query vector. Only included when
    debug=True in the request (not shown in this schema). Used for debugging
    embedding drift and quality issues.

    retrieval_time_ms: wall-clock time from query start to result return.
    Tracks HNSW scan latency + reranking overhead (if enabled).
    """

    results: list[RetrievalResult] = Field(
        default_factory=list,
        description="Ranked knowledge chunks ordered by weighted_score descending",
    )
    total_chunks_searched: int = Field(
        ...,
        ge=0,
        description="Count of chunks in HNSW search set after pre-filtering",
    )
    kb_ids_searched: list[UUID] = Field(
        default_factory=list,
        description="List of KB UUIDs searched (auto-resolved or explicit)",
    )
    retrieval_time_ms: int = Field(
        ...,
        ge=0,
        description="Wall-clock retrieval time in milliseconds",
    )


# ── CitationResponse ──────────────────────────────────────────────────────────

class CitationResponse(BaseModel):
    """
    Formatted citation for learner-facing display.

    Mapped from RetrievalResult by the service layer after feedback
    generation. Stored in FeedbackReport.citations JSONB and returned
    in FeedbackReportResponse (public schema).

    source_title: human-readable title from KnowledgeChunk.metadata_['title'].
    kb_id, source_id, chunk_id: UUIDs for deep-linking and audit trail.
    snippet: extracted excerpt from the chunk content — typically the first
    150 chars or a sentence containing the most relevant keyword match.

    relevance: original cosine similarity from HNSW query (not the weighted_score).
    Shown to the learner as a 0–100% relevance indicator ("87% match").

    source_url: optional URL from metadata. If present, the UI renders the
    citation as a clickable link to the original source document.
    """

    source_title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable title of the knowledge source",
    )
    kb_id: UUID = Field(
        ...,
        description="UUID of the KnowledgeBase containing this chunk",
    )
    source_id: UUID = Field(
        ...,
        description="UUID of the KnowledgeSource document",
    )
    chunk_id: UUID = Field(
        ...,
        description="UUID of the specific KnowledgeChunk",
    )
    snippet: str = Field(
        ...,
        min_length=1,
        description=(
            "Extracted excerpt from chunk content (~150 chars or "
            "sentence with keyword match)."
        ),
    )
    relevance: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Original cosine similarity (not weighted_score); "
            "shown as relevance % to learner."
        ),
    )
    source_url: str | None = Field(
        default=None,
        description=(
            "Optional URL from chunk metadata; if present, "
            "citation renders as clickable link."
        ),
    )
    page_number: int | None = Field(
        default=None,
        ge=1,
        description="Optional page number for PDF/PPTX sources",
    )


# ── ChunkReference ────────────────────────────────────────────────────────────

class ChunkReference(BaseModel):
    """
    Lightweight chunk metadata reference.

    Used internally by the AI engine to track which chunks were used
    during prompt construction. Stored in ConversationMessage.metadata_
    and RoleplayMessage.metadata_ as the 'retrieval_ids' key.

    The full CitationResponse objects are only constructed for the
    FeedbackReport — conversation messages just carry these lightweight
    references for audit trail and debugging.
    """

    chunk_id: UUID
    kb_id: UUID
    similarity: float = Field(..., ge=0.0, le=1.0)


# ── KnowledgeContext ──────────────────────────────────────────────────────────

class KnowledgeContext(BaseModel):
    """
    Aggregated knowledge context for LLM prompt injection.

    Constructed by the AI engine from RetrievalResponse.results.
    Passed to the prompt builder which injects it into the {{knowledge}}
    slot in ModulePromptTemplate.template_body.

    chunks_text: concatenated chunk contents, separated by newlines and
    optional source attribution. Example:
        \"\"\"
        [Source: Manager Playbook 2024]
        When delivering feedback, always start with...

        [Source: Leadership Essentials]
        Situation-Behaviour-Impact (SBI) is a framework...
        \"\"\"

    chunk_references: list of ChunkReference objects for audit trail.
    Stored in message metadata for debugging and quality analysis.

    total_chunks: count of chunks included; matches len(chunk_references).
    Used by the prompt builder to decide whether to include the knowledge
    section at all (if total_chunks == 0, the {{knowledge}} slot is
    replaced with "No specific company knowledge was found for this topic.").
    """

    chunks_text: str = Field(
        ...,
        description=(
            "Concatenated chunk contents with source attribution, "
            "ready for LLM prompt injection."
        ),
    )
    chunk_references: list[ChunkReference] = Field(
        default_factory=list,
        description="Lightweight chunk metadata for audit trail",
    )
    total_chunks: int = Field(
        ...,
        ge=0,
        description="Count of chunks included in the context",
    )
    total_tokens_estimate: int = Field(
        ...,
        ge=0,
        description=(
            "Estimated token count for chunks_text (chars / 4 heuristic). "
            "Used by prompt builder to avoid exceeding LLM context window."
        ),
    )
