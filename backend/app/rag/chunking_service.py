# FILE: app/rag/chunking_service.py
"""
ChunkingService — split text into overlapping chunks.

Uses langchain's RecursiveCharacterTextSplitter for intelligent chunking
that respects paragraph and sentence boundaries.

Chunk metadata includes:
  - title: source document title
  - source_url: optional URL for citation linking
  - char_start: character offset in original text
  - char_end: character offset end position
  - chunk_index: 0-based chunk number
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore[no-redef]

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class ChunkData:
    """
    A single chunk of text with metadata.

    Attributes:
        content: the chunk text
        index: 0-based chunk number within the source document
        metadata: dict with title, source_url, char_start, char_end
    """

    content: str
    index: int
    metadata: dict[str, Any]


class ChunkingService:
    """Split text documents into overlapping chunks for RAG."""

    def __init__(
        self,
        chunk_size: int | None = None,
        overlap: int | None = None,
    ) -> None:
        """
        Initialize chunking service.

        Args:
            chunk_size: target chunk size in characters (default from settings)
            overlap: overlap size in characters (default from settings)
        """
        self._chunk_size = chunk_size or settings.RAG_CHUNK_SIZE
        self._overlap = overlap or settings.RAG_CHUNK_OVERLAP
        
        # Initialize langchain splitter with paragraph-aware separators
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._overlap,
            length_function=len,
            separators=[
                "\n\n",  # paragraph breaks (highest priority)
                "\n",    # line breaks
                ". ",    # sentence ends
                "! ",    # exclamation sentence ends
                "? ",    # question sentence ends
                "; ",    # semicolon breaks
                ", ",    # comma breaks
                " ",     # word breaks
                "",      # character breaks (fallback)
            ],
        )

    async def chunk_text(
        self,
        text: str,
        source_title: str,
        source_url: str | None = None,
    ) -> list[ChunkData]:
        """
        Split text into overlapping chunks with metadata.

        Args:
            text: cleaned text to chunk
            source_title: document title for metadata
            source_url: optional URL for citation linking

        Returns:
            list of ChunkData objects with content and metadata

        Raises:
            ValueError: when text is empty or too short to chunk
        """
        if not text or not text.strip():
            raise ValueError("Cannot chunk empty or whitespace-only text")
        
        if len(text) < 10:
            raise ValueError(f"Text too short to chunk: {len(text)} chars")
        
        # Split text using langchain
        chunks_text = self._splitter.split_text(text)
        
        if not chunks_text:
            raise ValueError("Text splitting produced no chunks")
        
        # Build ChunkData objects with metadata
        chunks: list[ChunkData] = []
        char_position = 0
        
        for idx, chunk_content in enumerate(chunks_text):
            # Find actual position in original text (accounting for overlap)
            if idx > 0:
                # Try to find chunk in text starting near expected position
                search_start = max(0, char_position - self._overlap)
                found_pos = text.find(chunk_content[:50], search_start)
                if found_pos >= 0:
                    char_position = found_pos
            
            char_start = char_position
            char_end = char_start + len(chunk_content)
            
            metadata = {
                "title": source_title,
                "source_url": source_url,
                "char_start": char_start,
                "char_end": char_end,
            }
            
            chunks.append(
                ChunkData(
                    content=chunk_content.strip(),
                    index=idx,
                    metadata=metadata,
                )
            )
            
            char_position = char_end
        
        return chunks
