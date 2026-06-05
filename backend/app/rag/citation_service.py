# FILE: app/rag/citation_service.py
"""
CitationService — format retrieved chunks into citations.

Responsibilities:
  1. Map ChunkSearchResult → citation dict for FeedbackReport.citations
  2. Build concatenated context text with source attribution
  3. Extract snippets for citation display

Citations are stored in FeedbackReport.citations JSONB and used by
the frontend to render source references with relevance scores.
"""
from __future__ import annotations

from typing import Any

from app.repositories.knowledge.knowledge_chunk_repository import ChunkSearchResult


class CitationService:
    """Format knowledge chunks into citations and context text."""

    def __init__(self) -> None:
        """Initialize citation service."""
        self._snippet_length = 150  # characters for snippet preview

    def format_citations(
        self,
        chunks: list[ChunkSearchResult],
    ) -> list[dict[str, Any]]:
        """
        Format ChunkSearchResult list into citation dicts.

        Each citation dict matches the CitationResponse schema structure
        and can be directly stored in FeedbackReport.citations JSONB.

        Args:
            chunks: retrieved chunks from similarity search

        Returns:
            list of citation dicts with keys:
                - source_title
                - kb_id
                - source_id
                - chunk_id
                - snippet
                - relevance (0.0-1.0)
                - source_url (optional)
                - page_number (optional)
        """
        citations: list[dict[str, Any]] = []
        
        for result in chunks:
            chunk = result.chunk
            metadata = chunk.metadata_ or {}
            
            # Extract snippet (first N chars or first sentence)
            snippet = self._extract_snippet(chunk.content)
            
            citation = {
                "source_title": metadata.get("title", "Untitled"),
                "kb_id": str(chunk.kb_id),
                "source_id": str(chunk.source_id),
                "chunk_id": str(chunk.id),
                "snippet": snippet,
                "relevance": round(result.similarity, 3),
                "source_url": metadata.get("source_url"),
                "page_number": metadata.get("page_number"),
            }
            
            citations.append(citation)
        
        return citations

    def build_context_text(
        self,
        chunks: list[ChunkSearchResult],
    ) -> str:
        """
        Build concatenated context text with source attribution.

        Format:
            [Source: Title 1]
            chunk content...

            [Source: Title 2]
            chunk content...

        This text is injected into the LLM prompt's {{knowledge}} slot.

        Args:
            chunks: retrieved chunks from similarity search

        Returns:
            formatted context text ready for prompt injection
        """
        if not chunks:
            return "No relevant knowledge found for this query."
        
        context_parts: list[str] = []
        
        for result in chunks:
            chunk = result.chunk
            metadata = chunk.metadata_ or {}
            title = metadata.get("title", "Unknown Source")
            
            # Format: [Source: Title] followed by content
            context_parts.append(f"[Source: {title}]")
            context_parts.append(chunk.content.strip())
            context_parts.append("")  # blank line separator
        
        return "\n".join(context_parts).strip()

    def _extract_snippet(self, content: str) -> str:
        """
        Extract a preview snippet from chunk content.

        Tries to extract first complete sentence up to snippet_length.
        Falls back to first N characters if no sentence boundary found.

        Args:
            content: full chunk text

        Returns:
            snippet of max snippet_length characters
        """
        if len(content) <= self._snippet_length:
            return content
        
        # Try to find sentence boundary
        truncated = content[:self._snippet_length]
        
        # Look for sentence endings
        for delimiter in [". ", "! ", "? "]:
            pos = truncated.rfind(delimiter)
            if pos > 50:  # ensure snippet has meaningful length
                return truncated[:pos + 1].strip()
        
        # Fallback: cut at word boundary
        space_pos = truncated.rfind(" ")
        if space_pos > 50:
            return truncated[:space_pos].strip() + "..."
        
        return truncated.strip() + "..."
