"""Resolve the rate for a delivery, order line or invoice check.

The one entry point features use (docs/05 §4): `resolve(...)` returns the
winning `RateView`, or None — the caller decides what "no rate" means (a
delivery is captured now and priced later, flagged RATE_MISSING). Only
approved rates count; a change awaiting approval is not in force.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.modules.rates.domain import resolution
from app.modules.rates.domain.enums import RateStatus
from app.modules.rates.domain.resolution import RateView
from app.modules.rates.models import VendorRate
from app.modules.rates.services.rate_service import view


async def resolve(
    session: AsyncSession,
    *,
    company_id: UUID,
    vendor_id: UUID,
    material_id: UUID,
    at: date | None = None,
    project_id: UUID | None = None,
    site_id: UUID | None = None,
    unit_id: UUID | None = None,
) -> RateView | None:
    rows = await session.execute(
        select(VendorRate).where(
            VendorRate.company_id == company_id,
            VendorRate.vendor_id == vendor_id,
            VendorRate.material_id == material_id,
            VendorRate.status == RateStatus.ACTIVE.value,
        )
    )
    return resolution.pick(
        [view(r) for r in rows.scalars()],
        at=at or utcnow().date(),
        project_id=project_id,
        site_id=site_id,
        unit_id=unit_id,
    )
