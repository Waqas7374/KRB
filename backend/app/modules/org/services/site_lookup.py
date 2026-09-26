"""Small site lookups other modules need.

Exposed as a service so the access resolver can ask "which project does this
site belong to?" without importing the org module's ORM models — the boundary
enforced by tests/unit/test_architecture.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.models import Project, Site


async def project_ids_for_sites(session: AsyncSession, site_ids: list[UUID]) -> dict[UUID, UUID]:
    """Map each site to its project, skipping company-level sites.

    A central store or head-office yard has no project, so it simply does not
    appear in the result.
    """
    if not site_ids:
        return {}

    rows = (
        await session.execute(
            select(Site.id, Site.project_id).where(
                Site.id.in_(site_ids), Site.project_id.is_not(None)
            )
        )
    ).tuples()
    return {site_id: project_id for site_id, project_id in rows.all() if project_id is not None}


@dataclass(frozen=True, slots=True)
class SiteInfo:
    id: UUID
    code: str
    name: str
    project_id: UUID | None
    project_code: str | None
    # docs/12 Q3: a project can make a purchase order mandatory for deliveries.
    project_requires_po: bool
    timezone: str
    geofence_radius_m: Decimal
    is_active: bool


async def get_site(session: AsyncSession, *, company_id: UUID, site_id: UUID) -> SiteInfo | None:
    row = (
        await session.execute(
            select(Site, Project)
            .outerjoin(Project, Project.id == Site.project_id)
            .where(Site.company_id == company_id, Site.id == site_id)
        )
    ).first()
    if row is None:
        return None
    site, project = row
    return SiteInfo(
        id=site.id,
        code=site.code,
        name=site.name,
        project_id=site.project_id,
        project_code=project.code if project else None,
        project_requires_po=project.require_po_for_delivery if project else False,
        timezone=site.timezone,
        geofence_radius_m=site.geofence_radius_m,
        is_active=site.deleted_at is None,
    )


async def distance_outside_m(
    session: AsyncSession,
    *,
    site_id: UUID,
    latitude: Decimal,
    longitude: Decimal,
    radius_m: Decimal,
) -> Decimal | None:
    """Metres a point lies beyond a site's fence; 0 when inside; None when the
    site has no boundary or centre to measure against (docs/05 §2).

    A polygon boundary wins when present; otherwise the fence is `radius_m`
    around the centre. PostGIS ordering is (longitude, latitude) - the one
    place a swap would put every site in the wrong hemisphere, so it is here
    and nowhere else.
    """
    point = func.ST_GeogFromText(f"SRID=4326;POINT({longitude} {latitude})")
    fence = case(
        (Site.boundary.is_not(None), func.ST_Distance(Site.boundary, point)),
        (
            Site.centroid.is_not(None),
            func.greatest(func.ST_Distance(Site.centroid, point) - radius_m, 0),
        ),
        else_=None,
    )
    distance = (await session.execute(select(fence).where(Site.id == site_id))).scalar_one_or_none()
    if distance is None:
        return None
    return Decimal(str(distance)).quantize(Decimal("0.01"))
