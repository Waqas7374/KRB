"""The unit-conversion engine.

Lives in the master-data module rather than in `app/platform` because it reads
`units` and `unit_conversions`, and `platform` sits *below* `modules` in the
layering. Other modules use it through this services package, which is their
permitted surface.

**No conversion factor is hard-coded anywhere in this codebase.** `1 tonne = X
cft` is a row in `unit_conversions` that an administrator maintains (§20).

Resolution, from most specific to least:

    MATERIAL_VENDOR  ->  MATERIAL  ->  VENDOR  ->  GLOBAL

filtered by the document's date, because a factor that changed in August must
not restate a July delivery.

Failure to resolve raises `ConversionNotConfiguredError`. It never falls back to
a guess: a missing factor is a configuration gap an administrator has to close,
not a number the system should invent. Inventing one would silently misprice
every delivery of that material.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.core.access import AccessContext
from app.core.errors import BusinessRuleError, ConversionNotConfiguredError
from app.core.logging import get_logger
from app.core.types import today_utc, uuid7
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.masterdata.domain.enums import ConversionScope
from app.modules.masterdata.models import Unit, UnitConversion

log = get_logger("units")

# Two hops at most: TON -> KG -> BAG is resolvable, anything longer is ambiguous
# and refused rather than guessed.
MAX_HOPS = 2


@dataclass(frozen=True, slots=True)
class ResolvedFactor:
    """A factor plus the evidence for it.

    Callers snapshot `factor` **and** `conversion_id` onto the document line, so
    the arithmetic behind a posted amount stays reproducible for ever.
    """

    factor: Decimal
    conversion_id: UUID | None
    scope: ConversionScope
    from_unit_code: str
    to_unit_code: str
    inverted: bool = False
    via_unit_code: str | None = None

    @property
    def is_identity(self) -> bool:
        return self.conversion_id is None and self.factor == Decimal(1)


@dataclass(frozen=True, slots=True)
class ConvertedQuantity:
    original_quantity: Decimal
    original_unit_id: UUID
    converted_quantity: Decimal
    converted_unit_id: UUID
    factor: Decimal
    conversion_id: UUID | None


class UnitConverter:
    """Resolves and applies conversion factors.

    Instantiated per request with the caller's session; the resolution cache is
    per-instance, so a delivery batch converting 50 lines of the same material
    hits the database once.
    """

    def __init__(self, session: AsyncSession, company_id: UUID) -> None:
        self._session = session
        self._company_id = company_id
        self._cache: dict[tuple[UUID, UUID, UUID | None, UUID | None, date], ResolvedFactor] = {}

    # -- Public API ----------------------------------------------------------

    async def factor(
        self,
        from_unit_id: UUID,
        to_unit_id: UUID,
        *,
        material_id: UUID | None = None,
        vendor_id: UUID | None = None,
        at: date | None = None,
    ) -> ResolvedFactor:
        """The factor to multiply a `from_unit` quantity by to get `to_unit`."""
        on = at or today_utc()

        if from_unit_id == to_unit_id:
            code = await self._unit_code(from_unit_id)
            return ResolvedFactor(
                factor=Decimal(1),
                conversion_id=None,
                scope=ConversionScope.GLOBAL,
                from_unit_code=code,
                to_unit_code=code,
            )

        key = (from_unit_id, to_unit_id, material_id, vendor_id, on)
        if key in self._cache:
            return self._cache[key]

        resolved = await self._resolve_direct(
            from_unit_id, to_unit_id, material_id=material_id, vendor_id=vendor_id, on=on
        )
        if resolved is None:
            resolved = await self._resolve_two_hop(
                from_unit_id, to_unit_id, material_id=material_id, vendor_id=vendor_id, on=on
            )

        if resolved is None:
            from_code = await self._unit_code(from_unit_id)
            to_code = await self._unit_code(to_unit_id)
            scope_text = []
            if material_id:
                scope_text.append(f"material {material_id}")
            if vendor_id:
                scope_text.append(f"vendor {vendor_id}")
            raise ConversionNotConfiguredError(from_code, to_code, " and ".join(scope_text))

        self._cache[key] = resolved
        return resolved

    async def convert(
        self,
        quantity: Decimal,
        from_unit_id: UUID,
        to_unit_id: UUID,
        *,
        material_id: UUID | None = None,
        vendor_id: UUID | None = None,
        at: date | None = None,
    ) -> ConvertedQuantity:
        """Convert a quantity, returning the factor used alongside the result."""
        resolved = await self.factor(
            from_unit_id, to_unit_id, material_id=material_id, vendor_id=vendor_id, at=at
        )
        precision = await self._unit_precision(to_unit_id)
        converted = (quantity * resolved.factor).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP
        )
        return ConvertedQuantity(
            original_quantity=quantity,
            original_unit_id=from_unit_id,
            converted_quantity=converted,
            converted_unit_id=to_unit_id,
            factor=resolved.factor,
            conversion_id=resolved.conversion_id,
        )

    # -- Resolution ----------------------------------------------------------

    async def _resolve_direct(
        self,
        from_unit_id: UUID,
        to_unit_id: UUID,
        *,
        material_id: UUID | None,
        vendor_id: UUID | None,
        on: date,
    ) -> ResolvedFactor | None:
        """Look for a row in either direction, most specific scope first.

        A row stored one way serves both directions: the inverse is 1/factor,
        so an administrator maintains one row rather than two that can disagree.
        """
        rows = await self._candidates(from_unit_id, to_unit_id, material_id, vendor_id, on)
        if not rows:
            return None

        best = max(
            rows, key=lambda r: (ConversionScope(r.scope_type).specificity, r.effective_from)
        )
        forward = best.from_unit_id == from_unit_id

        return ResolvedFactor(
            factor=best.factor if forward else (Decimal(1) / best.factor),
            conversion_id=best.id,
            scope=ConversionScope(best.scope_type),
            from_unit_code=await self._unit_code(from_unit_id),
            to_unit_code=await self._unit_code(to_unit_id),
            inverted=not forward,
        )

    async def _candidates(
        self,
        unit_a: UUID,
        unit_b: UUID,
        material_id: UUID | None,
        vendor_id: UUID | None,
        on: date,
    ) -> list[UnitConversion]:
        scope_filters = [UnitConversion.scope_type == ConversionScope.GLOBAL.value]
        if material_id is not None:
            scope_filters.append(
                (UnitConversion.scope_type == ConversionScope.MATERIAL.value)
                & (UnitConversion.material_id == material_id)
            )
        if vendor_id is not None:
            scope_filters.append(
                (UnitConversion.scope_type == ConversionScope.VENDOR.value)
                & (UnitConversion.vendor_id == vendor_id)
            )
        if material_id is not None and vendor_id is not None:
            scope_filters.append(
                (UnitConversion.scope_type == ConversionScope.MATERIAL_VENDOR.value)
                & (UnitConversion.material_id == material_id)
                & (UnitConversion.vendor_id == vendor_id)
            )

        stmt = select(UnitConversion).where(
            UnitConversion.company_id == self._company_id,
            UnitConversion.deleted_at.is_(None),
            UnitConversion.effective_from <= on,
            or_(UnitConversion.effective_to.is_(None), UnitConversion.effective_to >= on),
            or_(
                (UnitConversion.from_unit_id == unit_a) & (UnitConversion.to_unit_id == unit_b),
                (UnitConversion.from_unit_id == unit_b) & (UnitConversion.to_unit_id == unit_a),
            ),
            or_(*scope_filters),
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def _resolve_two_hop(
        self,
        from_unit_id: UUID,
        to_unit_id: UUID,
        *,
        material_id: UUID | None,
        vendor_id: UUID | None,
        on: date,
    ) -> ResolvedFactor | None:
        """Chain through one intermediate unit, e.g. TON -> KG -> BAG.

        Only considers units that both endpoints already connect to, so the
        search stays bounded. More than one hop is refused as ambiguous.
        """
        neighbours_from = await self._neighbours(from_unit_id, material_id, vendor_id, on)
        neighbours_to = await self._neighbours(to_unit_id, material_id, vendor_id, on)
        shared = set(neighbours_from) & set(neighbours_to)
        shared.discard(from_unit_id)
        shared.discard(to_unit_id)

        for middle in sorted(shared, key=str):
            first = await self._resolve_direct(
                from_unit_id, middle, material_id=material_id, vendor_id=vendor_id, on=on
            )
            second = await self._resolve_direct(
                middle, to_unit_id, material_id=material_id, vendor_id=vendor_id, on=on
            )
            if first and second:
                log.debug(
                    "units.two_hop_conversion",
                    via=await self._unit_code(middle),
                    from_unit=first.from_unit_code,
                    to_unit=second.to_unit_code,
                )
                return ResolvedFactor(
                    factor=first.factor * second.factor,
                    # A chained factor is not one stored row, so nothing is
                    # snapshotted as its source; the caller stores the factor
                    # itself, which is what reproducibility actually needs.
                    conversion_id=None,
                    scope=(
                        first.scope
                        if first.scope.specificity >= second.scope.specificity
                        else second.scope
                    ),
                    from_unit_code=first.from_unit_code,
                    to_unit_code=second.to_unit_code,
                    via_unit_code=await self._unit_code(middle),
                )
        return None

    async def _neighbours(
        self, unit_id: UUID, material_id: UUID | None, vendor_id: UUID | None, on: date
    ) -> list[UUID]:
        stmt = select(UnitConversion.from_unit_id, UnitConversion.to_unit_id).where(
            UnitConversion.company_id == self._company_id,
            UnitConversion.deleted_at.is_(None),
            UnitConversion.effective_from <= on,
            or_(UnitConversion.effective_to.is_(None), UnitConversion.effective_to >= on),
            or_(UnitConversion.from_unit_id == unit_id, UnitConversion.to_unit_id == unit_id),
        )
        result = []
        for from_id, to_id in (await self._session.execute(stmt)).tuples():
            result.append(to_id if from_id == unit_id else from_id)
        return result

    # -- Unit metadata -------------------------------------------------------

    async def _unit(self, unit_id: UUID) -> Unit:
        unit = (
            await self._session.execute(select(Unit).where(Unit.id == unit_id))
        ).scalar_one_or_none()
        if unit is None:
            raise ConversionNotConfiguredError(str(unit_id), "?", "unknown unit")
        return unit

    async def _unit_code(self, unit_id: UUID) -> str:
        return (await self._unit(unit_id)).code

    async def _unit_precision(self, unit_id: UUID) -> int:
        return (await self._unit(unit_id)).precision


# -----------------------------------------------------------------------------
# Writing factors
# -----------------------------------------------------------------------------
# There is deliberately no function that updates a factor in place. A
# conversion row is superseded, never edited: the old row is closed with
# `effective_to` and a new one inserted, so a document priced last month can
# still be recomputed with the factor that was actually used then.


async def supersede_factor(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    from_unit_id: UUID,
    to_unit_id: UUID,
    factor: Decimal,
    scope_type: str,
    material_id: UUID | None,
    vendor_id: UUID | None,
    effective_from: date,
    basis_note: str | None,
) -> UnitConversion:
    """Close the current factor for this pair/scope and insert a new one.

    Used both by direct admin entry and by calibration confirmation
    (`masterdata.services.calibration`), so the two paths produce identical,
    equally auditable rows.
    """
    current = (
        await session.execute(
            select(UnitConversion)
            # `from_unit`/`to_unit` are lazy="joined"; PostgreSQL refuses
            # FOR UPDATE across the resulting outer joins, so eager loading
            # is switched off for this one locking query. See the identical
            # note on ScopedRepository.get_for_update in core/crud.py.
            .options(noload("*"))
            .where(
                UnitConversion.company_id == ctx.company_id,
                UnitConversion.from_unit_id == from_unit_id,
                UnitConversion.to_unit_id == to_unit_id,
                UnitConversion.scope_type == scope_type,
                UnitConversion.material_id.is_(None)
                if material_id is None
                else UnitConversion.material_id == material_id,
                UnitConversion.vendor_id.is_(None)
                if vendor_id is None
                else UnitConversion.vendor_id == vendor_id,
                UnitConversion.deleted_at.is_(None),
                UnitConversion.effective_to.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if current is not None:
        if current.effective_from >= effective_from:
            raise BusinessRuleError(
                "effective_date_not_after_current",
                f"The current factor is effective from {current.effective_from}; "
                f"a new one must take effect after that date, not {effective_from}.",
            )
        current.effective_to = effective_from - timedelta(days=1)
        current.version += 1

    new_row = UnitConversion(
        id=uuid7(),
        company_id=ctx.company_id,
        from_unit_id=from_unit_id,
        to_unit_id=to_unit_id,
        factor=factor,
        scope_type=scope_type,
        material_id=material_id,
        vendor_id=vendor_id,
        effective_from=effective_from,
        basis_note=basis_note,
        supersedes_id=current.id if current else None,
    )
    session.add(new_row)
    await session.flush()

    old_value = current.factor if current else None
    await record_audit(
        session,
        action=AuditAction.RATE_CHANGE,
        entity_type="UnitConversion",
        entity_id=new_row.id,
        company_id=ctx.company_id,
        summary=(
            f"Unit conversion factor {'changed from ' + str(old_value) if old_value else 'set'} "
            f"to {factor}, effective {effective_from}"
        ),
        old_values={"factor": str(old_value)} if old_value else None,
        new_values={"factor": str(factor), "effective_from": str(effective_from)},
    )
    return new_row
