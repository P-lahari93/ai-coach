from __future__ import annotations
from typing import AsyncGenerator
from uuid import UUID

from app.database.unit_of_work import UnitOfWork


async def get_uow(tenant_id: UUID | None = None) -> AsyncGenerator[UnitOfWork, None]:
    """
    Default request-scoped UnitOfWork — tenant-scoped.

    Callers should pass tenant_id explicitly (e.g. via
    Depends(get_current_tenant_id) in the route signature itself,
    rather than wiring it in here) to avoid a circular import between
    this module and app.api.v1.dependencies.auth.
    """
    async with UnitOfWork(tenant_id=tenant_id) as uow:
        yield uow


async def get_system_uow() -> AsyncGenerator[UnitOfWork, None]:
    """
    Explicit superadmin/system UnitOfWork — for genuine platform-level
    endpoints only (superadmin tenant management, cross-tenant analytics).
    Do NOT wire this into ordinary user-facing routes.
    """
    async with UnitOfWork.system() as uow:
        yield uow