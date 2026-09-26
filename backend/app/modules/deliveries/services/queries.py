"""Reading deliveries: lists scoped to what the caller may see."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.pagination import PageParams, apply_sort
from app.modules.deliveries.models import Delivery

PERM_VIEW = "deliveries.view"


def repository(session: AsyncSession) -> ScopedRepository[Delivery]:
    return ScopedRepository(
        session,
        Delivery,
        entity_name="Delivery",
        sortable={
            "delivery_number",
            "captured_at",
            "received_at",
            "status",
            "flag_count",
            "created_at",
            "updated_at",
        },
        searchable=("delivery_number", "truck_number", "challan_number"),
        default_sort="-captured_at",
    )


async def list_deliveries(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
    captured_from: datetime | None = None,
    captured_before: datetime | None = None,
    only_open_flags: bool = False,
) -> tuple[list[Delivery], int]:
    repo = repository(session)
    stmt = repo.base_query(ctx, PERM_VIEW)
    stmt = repo.apply_filters(stmt, filters)
    stmt = repo.apply_search(stmt, search)
    # Instants, not dates: the caller works out "the whole of 25 Sept" in the
    # company's timezone, so a load captured at 1 a.m. lands on the right day.
    if captured_from is not None:
        stmt = stmt.where(Delivery.captured_at >= captured_from)
    if captured_before is not None:
        stmt = stmt.where(Delivery.captured_at < captured_before)
    if only_open_flags:
        stmt = stmt.where(Delivery.has_open_flags.is_(True))
    total = (
        await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ) or 0
    stmt = apply_sort(stmt, Delivery, page.sort, allowed=repo.sortable, default=repo.default_sort)
    rows = (
        (await session.execute(stmt.limit(page.limit).offset(page.offset))).scalars().unique().all()
    )
    return list(rows), total


async def get(session: AsyncSession, ctx: AccessContext, delivery_id: UUID) -> Delivery:
    return await repository(session).get(ctx, PERM_VIEW, delivery_id)
