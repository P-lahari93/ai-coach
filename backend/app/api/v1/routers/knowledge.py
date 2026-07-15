# FILE: backend/app/api/v1/routers/knowledge.py
from __future__ import annotations

import os
import uuid
from uuid import UUID
from fastapi import APIRouter, Depends, Query, UploadFile, File, status
from pydantic import BaseModel

from app.schemas.common import MessageResponse
from app.services.knowledge.knowledge_service import KnowledgeBaseService, KnowledgeSourceService
from app.api.v1.dependencies.auth import get_current_active_user, get_current_tenant_id
from app.models.user import User
from app.core.config import settings
from app.core.exceptions import BadRequestError

router = APIRouter()
_kb_svc = KnowledgeBaseService()
_src_svc = KnowledgeSourceService()


class KBCreateRequest(BaseModel):
    name: str
    description: str | None = None
    scope: str = "tenant"


class TextSourceRequest(BaseModel):
    title: str
    content: str


@router.get("/")
async def list_knowledge_bases(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """List knowledge bases for the current tenant."""
    if tenant_id is None:
        return {"items": [], "total": 0}
    result = await _kb_svc.list_knowledge_bases(tenant_id=tenant_id, page=page, page_size=page_size)
    return {
        "items": [
            {"id": str(kb.id), "name": kb.name, "scope": kb.scope, "chunk_count": kb.chunk_count}
            for kb in result.items
        ],
        "total": result.total,
    }


@router.get("/{kb_id}")
async def get_knowledge_base(
    kb_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Get a knowledge base by ID."""
    kb = await _kb_svc.get_knowledge_base(kb_id, tenant_id=tenant_id)
    return {
        "id": str(kb.id),
        "name": kb.name,
        "scope": kb.scope,
        "description": kb.description,
        "chunk_count": kb.chunk_count,
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    body: KBCreateRequest,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Create a new knowledge base."""
    if tenant_id is None:
        raise BadRequestError("Tenant context required")
    kb = await _kb_svc.create_knowledge_base(
        name=body.name,
        tenant_id=tenant_id,
        scope=body.scope,
        description=body.description,
        created_by=current_user.id,
    )
    return {"id": str(kb.id), "name": kb.name, "scope": kb.scope}


@router.delete("/{kb_id}", response_model=MessageResponse)
async def delete_knowledge_base(
    kb_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Delete a knowledge base securely within tenant boundaries."""
    await _kb_svc.delete_knowledge_base(kb_id, tenant_id=tenant_id)
    return MessageResponse(message="Knowledge base deleted")


@router.get("/{kb_id}/sources")
async def list_sources(
    kb_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """List sources inside a knowledge base checking inherited scopes."""
    result = await _src_svc.list_sources(
        kb_id=kb_id, tenant_id=tenant_id, page=page, page_size=page_size
    )
    return {
        "items": [
            {
                "id": str(s.id),
                "title": s.title,
                "type": s.type,
                "status": s.status,
                "chunk_count": s.chunk_count,
            }
            for s in result.items
        ],
        "total": result.total,
    }


@router.post("/{kb_id}/sources/text", status_code=status.HTTP_201_CREATED)
async def ingest_text(
    kb_id: UUID,
    body: TextSourceRequest,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Ingest plain text into a knowledge base."""
    if tenant_id is None:
        raise BadRequestError("Tenant context required")
    source = await _src_svc.create_source_from_text(
        kb_id=kb_id,
        title=body.title,
        content=body.content,
        tenant_id=tenant_id,
        created_by=current_user.id,
    )
    return {
        "id": str(source.id),
        "title": source.title,
        "type": source.type,
        "status": source.status,
    }


@router.post("/{kb_id}/sources/upload", status_code=status.HTTP_201_CREATED)
async def upload_source(
    kb_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Upload a file to a knowledge base."""
    if tenant_id is None:
        raise BadRequestError("Tenant context required")

    ext = os.path.splitext(file.filename or "")[-1].lower()
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise BadRequestError(f"File type {ext} not allowed")

    contents = await file.read()
    if len(contents) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise BadRequestError(f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit")

    upload_dir = settings.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    file_name = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(upload_dir, file_name)
    with open(file_path, "wb") as f:
        f.write(contents)

    source = await _src_svc.create_source_from_file(
        kb_id=kb_id,
        title=file.filename or file_name,
        file_path=file_path,
        mime_type=file.content_type or "application/octet-stream",
        file_size=len(contents),
        tenant_id=tenant_id,
        created_by=current_user.id,
    )
    return {
        "id": str(source.id),
        "title": source.title,
        "type": source.type,
        "status": source.status,
    }


@router.delete("/{kb_id}/sources/{source_id}", response_model=MessageResponse)
async def delete_source(
    kb_id: UUID,
    source_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Delete a knowledge source checking hierarchy isolation rules."""
    await _src_svc.delete_source(source_id, tenant_id=tenant_id)
    return MessageResponse(message="Source deleted")