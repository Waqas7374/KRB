"""Weighbridge calibration: how a conversion factor gets its number.

§20 forbids hard-coding a conversion factor. It does not say where a *correct*
one comes from — this is that answer. An administrator logs real (weight,
volume) pairs from actual truckloads, reviews the spread across several
readings, and only then confirms a factor. Confirming creates an ordinary
effective-dated `unit_conversions` row through
`masterdata.services.conversion`, so nothing downstream needs to know
calibration exists — a confirmed factor is indistinguishable from one entered
directly.

Deliberately **not** auto-averaging: a single bad weighbridge reading must not
silently reprice every future delivery. A person reviews the numbers and
confirms.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import BusinessRuleError, NotFoundError
from app.core.logging import get_logger
from app.core.types import uuid7
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.masterdata.domain.enums import CalibrationStatus, ConversionScope
from app.modules.masterdata.models import Unit, UnitConversion, UnitConversionCalibration
from app.platform import outbox

log = get_logger("calibration")

_FACTOR_QUANT = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class CalibrationStats:
    """A summary of pending readings, for an admin to review before confirming."""

    count: int
    average_factor: Decimal | None
    median_factor: Decimal | None
    min_factor: Decimal | None
    max_factor: Decimal | None
    # Spread as a percentage of the average — a quick "how much do these
    # readings disagree" signal without making the admin do the arithmetic.
    spread_pct: Decimal | None
    readings: list[UnitConversionCalibration]


def _factor_of(reading: UnitConversionCalibration) -> Decimal:
    return reading.implied_factor


async def record_reading(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    material_id: UUID,
    vendor_id: UUID | None,
    from_unit_id: UUID,
    to_unit_id: UUID,
    source_quantity: Decimal,
    target_quantity: Decimal,
    recorded_at: date,
    truck_number: str | None = None,
    weighbridge_ref: str | None = None,
    notes: str | None = None,
) -> UnitConversionCalibration:
    """Log one weighbridge reading. Does not change any live conversion factor."""
    if source_quantity <= 0 or target_quantity <= 0:
        raise BusinessRuleError(
            "invalid_calibration_reading",
            "Both the weighed and the measured quantity must be greater than zero.",
        )

    implied = (target_quantity / source_quantity).quantize(_FACTOR_QUANT, rounding=ROUND_HALF_UP)

    reading = UnitConversionCalibration(
        id=uuid7(),
        company_id=ctx.company_id,
        material_id=material_id,
        vendor_id=vendor_id,
        from_unit_id=from_unit_id,
        to_unit_id=to_unit_id,
        source_quantity=source_quantity,
        target_quantity=target_quantity,
        implied_factor=implied,
        truck_number=truck_number,
        weighbridge_ref=weighbridge_ref,
        recorded_at=recorded_at,
        notes=notes,
        status=CalibrationStatus.PENDING.value,
    )
    session.add(reading)
    await session.flush()

    log.info(
        "calibration.reading_recorded",
        reading_id=str(reading.id),
        material_id=str(material_id),
        implied_factor=str(implied),
    )
    return reading


async def get_stats(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    material_id: UUID,
    vendor_id: UUID | None,
    from_unit_id: UUID,
    to_unit_id: UUID,
) -> CalibrationStats:
    """Pending readings for a pair, with summary statistics.

    What an admin reviews before deciding on a factor — never computed and
    applied automatically.
    """
    stmt = select(UnitConversionCalibration).where(
        UnitConversionCalibration.company_id == ctx.company_id,
        UnitConversionCalibration.material_id == material_id,
        UnitConversionCalibration.from_unit_id == from_unit_id,
        UnitConversionCalibration.to_unit_id == to_unit_id,
        UnitConversionCalibration.status == CalibrationStatus.PENDING.value,
    )
    stmt = (
        stmt.where(UnitConversionCalibration.vendor_id == vendor_id)
        if vendor_id
        else stmt.where(UnitConversionCalibration.vendor_id.is_(None))
    )
    readings = list(
        (await session.execute(stmt.order_by(UnitConversionCalibration.recorded_at)))
        .scalars()
        .all()
    )

    if not readings:
        return CalibrationStats(
            count=0,
            average_factor=None,
            median_factor=None,
            min_factor=None,
            max_factor=None,
            spread_pct=None,
            readings=[],
        )

    factors = [_factor_of(r) for r in readings]
    # sum()'s default start=0 is an int, which makes mypy infer Decimal | int
    # for the result unless a Decimal start is given explicitly.
    total = sum(factors, start=Decimal(0))
    average = (total / len(factors)).quantize(_FACTOR_QUANT, rounding=ROUND_HALF_UP)
    # statistics.median()'s stub returns float | Decimal for a mixed overload
    # set; the input is homogeneously Decimal, so the result always is too.
    median_value = Decimal(statistics.median(factors))
    median = median_value.quantize(_FACTOR_QUANT, rounding=ROUND_HALF_UP)
    lo, hi = min(factors), max(factors)
    spread_pct = (
        ((hi - lo) / average * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if average
        else None
    )

    return CalibrationStats(
        count=len(readings),
        average_factor=average,
        median_factor=median,
        min_factor=lo,
        max_factor=hi,
        spread_pct=spread_pct,
        readings=readings,
    )


async def discard_reading(
    session: AsyncSession, ctx: AccessContext, *, reading_id: UUID, reason: str
) -> UnitConversionCalibration:
    """Exclude a bad reading from calibration. Never deleted — the record of a
    bad reading is itself evidence of what went wrong."""
    reading = await _get_reading(session, ctx, reading_id)
    if reading.status != CalibrationStatus.PENDING.value:
        raise BusinessRuleError(
            "reading_not_pending",
            f"This reading is already {reading.status.lower()} and cannot be discarded.",
        )
    reading.status = CalibrationStatus.DISCARDED.value
    reading.discard_reason = reason
    reading.version += 1
    return reading


async def confirm_calibration(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    material_id: UUID,
    vendor_id: UUID | None,
    from_unit_id: UUID,
    to_unit_id: UUID,
    factor: Decimal,
    reading_ids: list[UUID],
    effective_from: date,
    basis_note: str | None,
) -> UnitConversion:
    """Turn confirmed readings into a live, effective-dated conversion factor.

    `factor` is supplied explicitly by the admin — it is expected to be the
    average or median from `get_stats`, but the admin may override it (e.g. to
    discount an outlier without formally discarding it). The readings named in
    `reading_ids` are marked APPLIED and linked to the resulting row, so the
    factor's provenance is never lost.

    Delegates the actual row creation to `conversion.supersede_factor`, so a
    calibrated factor and a manually-entered one are created identically and
    obey the same "never overwrite history" rule.
    """
    from app.modules.masterdata.services import conversion as conversion_service

    if factor <= 0:
        raise BusinessRuleError("invalid_factor", "The confirmed factor must be greater than zero.")

    readings: list[UnitConversionCalibration] = []
    for reading_id in reading_ids:
        reading = await _get_reading(session, ctx, reading_id)
        if reading.status != CalibrationStatus.PENDING.value:
            raise BusinessRuleError(
                "reading_not_pending",
                f"Reading {reading_id} is {reading.status.lower()}, not pending.",
            )
        if (
            reading.material_id != material_id
            or reading.vendor_id != vendor_id
            or reading.from_unit_id != from_unit_id
            or reading.to_unit_id != to_unit_id
        ):
            raise BusinessRuleError(
                "reading_mismatch",
                f"Reading {reading_id} does not match this material/vendor/unit pair.",
            )
        readings.append(reading)

    if not readings:
        raise BusinessRuleError(
            "no_readings_selected",
            "Confirm at least one weighbridge reading to derive this factor from.",
        )

    scope_type = (
        ConversionScope.MATERIAL_VENDOR.value if vendor_id else ConversionScope.MATERIAL.value
    )
    conversion = await conversion_service.supersede_factor(
        session,
        ctx,
        from_unit_id=from_unit_id,
        to_unit_id=to_unit_id,
        factor=factor,
        scope_type=scope_type,
        material_id=material_id,
        vendor_id=vendor_id,
        effective_from=effective_from,
        basis_note=basis_note or f"Calibrated from {len(readings)} weighbridge reading(s)",
    )

    for reading in readings:
        reading.status = CalibrationStatus.APPLIED.value
        reading.applied_conversion_id = conversion.id
        reading.version += 1

    from_code = await session.scalar(select(Unit.code).where(Unit.id == from_unit_id))
    to_code = await session.scalar(select(Unit.code).where(Unit.id == to_unit_id))

    await record_audit(
        session,
        action=AuditAction.RULE_CHANGE,
        entity_type="UnitConversion",
        entity_id=conversion.id,
        entity_label=f"{from_code} -> {to_code}",
        company_id=ctx.company_id,
        summary=(
            f"Conversion {from_code} -> {to_code} calibrated to {factor} from "
            f"{len(readings)} weighbridge reading(s), effective {effective_from}"
        ),
        new_values={
            "factor": str(factor),
            "reading_ids": [str(r.id) for r in readings],
        },
    )
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="unit_conversion.calibrated",
            aggregate_type="UnitConversion",
            aggregate_id=conversion.id,
            payload={"factor": str(factor), "reading_count": len(readings)},
            company_id=ctx.company_id,
        ),
    )
    log.info(
        "calibration.confirmed",
        conversion_id=str(conversion.id),
        factor=str(factor),
        reading_count=len(readings),
    )
    return conversion


async def _get_reading(
    session: AsyncSession, ctx: AccessContext, reading_id: UUID
) -> UnitConversionCalibration:
    reading = (
        await session.execute(
            select(UnitConversionCalibration).where(
                UnitConversionCalibration.id == reading_id,
                UnitConversionCalibration.company_id == ctx.company_id,
            )
        )
    ).scalar_one_or_none()
    if reading is None:
        raise NotFoundError("Calibration reading", reading_id)
    return reading


async def list_readings(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    material_id: UUID | None = None,
    status: CalibrationStatus | None = None,
) -> list[UnitConversionCalibration]:
    stmt = select(UnitConversionCalibration).where(
        UnitConversionCalibration.company_id == ctx.company_id
    )
    if material_id is not None:
        stmt = stmt.where(UnitConversionCalibration.material_id == material_id)
    if status is not None:
        stmt = stmt.where(UnitConversionCalibration.status == status.value)
    stmt = stmt.order_by(UnitConversionCalibration.recorded_at.desc())
    return list((await session.execute(stmt)).scalars().all())
