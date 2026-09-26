"""Vendors a phone caches (docs/06 §4): enough to pick one."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sync import FeedRow
from app.modules.vendors.models import Vendor


async def changes(
    session: AsyncSession, company_id: UUID, *, since: int, limit: int
) -> list[FeedRow]:
    rows = (
        (
            await session.execute(
                select(Vendor)
                .where(
                    Vendor.company_id == company_id,
                    Vendor.server_seq.is_not(None),
                    Vendor.server_seq > since,
                )
                .order_by(Vendor.server_seq)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        FeedRow(
            "vendors",
            v.id,
            int(v.server_seq or 0),
            deleted=v.deleted_at is not None,
            data={
                "code": v.code,
                "name": v.trade_name or v.legal_name,
                "status": v.status,
                "phone": v.phone,
            },
        )
        for v in rows
    ]
