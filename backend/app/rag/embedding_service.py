# FILE: app/rag/embedding_service.py
"""
EmbeddingService — generate embeddings using sentence-transformers.

Model: BAAI/bge-small-en-v1.5 (384 dimensions)

This is a lightweight, CPU-friendly model suitable for RAG. The model
is loaded once at service initialization and cached in memory.

Concurrency note:
  SentenceTransformer.encode() is a synchronous, CPU-bound call. Both
  embed_query() and embed_batch() offload it via asyncio.to_thread() so
  it runs on a worker thread instead of blocking the event loop — every
  other concurrent request (coaching, roleplay, etc.) would otherwise
  stall for the full duration of each embedding call.

For production deployments with high throughput requirements, consider:
  - Running on GPU (CUDA)
  - Using a dedicated embedding service (FastEmbed, Infinity)
  - Batching requests from background workers
"""
from __future__ import annotations

import asyncio
from typing import List

from sentence_transformers import SentenceTransformer

from app.core.config import settings


class EmbeddingService:
    """Generate embeddings for text using sentence-transformers."""

    def __init__(self) -> None:
        """
        Initialize embedding service.

        Loads the sentence-transformer model into memory. This may take
        a few seconds on first initialization when the model needs to
        be downloaded from Hugging Face.

        The model is cached at ~/.cache/huggingface/hub/ after first download.
        """
        self._model_name = settings.EMBEDDING_MODEL
        self._dimension = settings.EMBEDDING_DIMENSION

        # Load model (cached after first load)
        self._model = SentenceTransformer(self._model_name)

        # Verify dimension matches configuration
        actual_dim = self._model.get_sentence_embedding_dimension()
        if actual_dim != self._dimension:
            raise ValueError(
                f"Model {self._model_name} has dimension {actual_dim}, "
                f"but settings specify {self._dimension}"
            )

    @property
    def dimension(self) -> int:
        """Return the embedding dimension (384 for bge-small-en-v1.5)."""
        return self._dimension

    def _encode_sync(self, texts, batch_size: int | None = None):
        """
        The actual blocking sentence-transformers call. Never call this
        directly from async code — always go through embed_query() or
        embed_batch(), which run this in a worker thread.
        """
        kwargs = {"normalize_embeddings": True, "show_progress_bar": False}
        if batch_size is not None:
            kwargs["batch_size"] = batch_size
        return self._model.encode(texts, **kwargs)

    async def embed_query(self, text: str) -> List[float]:
        """
        Generate embedding for a single query text.

        Args:
            text: query text to embed

        Returns:
            384-dimensional embedding vector as list of floats

        Raises:
            ValueError: when text is empty
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")

        # Offloaded to a worker thread — encode() is synchronous and
        # CPU-bound, so calling it directly here would block the event
        # loop and serialise every other concurrent request.
        embedding_array = await asyncio.to_thread(self._encode_sync, text)

        # Convert numpy array to Python list for JSON serialization
        return embedding_array.tolist()

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts in a batch.

        Batching is more efficient than individual calls for large volumes.
        The background embedding worker uses this method.

        Args:
            texts: list of texts to embed

        Returns:
            list of 384-dimensional embedding vectors

        Raises:
            ValueError: when texts list is empty or contains empty strings
        """
        if not texts:
            raise ValueError("Cannot embed empty batch")

        # Filter out empty strings
        valid_texts = [t for t in texts if t and t.strip()]
        if len(valid_texts) != len(texts):
            raise ValueError("Batch contains empty or whitespace-only texts")

        # Offloaded to a worker thread — same reasoning as embed_query().
        # This one is used by the background ingestion worker, so it
        # matters less for request-path latency, but it can still starve
        # the worker's own event loop if other async jobs run alongside it.
        embeddings_array = await asyncio.to_thread(
            self._encode_sync, valid_texts, settings.EMBEDDING_BATCH_SIZE
        )

        # Convert numpy arrays to Python lists
        return [emb.tolist() for emb in embeddings_array]