# FILE: app/ai/ollama_client.py
"""
OllamaClient — wrapper for Ollama API using ollama Python SDK.

Provides both synchronous and streaming generation with token counting
and latency tracking.

Configuration:
  - OLLAMA_BASE_URL: Ollama server URL (default: http://localhost:11434)
  - OLLAMA_MODEL: default model name (default: qwen3:4b)
  - OLLAMA_TIMEOUT: request timeout in seconds (default: 120)
  - OLLAMA_TEMPERATURE: default temperature (default: 0.7)
  - OLLAMA_MAX_TOKENS: default max tokens (default: 2048)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import ollama

from app.core.config import settings
from app.core.exceptions import UnprocessableError


@dataclass(frozen=True, slots=True)
class OllamaResponse:
    """
    Response from Ollama generation.

    Attributes:
        content: generated text
        prompt_tokens: tokens in prompt
        completion_tokens: tokens in completion
        total_tokens: sum of prompt and completion tokens
        response_time_ms: wall-clock generation time in milliseconds
        model_used: model name used for generation
    """

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    response_time_ms: int
    model_used: str


class OllamaClient:
    """Async client for Ollama API."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        """
        Initialize Ollama client.

        Args:
            base_url: Ollama server URL (default from settings)
            model: default model name (default from settings)
            timeout: request timeout in seconds (default from settings)
        """
        self._base_url = base_url or settings.OLLAMA_BASE_URL
        self._model = model or settings.OLLAMA_MODEL
        self._timeout = timeout or settings.OLLAMA_TIMEOUT
        
        # Initialize async client with timeout
        self._client = ollama.AsyncClient(
            host=self._base_url,
            timeout=self._timeout,
        )

    async def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> OllamaResponse:
        """
        Generate completion for a prompt.

        Args:
            prompt: user prompt text
            temperature: sampling temperature (default from settings)
            max_tokens: max completion tokens (default from settings)
            system: optional system message

        Returns:
            OllamaResponse with generated content and metadata

        Raises:
            UnprocessableError: when generation fails
        """
        temperature = temperature if temperature is not None else settings.OLLAMA_TEMPERATURE
        max_tokens = max_tokens if max_tokens is not None else settings.OLLAMA_MAX_TOKENS
        
        try:
            start_time = time.time()
            
            response = await self._client.generate(
                model=self._model,
                prompt=prompt,
                system=system,
                options={
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            )
            
            end_time = time.time()
            response_time_ms = int((end_time - start_time) * 1000)

            # The ollama SDK returns a Pydantic model object, not a dict.
            # Use attribute access, not .get()
            import logging as _log
            _logger = _log.getLogger("ai_coach.ollama_client")
            _logger.info(f"[OLLAMA] Response type: {type(response).__name__}")
            _logger.info(f"[OLLAMA] Response attrs: {[a for a in dir(response) if not a.startswith('_')]}")

            # Extract content — try attribute first, then dict-style
            if hasattr(response, 'response'):
                content = response.response or ""
                _logger.info(f"[OLLAMA] Got content via attribute — length={len(content)}")
            elif hasattr(response, 'get'):
                content = response.get("response", "")
                _logger.info(f"[OLLAMA] Got content via .get() — length={len(content)}")
            else:
                _logger.error(f"[OLLAMA] Cannot extract response content — unknown SDK format")
                content = ""

            # Extract token counts — attribute or dict
            if hasattr(response, 'prompt_eval_count'):
                prompt_tokens = response.prompt_eval_count or 0
                completion_tokens = response.eval_count or 0
            elif hasattr(response, 'get'):
                prompt_tokens = response.get("prompt_eval_count", 0)
                completion_tokens = response.get("eval_count", 0)
            else:
                prompt_tokens = 0
                completion_tokens = 0

            total_tokens = prompt_tokens + completion_tokens

            if not content:
                _logger.error("[OLLAMA] Empty content from Ollama response")
                raise UnprocessableError("Ollama returned empty response")
            
            return OllamaResponse(
                content=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                response_time_ms=response_time_ms,
                model_used=self._model,
            )
            
        except ollama.ResponseError as exc:
            import logging as _l; _l.getLogger("ai_coach.ollama_client").error(f"[OLLAMA] ResponseError: {exc}")
            raise UnprocessableError(
                f"Ollama API error: {exc}"
            ) from exc
        except Exception as exc:
            import traceback as _tb2, logging as _l2
            _l2.getLogger("ai_coach.ollama_client").error(f"[OLLAMA] Exception {type(exc).__name__}: {exc}")
            _l2.getLogger("ai_coach.ollama_client").error(f"[OLLAMA] TB:\n{_tb2.format_exc()}")
            raise UnprocessableError(
                f"Failed to generate completion: {type(exc).__name__}: {exc}"
            ) from exc

    async def stream_generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream completion tokens for a prompt.

        Yields text chunks as they are generated.

        Args:
            prompt: user prompt text
            temperature: sampling temperature (default from settings)
            max_tokens: max completion tokens (default from settings)
            system: optional system message

        Yields:
            text chunks as they are generated

        Raises:
            UnprocessableError: when streaming fails
        """
        temperature = temperature if temperature is not None else settings.OLLAMA_TEMPERATURE
        max_tokens = max_tokens if max_tokens is not None else settings.OLLAMA_MAX_TOKENS
        
        try:
            stream = await self._client.generate(
                model=self._model,
                prompt=prompt,
                system=system,
                options={
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                stream=True,
            )
            
            async for chunk in stream:
                content = chunk.get("response", "")
                if content:
                    yield content
                    
        except ollama.ResponseError as exc:
            raise UnprocessableError(
                f"Ollama streaming error: {exc}"
            ) from exc
        except Exception as exc:
            raise UnprocessableError(
                f"Failed to stream completion: {exc}"
            ) from exc
