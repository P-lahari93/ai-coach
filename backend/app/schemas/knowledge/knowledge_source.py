"""Knowledge source schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal["paste", "upload", "url"]
SourceStatus = Literal["pending", "processing", "completed", "failed"]
CrawlFrequency = Literal["daily", "weekly", "monthly"]


class KnowledgeSourceCreate(BaseModel):
    """
    POST /knowledge-bases/{kb_id}/sources

    file_path is never accepted from clients — it is assigned by the
    ingestion service after a validated upload. Only the service layer
    writes file_path; API routes must never expose it.
    """

    kb_id: UUID
    type: SourceType = Field(..., description="paste | upload | url")
    title: str = Field(..., min_length=1, max_length=500)
    url: str | None = Field(
        default=None,
        description="Required when type='url'",
    )
    crawl_frequency: CrawlFrequency | None = Field(
        default=None,
        description="daily | weekly | monthly — only for type='url'",
    )

    @model_validator(mode="after")
    def validate_url_fields(self) -> "KnowledgeSourceCreate":
        if self.type == "url" and not self.url:
            raise ValueError("url is required when type='url'")
        if self.type != "url" and self.crawl_frequency is not None:
            raise ValueError("crawl_frequency is only valid for type='url'")
        if self.url and not self.url.startswith(("http://", "https://")):
            raise ValueError("url must be an absolute HTTP/HTTPS URL")
        return self


class KnowledgeSourceUpdate(BaseModel):
    """
    PATCH /knowledge-bases/{kb_id}/sources/{source_id}

    Only the title and crawl_frequency are updatable after creation.
    To change type or url, create a new source and delete the old one.
    """

    title: str | None = Field(default=None, min_length=1, max_length=500)
    crawl_frequency: CrawlFrequency | None = None


class KnowledgeSourceStatusUpdate(BaseModel):
    """
    Internal schema used by the ingestion worker to update processing state.
    Not exposed to external API clients directly.
    """

    status: SourceStatus
    chunk_count: int | None = Field(default=None, ge=0)
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_error_on_failed(self) -> "KnowledgeSourceStatusUpdate":
        if self.status == "failed" and not self.error_message:
            raise ValueError("error_message is required when status='failed'")
        if self.status == "completed" and self.chunk_count is None:
            raise ValueError("chunk_count is required when status='completed'")
        return self


class KnowledgeSourceSummary(BaseModel):
    """Minimal source reference embedded in KB responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    type: SourceType
    status: SourceStatus
    chunk_count: int


class KnowledgeSourceResponse(BaseModel):
    """
    Full source response.

    file_path is intentionally excluded — it is a server-side path
    that must never be returned to clients (SEC-03 mitigation).
    Use a presigned URL / download endpoint instead.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kb_id: UUID
    type: SourceType
    title: str
    url: str | None
    file_size_bytes: int | None
    mime_type: str | None
    status: SourceStatus
    chunk_count: int
    error_message: str | None
    last_crawled_at: datetime | None
    crawl_frequency: CrawlFrequency | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
