"""Sites a phone may capture at (docs/06 §4): only those the caller is
granted, with the centre and radius the device pre-checks against."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.scoping import scope_filter
from app.core.sync import FeedRow
from app.modules.org.models import Site
from app.modules.org.services.geo import from_db_point

# A phone lists the sites its owner may record deliveries at, which is a narrower
# question than which sites they may view.
CAPTURE_PERMISSION = "deliveries.create"


async def changes(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    since: int,
    limit: int,
    site_id: UUID | None,
) -> list[FeedRow]:
    stmt = (
        scope_filter(select(Site), Site, ctx, CAPTURE_PERMISSION)
        .where(Site.server_seq.is_not(None), Site.server_seq > since)
        .order_by(Site.server_seq)
        .limit(limit)
    )
    if site_id is not None:
        stmt = stmt.where(Site.id == site_id)
    rows = (await session.execute(stmt)).scalars().unique().all()
    out = []
    for s in rows:
        centre = from_db_point(s.centroid)
        out.append(
            FeedRow(
                "sites",
                s.id,
                int(s.server_seq or 0),
                deleted=s.deleted_at is not None,
                data={
                    "code": s.code,
                    "name": s.name,
                    "project_id": str(s.project_id) if s.project_id else None,
                    "latitude": centre.latitude if centre else None,
                    "longitude": centre.longitude if centre else None,
                    "geofence_radius_m": str(s.geofence_radius_m),
                    "timezone": s.timezone,
                },
            )
        )
    return out
