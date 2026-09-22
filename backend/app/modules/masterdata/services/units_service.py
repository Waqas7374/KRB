"""Units and unit-conversion CRUD.

Conversion factors are never updated in place — see
`conversion.supersede_factor`. This module wraps that for direct admin entry
(as opposed to the calibration workflow in `calibration.py`, which arrives at
the same call through weighbridge readings).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import NotFoundError
from app.core.types import today_utc
from app.modules.masterdata.domain.enums import ConversionScope
from app.modules.masterdata.models import Unit, UnitConversion
from app.modules.masterdata.services import conversion as conversion_service


def unit_repository(session: AsyncSession) -> ScopedRepository[Unit]:
    return ScopedRepository(
        session,
        Unit,
        entity_name="Unit",
        sortable={"code", "name", "dimension", "created_at"},
        searchable=("code", "name"),
        default_sort="code",
    )


async def create_unit(
    session: AsyncSession, ctx: AccessContext, *, payload: dict[str, Any]
) -> Unit:
    repo = unit_repository(session)
    await repo.assert_code_available(ctx.company_id, "code", payload["code"])
    return await repo.create(company_id=ctx.company_id, **payload)


async def update_unit(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    unit_id: UUID,
    changes: dict[str, Any],
    expected_version: int | None = None,
) -> Unit:
    repo = unit_repository(session)
    unit = await repo.get_for_update(ctx, "units.view", unit_id)
    repo.apply_update(unit, changes, expected_version=expected_version)
    return unit


async def list_conversions(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    material_id: UUID | None = None,
    vendor_id: UUID | None = None,
    from_unit_id: UUID | None = None,
    to_unit_id: UUID | None = None,
    current_only: bool = True,
) -> list[UnitConversion]:
    stmt = select(UnitConversion).where(
        UnitConversion.company_id == ctx.company_id, UnitConversion.deleted_at.is_(None)
    )
    if material_id is not None:
        stmt = stmt.where(UnitConversion.material_id == material_id)
    if vendor_id is not None:
        stmt = stmt.where(UnitConversion.vendor_id == vendor_id)
    if from_unit_id is not None:
        stmt = stmt.where(UnitConversion.from_unit_id == from_unit_id)
    if to_unit_id is not None:
        stmt = stmt.where(UnitConversion.to_unit_id == to_unit_id)
    if current_only:
        stmt = stmt.where(UnitConversion.effective_to.is_(None))
    stmt = stmt.order_by(UnitConversion.effective_from.desc())
    return list((await session.execute(stmt)).scalars().all())


async def get_conversion_history(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    from_unit_id: UUID,
    to_unit_id: UUID,
    scope_type: ConversionScope,
    material_id: UUID | None,
    vendor_id: UUID | None,
) -> list[UnitConversion]:
    """Every row ever recorded for one pair/scope, current and superseded.

    "Rs 48 -> Rs 52, 12 Aug" for a rate; the same idea for a conversion factor.
    """
    stmt = (
        select(UnitConversion)
        .where(
            UnitConversion.company_id == ctx.company_id,
            UnitConversion.from_unit_id == from_unit_id,
            UnitConversion.to_unit_id == to_unit_id,
            UnitConversion.scope_type == scope_type.value,
            UnitConversion.material_id.is_(material_id)
            if material_id is None
            else UnitConversion.material_id == material_id,
            UnitConversion.vendor_id.is_(vendor_id)
            if vendor_id is None
            else UnitConversion.vendor_id == vendor_id,
        )
        .order_by(UnitConversion.effective_from.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def create_or_supersede_conversion(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    from_unit_id: UUID,
    to_unit_id: UUID,
    factor: Decimal,
    scope_type: ConversionScope,
    material_id: UUID | None,
    vendor_id: UUID | None,
    effective_from: date | None,
    basis_note: str | None,
) -> UnitConversion:
    """Direct admin entry of a factor — the counterpart to calibration."""
    return await conversion_service.supersede_factor(
        session,
        ctx,
        from_unit_id=from_unit_id,
        to_unit_id=to_unit_id,
        factor=factor,
        scope_type=scope_type.value,
        material_id=material_id,
        vendor_id=vendor_id,
        effective_from=effective_from or today_utc(),
        basis_note=basis_note,
    )


async def get_unit_or_404(session: AsyncSession, ctx: AccessContext, unit_id: UUID) -> Unit:
    unit = (
        await session.execute(
            select(Unit).where(Unit.id == unit_id, Unit.company_id == ctx.company_id)
        )
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError("Unit", unit_id)
    return unit
