"""
Application configuration via environment variables.
Uses pydantic-settings for validation and type coercion.
All secrets come from environment — never hardcoded.

Production hardening:
  When ENVIRONMENT=production, a model validator hard-fails startup if:
    - SECRET_KEY is under 64 characters, or matches a known placeholder
    - DEBUG is True
    - ALLOWED_ORIGINS is empty, contains "*", or contains a localhost/
      127.0.0.1 entry
  This is deliberate: a misconfigured production deploy should refuse to
  start with a clear error, rather than boot insecurely.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Known placeholder/example SECRET_KEY values that must never reach
# production — pulled from README.md, .env.example, and common defaults
# seen across this codebase's history.
_KNOWN_WEAK_SECRET_KEYS = {
    "change-me-to-a-long-random-secret-at-least-32-chars",
    "your-random-64-char-string-here",
    "secret",
    "changeme",
    "change-me",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    APP_NAME: str = "AI Coach"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # ── Security ─────────────────────────────────────────────────────────────
    # min_length=32 is the absolute floor for ANY environment. Production
    # additionally requires 64+ via the model validator below — the
    # README promises 64 in production; this now actually enforces it.
    SECRET_KEY: str = Field(..., min_length=32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        ...,
        description="Async PostgreSQL DSN. Use asyncpg driver: postgresql+asyncpg://...",
    )
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Safe default for local dev. Must be explicitly overridden in
    # production — enforced below, since allow_credentials=True is set
    # alongside this in main.py's CORSMiddleware, and leaving a wildcard
    # or unreviewed origin list live in prod is a real exposure.
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── AI ───────────────────────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:4b"
    OLLAMA_TIMEOUT: int = 120
    OLLAMA_MAX_TOKENS: int = 2048
    OLLAMA_TEMPERATURE: float = 0.7

    # ── Embeddings ───────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSION: int = 384
    EMBEDDING_BATCH_SIZE: int = 32

    # ── RAG ──────────────────────────────────────────────────────────────────
    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 64
    RAG_TOP_K: int = 6
    RAG_SCORE_THRESHOLD: float = 0.35
    RAG_TOKEN_BUDGET: int = 2048

    # ── File Storage ─────────────────────────────────────────────────────────
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_UPLOAD_EXTENSIONS: list[str] = [
        ".pdf", ".docx", ".pptx", ".txt", ".md"
    ]

    # ── Pagination ───────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    # ── Rate Limiting ────────────────────────────────────────────────────────
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL DSN")
        # Ensure async driver is used
        if "asyncpg" not in v and "postgresql://" in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://")
        return v

    @model_validator(mode="after")
    def _enforce_production_hardening(self) -> "Settings":
        """
        Hard-fail startup on a misconfigured production deploy, rather
        than booting insecurely. Only applies when ENVIRONMENT=production
        — development/staging are unaffected.
        """
        if self.ENVIRONMENT != "production":
            return self

        errors: list[str] = []

        if len(self.SECRET_KEY) < 64:
            errors.append(
                f"SECRET_KEY must be at least 64 characters in production "
                f"(got {len(self.SECRET_KEY)}). Generate one with: "
                f"python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        if self.SECRET_KEY.lower() in _KNOWN_WEAK_SECRET_KEYS:
            errors.append(
                "SECRET_KEY matches a known placeholder/example value. "
                "Generate a real random secret before deploying to production."
            )

        if self.DEBUG:
            errors.append(
                "DEBUG must be False in production — it enables verbose "
                "error output and can leak internals/PII in logs."
            )

        if not self.ALLOWED_ORIGINS:
            errors.append(
                "ALLOWED_ORIGINS must be explicitly set in production "
                "(no default origins are assumed safe for a live deploy)."
            )
        else:
            for origin in self.ALLOWED_ORIGINS:
                lowered = origin.lower()
                if lowered == "*":
                    errors.append(
                        "ALLOWED_ORIGINS must not contain '*' in production "
                        "— this app sets allow_credentials=True, and a "
                        "wildcard origin with credentials is a real "
                        "cross-origin exposure (browsers reject the "
                        "combination outright, but the intent is still wrong)."
                    )
                if "localhost" in lowered or "127.0.0.1" in lowered:
                    errors.append(
                        f"ALLOWED_ORIGINS contains a localhost origin "
                        f"({origin!r}) in production — this is almost "
                        f"certainly a forgotten dev default."
                    )

        if errors:
            joined = "\n  - ".join(errors)
            raise ValueError(
                f"Refusing to start with ENVIRONMENT=production due to "
                f"{len(errors)} configuration issue(s):\n  - {joined}"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — reads .env once at startup."""
    return Settings()


settings = get_settings()