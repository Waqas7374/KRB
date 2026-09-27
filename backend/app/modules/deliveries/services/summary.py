"""The material-delivery dashboard (docs/08 §5, requirements §23): today's totals, and
the shape of a period by day, material, vendor and site.

Everything is computed in the database over the deliveries the caller may see, and
every figure is one a list screen can reproduce (the dashboard's tiles link to the
filtered list behind them). Loads that were rejected or cancelled count as such but
add nothing to a quantity or a value: they did not arrive.

Quantities are the server-converted figures in each material's base unit, so a
period holds "TON 120 and CFT 800", never a sum across units that mean different
things. "Tonnage" is the total in tonnes specifically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.scoping import scope_filter
from app.modules.deliveries.domain.enums import DeliveryStatus
from app.modules.deliveries.models import Delivery, DeliveryItem
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import document_lookup
from app.modules.vendors.services import vendor_lookup

PERM_VIEW = "deliveries.view"
TONNES = "TON"
TOP = 8
# A load counts as soon as it arrives, priced or not. Its converted figures exist only once a rate
# has been found (the conversion is resolved alongside the price), so a load still waiting for
# one is counted in the unit it was entered in rather than left out of the totals.
QTY = func.coalesce(DeliveryItem.converted_quantity, DeliveryItem.quantity)
UNIT = func.coalesce(DeliveryItem.converted_unit_id, DeliveryItem.unit_id)
_DEAD = (DeliveryStatus.REJECTED.value, DeliveryStatus.CANCELLED.value)
_WAITING = (DeliveryStatus.UNDER_REVIEW.value, DeliveryStatus.CORRECTION_REQUESTED.value)


@dataclass(slots=True)
class Quantity:
    unit_code: str
    quantity: Decimal


@dataclass(slots=True)
class DayPoint:
    day: date
    deliveries: int = 0
    tonnage: Decimal = Decimal(0)
    value: Decimal | None = None


@dataclass(slots=True)
class Rank:
    id: UUID
    label: str
    sublabel: str | None = None
    deliveries: int = 0
    quantities: list[Quantity] = field(default_factory=list)
    value: Decimal | None = None


@dataclass(slots=True)
class Waiting:
    id: UUID
    delivery_number: str
    captured_at: datetime
    site_code: str | None
    vendor_name: str | None
    flag_count: int
    status: str


@dataclass(slots=True)
class Summary:
    from_date: date
    to_date: date
    deliveries: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    open_flags: int = 0
    tonnage: Decimal = Decimal(0)
    quantities: list[Quantity] = field(default_factory=list)
    value: Decimal | None = None
    by_day: list[DayPoint] = field(default_factory=list)
    top_materials: list[Rank] = field(default_factory=list)
    top_vendors: list[Rank] = field(default_factory=list)
    by_site: list[Rank] = field(default_factory=list)
    waiting: list[Waiting] = field(default_factory=list)
    waiting_total: int = 0


async def summarise(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    start: datetime,
    end: datetime,
    from_date: date,
    to_date: date,
    timezone: str,
    show_value: bool,
) -> Summary:
    company = ctx.company_id
    in_period: Any = (
        scope_filter(select(Delivery.id), Delivery, ctx, PERM_VIEW)
        .where(Delivery.captured_at >= start, Delivery.captured_at < end)
        .scalar_subquery()
    )
    arrived: Any = (
        scope_filter(select(Delivery.id), Delivery, ctx, PERM_VIEW)
        .where(
            Delivery.captured_at >= start,
            Delivery.captured_at < end,
            Delivery.status.not_in(_DEAD),
        )
        .scalar_subquery()
    )
    out = Summary(from_date=from_date, to_date=to_date)

    # -- Counts ----------------------------------------------------------------------------
    rows = await session.execute(
        select(Delivery.status, func.count())
        .where(Delivery.id.in_(in_period))
        .group_by(Delivery.status)
    )
    out.by_status = {status: int(n) for status, n in rows.tuples().all()}
    out.deliveries = sum(out.by_status.values())
    out.open_flags = int(
        await session.scalar(
            select(func.count()).where(
                Delivery.id.in_(in_period), Delivery.has_open_flags.is_(True)
            )
        )
        or 0
    )

    # -- Quantities and value, over loads that arrived ----------------------------------------
    unit_rows = (
        (
            await session.execute(
                select(UNIT, func.sum(QTY))
                .where(
                    DeliveryItem.delivery_id.in_(arrived),
                )
                .group_by(UNIT)
            )
        )
        .tuples()
        .all()
    )
    if show_value:
        out.value = await session.scalar(
            select(func.sum(DeliveryItem.amount)).where(DeliveryItem.delivery_id.in_(arrived))
        )

    # Names, looked up once for everything below.
    day = cast(func.timezone(timezone, Delivery.captured_at), Date)
    day_rows = (
        (
            await session.execute(
                select(day, func.count(func.distinct(Delivery.id)))
                .where(Delivery.id.in_(arrived))
                .group_by(day)
            )
        )
        .tuples()
        .all()
    )
    day_units = (
        (
            await session.execute(
                select(
                    day,
                    UNIT,
                    func.sum(QTY),
                    func.sum(DeliveryItem.amount),
                )
                .join(Delivery, Delivery.id == DeliveryItem.delivery_id)
                .where(Delivery.id.in_(arrived))
                .group_by(day, UNIT)
            )
        )
        .tuples()
        .all()
    )

    material_rows = (
        (
            await session.execute(
                select(
                    DeliveryItem.material_id,
                    UNIT,
                    func.sum(QTY),
                    func.count(func.distinct(DeliveryItem.delivery_id)),
                    func.sum(DeliveryItem.amount),
                )
                .where(
                    DeliveryItem.delivery_id.in_(arrived),
                )
                .group_by(DeliveryItem.material_id, UNIT)
                .order_by(func.sum(QTY).desc())
                .limit(TOP)
            )
        )
        .tuples()
        .all()
    )
    vendor_rows = (
        (
            await session.execute(
                select(
                    Delivery.vendor_id,
                    func.count(func.distinct(Delivery.id)),
                    func.sum(DeliveryItem.amount),
                )
                .join(DeliveryItem, DeliveryItem.delivery_id == Delivery.id)
                .where(Delivery.id.in_(arrived))
                .group_by(Delivery.vendor_id)
                .order_by(func.count(func.distinct(Delivery.id)).desc())
                .limit(TOP)
            )
        )
        .tuples()
        .all()
    )
    site_loads = dict(
        (
            await session.execute(
                select(Delivery.site_id, func.count())
                .where(Delivery.id.in_(arrived))
                .group_by(Delivery.site_id)
            )
        )
        .tuples()
        .all()
    )
    site_rows = (
        (
            await session.execute(
                select(
                    Delivery.site_id,
                    UNIT,
                    func.sum(QTY),
                )
                .join(DeliveryItem, DeliveryItem.delivery_id == Delivery.id)
                .where(Delivery.id.in_(arrived))
                .group_by(Delivery.site_id, UNIT)
            )
        )
        .tuples()
        .all()
    )
    waiting_stmt = scope_filter(select(Delivery), Delivery, ctx, PERM_VIEW).where(
        Delivery.has_open_flags.is_(True), Delivery.status.in_(_WAITING)
    )
    waiting_total = int(
        await session.scalar(select(func.count()).select_from(waiting_stmt.subquery())) or 0
    )
    waiting = (
        (await session.execute(waiting_stmt.order_by(Delivery.captured_at).limit(TOP)))
        .scalars()
        .unique()
        .all()
    )

    unit_ids = (
        {u for u, _ in unit_rows if u}
        | {u for _, u, _, _ in day_units if u}
        | {u for _, u, _, _, _ in material_rows if u}
        | {u for _, u, _ in site_rows if u}
    )
    units = await material_lookup.unit_codes(session, company_id=company, unit_ids=unit_ids)

    def unit_of(unit_id: UUID | None) -> str:
        return units.get(unit_id, "?") if unit_id is not None else "?"

    materials = await material_lookup.materials(
        session, company_id=company, material_ids={m for m, *_ in material_rows}
    )
    vendors = await vendor_lookup.vendors(
        session,
        company_id=company,
        vendor_ids={v for v, *_ in vendor_rows} | {d.vendor_id for d in waiting},
    )
    sites = await document_lookup.codes(
        session,
        company_id=company,
        project_ids=set(),
        site_ids=set(site_loads) | {d.site_id for d in waiting},
    )

    # -- Assemble ----------------------------------------------------------------------------
    out.quantities = sorted(
        (Quantity(unit_of(u), q or Decimal(0)) for u, q in unit_rows),
        key=lambda q: q.quantity,
        reverse=True,
    )
    out.tonnage = sum((q.quantity for q in out.quantities if q.unit_code == TONNES), Decimal(0))

    points: dict[date, DayPoint] = {d: DayPoint(day=d, deliveries=int(n)) for d, n in day_rows}
    for d, unit_id, qty, amount in day_units:
        point = points.setdefault(d, DayPoint(day=d))
        if unit_of(unit_id) == TONNES and qty:
            point.tonnage += qty
        if show_value and amount is not None:
            point.value = (point.value or Decimal(0)) + amount
    out.by_day = [points[d] for d in sorted(points)]

    for material_id, unit_id, qty, loads, amount in material_rows:
        info = materials.get(material_id)
        out.top_materials.append(
            Rank(
                id=material_id,
                label=info.name if info else "—",
                sublabel=info.sku if info else None,
                deliveries=int(loads),
                quantities=[Quantity(unit_of(unit_id), qty or Decimal(0))],
                value=amount if show_value else None,
            )
        )
    for vendor_id, loads, amount in vendor_rows:
        vendor = vendors.get(vendor_id)
        out.top_vendors.append(
            Rank(
                id=vendor_id,
                label=vendor.name if vendor else "—",
                sublabel=vendor.code if vendor else None,
                deliveries=int(loads),
                value=amount if show_value else None,
            )
        )
    by_site: dict[UUID, Rank] = {
        site_id: Rank(
            id=site_id,
            label=sites.get(site_id, (None, None))[0] or "—",
            deliveries=int(loads),
        )
        for site_id, loads in site_loads.items()
    }
    for site_id, unit_id, qty in site_rows:
        if qty is not None:
            by_site[site_id].quantities.append(Quantity(unit_of(unit_id), qty))
    out.by_site = sorted(by_site.values(), key=lambda r: r.deliveries, reverse=True)[:TOP]

    out.waiting_total = waiting_total
    out.waiting = [
        Waiting(
            id=d.id,
            delivery_number=d.delivery_number,
            captured_at=d.captured_at,
            site_code=sites.get(d.site_id, (None, None))[0],
            vendor_name=vendors[d.vendor_id].name if d.vendor_id in vendors else None,
            flag_count=d.flag_count,
            status=d.status,
        )
        for d in waiting
    ]
    return out
