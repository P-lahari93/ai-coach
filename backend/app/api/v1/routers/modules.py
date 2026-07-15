# FILE: backend/app/api/v1/routers/modules.py
from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from app.schemas.common import MessageResponse
from app.services.module.module_service import CoachingModuleService
from app.api.v1.dependencies.auth import get_current_active_user, get_current_tenant_id
from app.models.user import User

router = APIRouter()
_svc = CoachingModuleService()


class ModuleCreateRequest(BaseModel):
    key: str
    name: str
    blurb: str | None = None
    icon: str | None = None


class ModuleUpdateRequest(BaseModel):
    name: str | None = None
    blurb: str | None = None
    icon: str | None = None


@router.get("/")
async def list_modules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    module_status: str | None = Query(None, alias="status"),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
    current_user: User = Depends(get_current_active_user),
):
    """List coaching modules available within the tenant scope."""
    result = await _svc.list_modules(
        tenant_id=tenant_id, status=module_status, page=page, page_size=page_size
    )
    return {
        "items": [
            {"id": str(m.id), "key": m.key, "name": m.name, "status": m.status}
            for m in result.items
        ],
        "total": result.total,
    }


@router.get("/{module_id}")
async def get_module(
    module_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Get a coaching module by ID with tenant isolation check."""
    m = await _svc.get_module(module_id, tenant_id=tenant_id)
    return {
        "id": str(m.id),
        "key": m.key,
        "name": m.name,
        "status": m.status,
        "blurb": m.blurb,
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_module(
    body: ModuleCreateRequest,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Create a new coaching module explicitly bound to the current tenant."""
    m = await _svc.create_module(
        key=body.key,
        name=body.name,
        tenant_id=tenant_id,
        created_by=current_user.id,
        blurb=body.blurb,
        icon=body.icon,
    )
    return {"id": str(m.id), "key": m.key, "name": m.name, "status": m.status}


@router.patch("/{module_id}")
async def update_module(
    module_id: UUID,
    body: ModuleUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Update a coaching module securely within tenant boundaries."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    m = await _svc.update_module(module_id, tenant_id=tenant_id, **updates)
    return {"id": str(m.id), "key": m.key, "name": m.name, "status": m.status}


@router.post("/{module_id}/publish")
async def publish_module(
    module_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Publish a module to make it available to learners, verified by tenant context."""
    m = await _svc.publish_module(
        module_id, published_by=current_user.id, tenant_id=tenant_id
    )
    return {"id": str(m.id), "key": m.key, "status": m.status}


@router.post("/{module_id}/archive")
async def archive_module(
    module_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Archive a module securely matching the current tenant context."""
    m = await _svc.archive_module(module_id, tenant_id=tenant_id)
    return {"id": str(m.id), "key": m.key, "status": m.status}


@router.delete("/{module_id}", response_model=MessageResponse)
async def delete_module(
    module_id: UUID,
    current_user: User = Depends(get_current_active_user),
    tenant_id: UUID | None = Depends(get_current_tenant_id),
):
    """Soft-delete a coaching module safely walled off by tenancy."""
    await _svc.delete_module(module_id, tenant_id=tenant_id)
    return MessageResponse(message="Module deleted")