from __future__ import annotations
from datetime import date
from uuid import UUID


async def rollup_daily_analytics(tenant_id: UUID | None, target_date: date) -> None:
    """Aggregate daily analytics into summary tables. MVP stub."""
    pass
