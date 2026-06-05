"""
Knowledge base schemas.

KnowledgeBase has two scopes enforced by a DB CHECK constraint:
  scope='tenant'  — available to all modules within this tenant;
                    module_id must be NULL.
  scope='module'  — attached to a single module for domain-specific
                    knowledge; module_id must be set.

The scope+module_id consistency rule is validated here in Pydantic
as well as at the DB layer so API errors are returned before hitting
the database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.knowledge.knowledge_source import KnowledgeSourceSummary

KBScope = Literal["tenant", "module"]


# ── Base ──────────────────────────────────────────────────────────────────────

class KnowledgeBaseBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    scope: KBScope = Field(..., description="tenant | module")


# ── Request schemas ───────────────────────────────────────────────────────────

class KnowledgeBaseCreate(KnowledgeBaseBase):
    """
    POST /knowledge-bases

    scope='tenant'  → module_id must be omitted / None
    scope='module'  → module_id is required
    """

    tenant_id: UUID
    module_id: UUID | None = Field(
        default=None,
        description="Required when scope='module'; must be None when scope='tenant'",
    )

    @model_validator(mode="after")
    def validate_scope_module_consistency(self) -> "KnowledgeBaseCreate":
        if self.scope == "module" and self.module_id is None:
            raise ValueError("module_id is required when scope='module'")
        if self.scope == "tenant" and self.module_id is not None:
            raise ValueError("module_id must be None when scope='tenant'")
        return self


class KnowledgeBaseUpdate(BaseModel):
    """
    PATCH /knowledge-bases/{kb_id}

    Only name and description are updatable after creation.
    Scope and module_id are immutable — create a new KB to change them.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


# ── Response schemas ──────────────────────────────────────────────────────────

class KnowledgeBaseResponse(KnowledgeBaseBase):
    """
    Standard KB response — no source list.
    Used for list views and lightweight lookups.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    module_id: UUID | None
    chunk_count: int
    created_by: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class KnowledgeBaseSummary(BaseModel):
    """
    Minimal KB reference embedded in module or session responses.
    Omits timestamps and soft-delete state.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    scope: KBScope
    chunk_count: int


class KnowledgeBaseDetail(KnowledgeBaseResponse):
    """
    Full KB response including source list.
    Used by the KB management UI and admin detail views.
    Sources are the non-deleted active sources only.
    """

    sources: list[KnowledgeSourceSummary] = Field(default_factory=list)
