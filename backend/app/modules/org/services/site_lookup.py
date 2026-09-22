"""Small site lookups other modules need.

Exposed as a service so the access resolver can ask "which project does this
site belong to?" without importing the org module's ORM models — the boundary
enforced by tests/unit/test_architecture.py.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.models import Site


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
