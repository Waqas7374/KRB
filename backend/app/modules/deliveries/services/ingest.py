"""Record a delivery: authorise, validate, price, evaluate, persist.

One entry point serves the web form and (later) the mobile sync API, so the two
can never disagree about what a delivery is or how it is checked.

The contract (docs/05 §3, docs/06 §4):

* **A delivery is always saved.** Checks raise flags; only a request that is
  malformed or names something that does not exist is refused, and for reasons
  a person can fix.
* **The device never supplies a price, a factor or a rule.** Rate and unit
  conversion are resolved here from the capture time and snapshotted onto the
  line; every flag carries a snapshot of the rule that fired.
* **Idempotent.** The client-generated `id` is the key. Pushing the same
  delivery again returns the stored record and changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    ConversionNotConfiguredError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.scoping import assert_in_scope
from app.core.types import utcnow, uuid7
from app.modules.deliveries.domain import evaluation
from app.modules.deliveries.domain.enums import (
    DeliveryStatus,
    FlagSeverity,
    FlagStatus,
    FlagType,
    LocationSource,
)
from app.modules.deliveries.domain.evaluation import FlagDraft
from app.modules.deliveries.models import Delivery, DeliveryFlag, DeliveryItem
from app.modules.masterdata.services import material_lookup
from app.modules.masterdata.services.conversion import UnitConverter
from app.modules.org.services import company_service, site_lookup
from app.modules.procurement.services import po_lookup
from app.modules.rates.services import resolver as rate_resolver
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.services import resolver as rule_resolver
from app.modules.vendors.services import vendor_lookup
from app.platform import outbox
from app.platform.numbering import DocumentType, next_number

log = get_logger("deliveries")

PERM_CREATE = "deliveries.create"
_AMOUNT_QUANT = Decimal("0.0001")
_DEAD = (DeliveryStatus.REJECTED.value, DeliveryStatus.CANCELLED.value)


@dataclass(frozen=True, slots=True)
class LineInput:
    material_id: UUID
    quantity: Decimal
    unit_id: UUID
    remarks: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryInput:
    site_id: UUID
    vendor_id: UUID
    captured_at: datetime
    items: list[LineInput]
    # The client-generated key. Left out (the web form), the server makes one.
    id: UUID | None = None
    purchase_order_id: UUID | None = None
    po_item_id: UUID | None = None
    truck_number: str | None = None
    truck_type_id: UUID | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    challan_number: str | None = None
    challan_date: date | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    gps_accuracy_m: Decimal | None = None
    location_source: str = LocationSource.GPS.value
    remarks: str | None = None
    device_id: str | None = None
    app_version: str | None = None
    was_offline: bool = False
    # The device's clock at the moment it sent this: how skew is measured,
    # separately from how long the delivery waited offline.
    device_time: datetime | None = None


@dataclass(slots=True)
class IngestResult:
    delivery: Delivery
    duplicate: bool = False


@dataclass(slots=True)
class _Evaluation:
    flags: list[FlagDraft] = field(default_factory=list)

    def add(self, flag: FlagDraft | None, rule: rule_resolver.RuleView | None = None) -> None:
        if flag is None:
            return
        if rule is not None:
            flag = flag.with_rule(rule.id, rule.snapshot())
        self.flags.append(flag)


def flag_rows(ctx: AccessContext, drafts: list[FlagDraft]) -> list[DeliveryFlag]:
    return [
        DeliveryFlag(
            flag_type=f.flag_type.value,
            severity=f.severity.value,
            rule_id=f.rule_id,
            rule_snapshot=f.rule_snapshot,
            expected_value=f.expected_value,
            actual_value=f.actual_value,
            deviation_pct=f.deviation_pct,
            message=f.message,
            # INFO notes need nobody's decision.
            status=(FlagStatus.OPEN if f.severity.needs_review else FlagStatus.ACCEPTED).value,
            created_by_id=ctx.user_id,
        )
        for f in drafts
    ]


def _fail(field_name: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field_name, "code": "invalid", "message": message}]
    )


def normalise_truck(number: str | None) -> str | None:
    """Trucks are matched on letters and digits only: "RJ14 GB 4021" and
    "rj14-gb-4021" are the same vehicle."""
    if not number:
        return None
    cleaned = "".join(ch for ch in number.upper() if ch.isalnum())
    return cleaned or None


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class _Prepared:
    site: site_lookup.SiteInfo
    vendor: vendor_lookup.VendorInfo
    order: po_lookup.OrderInfo | None
    items: list[DeliveryItem]
    flags: list[FlagDraft]


async def ingest(session: AsyncSession, ctx: AccessContext, data: DeliveryInput) -> IngestResult:
    if data.id is not None:
        existing = await _existing(session, ctx, data.id)
        if existing is not None:
            return IngestResult(existing, duplicate=True)

    received_at = utcnow()
    prepared = await _prepare(session, ctx, data, received_at=received_at)
    delivery = await _persist(
        session,
        ctx,
        data,
        site=prepared.site,
        items=prepared.items,
        flags=prepared.flags,
        received_at=received_at,
        order=prepared.order,
    )
    return IngestResult(delivery)


async def _prepare(
    session: AsyncSession, ctx: AccessContext, data: DeliveryInput, *, received_at: datetime
) -> _Prepared:
    """Authorise, validate, price and evaluate. Writes nothing: the same
    pipeline serves a new delivery and a correction of an existing one."""
    if not data.items:
        raise _fail("items", "A delivery needs at least one line")
    if data.captured_at.tzinfo is None:
        raise _fail("captured_at", "must include a timezone")

    site = await site_lookup.get_site(session, company_id=ctx.company_id, site_id=data.site_id)
    if site is None:
        raise _fail("site_id", "Unknown site")
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=ctx.company_id,
        project_id=site.project_id,
        site_id=site.id,
        entity="Site",
    )
    if not site.is_active:
        raise BusinessRuleError("site_inactive", f"{site.code} is not an active site.")

    vendor = (
        await vendor_lookup.vendors(session, company_id=ctx.company_id, vendor_ids={data.vendor_id})
    ).get(data.vendor_id)
    if vendor is None:
        raise _fail("vendor_id", "Unknown vendor")

    materials = await material_lookup.materials(
        session,
        company_id=ctx.company_id,
        material_ids={line.material_id for line in data.items},
    )
    _check_lines(data, materials)

    truck = None
    if data.truck_type_id is not None:
        truck = await material_lookup.truck_type(
            session, company_id=ctx.company_id, truck_type_id=data.truck_type_id
        )
        if truck is None:
            raise _fail("truck_type_id", "Unknown truck type")

    order = await _resolve_order(session, ctx, data, site, vendor)
    if order is None and site.project_requires_po:
        raise BusinessRuleError(
            "purchase_order_required",
            f"Project {site.project_code} requires a purchase order for every delivery.",
        )

    converter = UnitConverter(session, ctx.company_id)
    verdict = _Evaluation()
    items: list[DeliveryItem] = []
    on = data.captured_at.date()
    for index, line in enumerate(data.items):
        matched = _match_po_item(order, line, data.po_item_id)
        item = await _price_line(
            session,
            ctx,
            converter,
            verdict,
            line=line,
            index=index,
            vendor_id=vendor.id,
            site=site,
            on=on,
            material=materials[line.material_id],
        )
        item.po_item_id = matched.id if matched else None
        items.append(item)

    await _evaluate(
        session,
        ctx,
        converter,
        verdict,
        data=data,
        site=site,
        vendor=vendor,
        truck=truck,
        order=order,
        materials=materials,
        received_at=received_at,
    )
    return _Prepared(site=site, vendor=vendor, order=order, items=items, flags=verdict.flags)


def _match_po_item(
    order: po_lookup.OrderInfo | None, line: LineInput, hint: UUID | None
) -> po_lookup.OrderItemInfo | None:
    """Which order line a load counts against: the one the device named if it is
    for this material, otherwise the order's line for that material."""
    if order is None:
        return None
    by_material = [i for i in order.items if i.material_id == line.material_id]
    if hint is not None:
        for candidate in by_material:
            if candidate.id == hint:
                return candidate
    return by_material[0] if by_material else None


async def _existing(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID
) -> Delivery | None:
    row = (
        await session.execute(select(Delivery).where(Delivery.id == delivery_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.company_id != ctx.company_id or row.submitted_by_id != ctx.user_id:
        # Same key, different owner: not a replay. Refusing keeps one user's
        # delivery from being read (or overwritten) by guessing its id.
        raise ConflictError("That delivery id is already in use.")
    return row


def _check_lines(data: DeliveryInput, materials: dict[UUID, material_lookup.MaterialInfo]) -> None:
    errors: list[dict[str, str]] = []
    for index, line in enumerate(data.items):
        info = materials.get(line.material_id)
        path = f"items.{index}"
        if info is None or not info.is_active:
            errors.append(
                {"field": f"{path}.material_id", "code": "invalid", "message": "unknown material"}
            )
        elif line.unit_id not in info.unit_ids:
            errors.append(
                {
                    "field": f"{path}.unit_id",
                    "code": "invalid",
                    "message": f"not a unit configured for {info.sku}",
                }
            )
        if line.quantity <= 0:
            errors.append(
                {"field": f"{path}.quantity", "code": "invalid", "message": "must be positive"}
            )
    if errors:
        raise ValidationError("Some lines are not valid.", errors=errors)


async def _resolve_order(
    session: AsyncSession,
    ctx: AccessContext,
    data: DeliveryInput,
    site: site_lookup.SiteInfo,
    vendor: vendor_lookup.VendorInfo,
) -> po_lookup.OrderInfo | None:
    if data.purchase_order_id is None:
        return None
    order = await po_lookup.order(
        session, company_id=ctx.company_id, purchase_order_id=data.purchase_order_id
    )
    if order is None:
        raise _fail("purchase_order_id", "Unknown purchase order")
    if order.vendor_id != vendor.id:
        raise _fail("purchase_order_id", f"{order.po_number} was placed with a different vendor")
    if not order.receivable:
        raise _fail(
            "purchase_order_id",
            f"{order.po_number} is {order.status.lower().replace('_', ' ')} and cannot be "
            "received against",
        )
    if data.po_item_id is not None and data.po_item_id not in {i.id for i in order.items}:
        raise _fail("po_item_id", f"not a line of {order.po_number}")
    return order


# -----------------------------------------------------------------------------
# Pricing: server-side, from the capture time
# -----------------------------------------------------------------------------


async def _price_line(
    session: AsyncSession,
    ctx: AccessContext,
    converter: UnitConverter,
    verdict: _Evaluation,
    *,
    line: LineInput,
    index: int,
    vendor_id: UUID,
    site: site_lookup.SiteInfo,
    on: date,
    material: material_lookup.MaterialInfo,
) -> DeliveryItem:
    """The rate and the conversion factor, resolved as of `on` (the capture
    date) and frozen on the line. A missing rate or factor does not stop the
    delivery: it is captured now and priced later, which is what happens on site."""
    item = DeliveryItem(
        line_no=index + 1,
        material_id=line.material_id,
        quantity=line.quantity,
        unit_id=line.unit_id,
        remarks=line.remarks,
        created_by_id=ctx.user_id,
    )
    rate = await rate_resolver.resolve(
        session,
        company_id=ctx.company_id,
        vendor_id=vendor_id,
        material_id=line.material_id,
        at=on,
        project_id=site.project_id,
        site_id=site.id,
    )
    if rate is None:
        verdict.add(
            FlagDraft(
                FlagType.RATE_MISSING,
                FlagSeverity.WARNING,
                f"No rate is set for {material.sku} from this vendor on {on:%d %b %Y}; "
                "it will be priced when one is",
            )
        )
        return item
    try:
        converted = await converter.convert(
            line.quantity,
            line.unit_id,
            rate.unit_id,
            material_id=line.material_id,
            vendor_id=vendor_id,
            at=on,
        )
    except ConversionNotConfiguredError as exc:
        verdict.add(
            FlagDraft(
                FlagType.CONVERSION_MISSING,
                FlagSeverity.WARNING,
                f"{exc} — the load cannot be priced until a conversion factor is set up",
            )
        )
        return item
    item.converted_quantity = converted.converted_quantity
    item.converted_unit_id = converted.converted_unit_id
    item.conversion_factor = converted.factor
    item.conversion_id = converted.conversion_id
    item.rate = rate.rate
    item.vendor_rate_id = rate.id
    item.rate_source = rate.source
    item.amount = (converted.converted_quantity * rate.rate).quantize(
        _AMOUNT_QUANT, rounding=ROUND_HALF_UP
    )
    return item


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------


def _rule_context(
    *,
    site: site_lookup.SiteInfo,
    vendor_id: UUID,
    material: material_lookup.MaterialInfo | None = None,
    truck_type_id: UUID | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "site_id": site.id,
        "project_id": site.project_id,
        "vendor_id": vendor_id,
    }
    if material is not None:
        context["material_id"] = material.id
        context["material_category_id"] = material.category_id
    if truck_type_id is not None:
        context["truck_type_id"] = truck_type_id
    return context


async def _rule(
    session: AsyncSession,
    ctx: AccessContext,
    rule_type: RuleType,
    context: dict[str, Any],
    at: date,
) -> rule_resolver.RuleView | None:
    return await rule_resolver.resolve(
        session, company_id=ctx.company_id, rule_type=rule_type, context=context, at=at
    )


async def _evaluate(
    session: AsyncSession,
    ctx: AccessContext,
    converter: UnitConverter,
    verdict: _Evaluation,
    *,
    data: DeliveryInput,
    site: site_lookup.SiteInfo,
    vendor: vendor_lookup.VendorInfo,
    truck: material_lookup.TruckTypeInfo | None,
    order: po_lookup.OrderInfo | None,
    materials: dict[UUID, material_lookup.MaterialInfo],
    received_at: datetime,
) -> None:
    on = data.captured_at.date()

    if not vendor.can_receive_orders:
        verdict.add(
            FlagDraft(
                FlagType.VENDOR_INACTIVE,
                FlagSeverity.WARNING,
                f"{vendor.name} is {vendor.status.lower()}; the delivery arrived anyway",
            )
        )

    if order is None:
        verdict.add(
            FlagDraft(
                FlagType.NO_PO,
                FlagSeverity.WARNING,
                "No purchase order — will be reviewed at head office",
            )
        )

    await _evaluate_geofence(session, ctx, verdict, data=data, site=site, on=on)

    # Clock: skew is how wrong the device's clock is, not how long the delivery
    # waited offline; that is lateness, checked next.
    skew_rule = await _rule(
        session, ctx, RuleType.CLOCK_SKEW_MAX, _rule_context(site=site, vendor_id=vendor.id), on
    )
    skew_seconds: int | None = None
    if data.device_time is not None:
        skew_seconds = int((data.device_time - received_at).total_seconds())
    future = int((data.captured_at - received_at).total_seconds())
    if skew_rule is not None:
        limit = int(skew_rule.value["seconds"])
        verdict.add(
            evaluation.check_clock_skew(skew_seconds=skew_seconds or 0, max_seconds=limit),
            skew_rule,
        )
        # A capture time in the future is a wrong clock whatever the batch said.
        if skew_seconds is None or abs(skew_seconds) <= limit:
            verdict.add(
                evaluation.check_clock_skew(skew_seconds=max(future, 0), max_seconds=limit),
                skew_rule,
            )

    late_rule = await _rule(
        session, ctx, RuleType.LATE_SUBMISSION, _rule_context(site=site, vendor_id=vendor.id), on
    )
    if late_rule is not None:
        verdict.add(
            evaluation.check_late_submission(
                captured_at=data.captured_at,
                received_at=received_at,
                max_age_days=int(late_rule.value["max_age_days"]),
            ),
            late_rule,
        )

    for line in data.items:
        material = materials[line.material_id]
        await _evaluate_tonnage(
            session,
            ctx,
            converter,
            verdict,
            line=line,
            material=material,
            data=data,
            site=site,
            vendor=vendor,
            truck=truck,
            on=on,
        )
        if order is not None:
            await _evaluate_po_balance(
                session,
                ctx,
                converter,
                verdict,
                line=line,
                material=material,
                data=data,
                site=site,
                vendor=vendor,
                order=order,
                on=on,
            )
        await _evaluate_daily_cap(
            session,
            ctx,
            verdict,
            line=line,
            material=material,
            data=data,
            site=site,
            vendor=vendor,
            on=on,
        )

    await _evaluate_duplicate(session, ctx, verdict, data=data, site=site, vendor=vendor, on=on)


async def _evaluate_geofence(
    session: AsyncSession,
    ctx: AccessContext,
    verdict: _Evaluation,
    *,
    data: DeliveryInput,
    site: site_lookup.SiteInfo,
    on: date,
) -> None:
    if data.latitude is None or data.longitude is None:
        verdict.add(
            evaluation.check_geofence(
                distance_outside_m=None,
                gps_accuracy_m=data.gps_accuracy_m,
                location_source=LocationSource.MANUAL.value,
            )
        )
        return
    # A rule scoped to this site or project beats the site's own radius, which
    # beats the company default.
    rule = await _rule(
        session,
        ctx,
        RuleType.GEOFENCE_RADIUS,
        {"site_id": site.id, "project_id": site.project_id},
        on,
    )
    radius = (
        Decimal(str(rule.value["radius_m"]))
        if rule is not None and rule.specificity > 0
        else site.geofence_radius_m
    )
    distance = await site_lookup.distance_outside_m(
        session,
        site_id=site.id,
        latitude=data.latitude,
        longitude=data.longitude,
        radius_m=radius,
    )
    if distance is None:
        return  # the site has no geometry to measure against
    verdict.add(
        evaluation.check_geofence(
            distance_outside_m=distance,
            gps_accuracy_m=data.gps_accuracy_m,
            location_source=data.location_source,
        ),
        rule if rule is not None and rule.specificity > 0 else None,
    )


async def _evaluate_tonnage(
    session: AsyncSession,
    ctx: AccessContext,
    converter: UnitConverter,
    verdict: _Evaluation,
    *,
    line: LineInput,
    material: material_lookup.MaterialInfo,
    data: DeliveryInput,
    site: site_lookup.SiteInfo,
    vendor: vendor_lookup.VendorInfo,
    truck: material_lookup.TruckTypeInfo | None,
    on: date,
) -> None:
    rule = await _rule(
        session,
        ctx,
        RuleType.TONNAGE_MAX,
        _rule_context(
            site=site, vendor_id=vendor.id, material=material, truck_type_id=data.truck_type_id
        ),
        on,
    )
    if rule is not None:
        limit = Decimal(str(rule.value["max"]))
    elif truck is not None:
        # No rule: the truck type's own default is the standing limit (docs/05).
        limit = truck.default_max_tonnage
    else:
        return
    ton_unit = await material_lookup.unit_by_code(session, company_id=ctx.company_id, code="TON")
    if ton_unit is None:
        return
    try:
        tons = (
            await converter.convert(
                line.quantity,
                line.unit_id,
                ton_unit,
                material_id=line.material_id,
                vendor_id=vendor.id,
                at=on,
            )
        ).converted_quantity
    except ConversionNotConfiguredError:
        return  # not a weight-priced load; the tonnage check does not apply
    subject = " / ".join(x for x in (truck.name if truck else "", material.name) if x)
    verdict.add(evaluation.check_tonnage(tons, limit, subject=subject), rule)


async def _received_to_date(
    session: AsyncSession,
    ctx: AccessContext,
    converter: UnitConverter,
    *,
    po_item: po_lookup.OrderItemInfo,
    vendor_id: UUID,
    exclude_delivery_id: UUID | None,
    on: date,
) -> Decimal:
    """Everything already received against one order line, in the line's unit,
    from deliveries that still stand."""
    rows = await session.execute(
        select(DeliveryItem.quantity, DeliveryItem.unit_id, DeliveryItem.delivery_id)
        .join(Delivery, Delivery.id == DeliveryItem.delivery_id)
        .where(
            Delivery.company_id == ctx.company_id,
            DeliveryItem.po_item_id == po_item.id,
            Delivery.status.notin_(_DEAD),
        )
    )
    total = Decimal(0)
    for quantity, unit_id, delivery_id in rows.tuples():
        if delivery_id == exclude_delivery_id:
            continue
        try:
            total += (
                await converter.convert(
                    quantity,
                    unit_id,
                    po_item.unit_id,
                    material_id=po_item.material_id,
                    vendor_id=vendor_id,
                    at=on,
                )
            ).converted_quantity
        except ConversionNotConfiguredError:
            continue
    return total


async def _evaluate_po_balance(
    session: AsyncSession,
    ctx: AccessContext,
    converter: UnitConverter,
    verdict: _Evaluation,
    *,
    line: LineInput,
    material: material_lookup.MaterialInfo,
    data: DeliveryInput,
    site: site_lookup.SiteInfo,
    vendor: vendor_lookup.VendorInfo,
    order: po_lookup.OrderInfo,
    on: date,
) -> None:
    po_item = _match_po_item(order, line, data.po_item_id)
    if po_item is None:
        verdict.add(
            FlagDraft(
                FlagType.PO_QTY_EXCEEDED,
                FlagSeverity.WARNING,
                f"{material.sku} is not on {order.po_number}",
            )
        )
        return
    try:
        this_load = (
            await converter.convert(
                line.quantity,
                line.unit_id,
                po_item.unit_id,
                material_id=line.material_id,
                vendor_id=vendor.id,
                at=on,
            )
        ).converted_quantity
    except ConversionNotConfiguredError:
        return
    before = await _received_to_date(
        session,
        ctx,
        converter,
        po_item=po_item,
        vendor_id=vendor.id,
        exclude_delivery_id=data.id,
        on=on,
    )
    rule = await _rule(
        session,
        ctx,
        RuleType.QTY_TOLERANCE,
        _rule_context(site=site, vendor_id=vendor.id, material=material),
        on,
    )
    pct = Decimal(str(rule.value.get("pct", 0))) if rule else Decimal(0)
    absolute = Decimal(str(rule.value.get("abs", 0))) if rule else Decimal(0)
    verdict.add(
        evaluation.check_po_balance(
            ordered=po_item.quantity,
            received_before=before,
            this_delivery=this_load,
            tolerance_pct=pct,
            tolerance_abs=absolute,
        ),
        rule,
    )


async def _evaluate_daily_cap(
    session: AsyncSession,
    ctx: AccessContext,
    verdict: _Evaluation,
    *,
    line: LineInput,
    material: material_lookup.MaterialInfo,
    data: DeliveryInput,
    site: site_lookup.SiteInfo,
    vendor: vendor_lookup.VendorInfo,
    on: date,
) -> None:
    rule = await _rule(
        session,
        ctx,
        RuleType.DAILY_DELIVERY_CAP,
        _rule_context(site=site, vendor_id=vendor.id, material=material),
        on,
    )
    if rule is None:
        return
    # "Today" is the site's local day, not the server's.
    zone = ZoneInfo(site.timezone)
    local = data.captured_at.astimezone(zone)
    start = datetime.combine(local.date(), datetime.min.time(), tzinfo=zone)
    end = start + timedelta(days=1)
    stmt = (
        select(
            func.count(func.distinct(Delivery.id)),
            func.coalesce(func.sum(DeliveryItem.quantity), 0),
        )
        .join(DeliveryItem, DeliveryItem.delivery_id == Delivery.id)
        .where(
            Delivery.company_id == ctx.company_id,
            Delivery.site_id == site.id,
            Delivery.vendor_id == vendor.id,
            Delivery.captured_at >= start,
            Delivery.captured_at < end,
            Delivery.status.notin_(_DEAD),
        )
    )
    per_material = "material_id" in rule.scope
    if per_material:
        stmt = stmt.where(DeliveryItem.material_id == material.id)
    count, quantity = (await session.execute(stmt)).one()
    max_deliveries = rule.value.get("max_deliveries")
    max_quantity = rule.value.get("max_quantity")
    verdict.add(
        evaluation.check_daily_cap(
            deliveries_today=int(count) + 1,
            quantity_today=Decimal(quantity) + line.quantity,
            max_deliveries=int(max_deliveries) if max_deliveries is not None else None,
            # Quantities in different units cannot be summed across materials.
            max_quantity=Decimal(str(max_quantity)) if per_material and max_quantity else None,
        ),
        rule,
    )


async def _evaluate_duplicate(
    session: AsyncSession,
    ctx: AccessContext,
    verdict: _Evaluation,
    *,
    data: DeliveryInput,
    site: site_lookup.SiteInfo,
    vendor: vendor_lookup.VendorInfo,
    on: date,
) -> None:
    truck = normalise_truck(data.truck_number)
    if truck is None:
        return
    rule = await _rule(
        session, ctx, RuleType.DUPLICATE_WINDOW, _rule_context(site=site, vendor_id=vendor.id), on
    )
    if rule is None:
        return
    window = timedelta(minutes=int(rule.value["minutes"]))
    materials = {line.material_id for line in data.items}
    rows = await session.execute(
        select(Delivery.id, Delivery.delivery_number, Delivery.truck_number)
        .join(DeliveryItem, DeliveryItem.delivery_id == Delivery.id)
        .where(
            Delivery.company_id == ctx.company_id,
            Delivery.site_id == site.id,
            Delivery.status.notin_(_DEAD),
            DeliveryItem.material_id.in_(materials),
            Delivery.captured_at >= data.captured_at - window,
            Delivery.captured_at <= data.captured_at + window,
        )
    )
    for _id, number, other_truck in rows.tuples():
        if normalise_truck(other_truck) == truck and _id != data.id:
            verdict.add(
                FlagDraft(
                    FlagType.DUPLICATE_SUSPECT,
                    FlagSeverity.WARNING,
                    f"Same truck and material as {number} within {rule.value['minutes']} minutes",
                ),
                rule,
            )
            return


# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------


async def _persist(
    session: AsyncSession,
    ctx: AccessContext,
    data: DeliveryInput,
    *,
    site: site_lookup.SiteInfo,
    items: list[DeliveryItem],
    flags: list[FlagDraft],
    received_at: datetime,
    order: po_lookup.OrderInfo | None,
) -> Delivery:
    from app.modules.org.services.geo import to_db_point

    number = await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DocumentType.DELIVERY,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )
    needs_review = any(f.severity.needs_review for f in flags)
    has_point = data.latitude is not None and data.longitude is not None
    distance: Decimal | None = None
    inside: bool | None = None
    geo = next((f for f in flags if f.flag_type is FlagType.GEOFENCE_MISMATCH), None)
    if has_point:
        # Inside unless a geofence flag records how far outside it was.
        inside = geo is None or geo.severity is FlagSeverity.INFO
        distance = geo.actual_value if geo is not None else Decimal(0)

    skew = (
        int((data.device_time - received_at).total_seconds())
        if data.device_time is not None
        else None
    )
    delivery = Delivery(
        id=data.id or uuid7(),
        company_id=ctx.company_id,
        delivery_number=number,
        status=(DeliveryStatus.UNDER_REVIEW if needs_review else DeliveryStatus.SUBMITTED).value,
        project_id=site.project_id,
        site_id=site.id,
        vendor_id=data.vendor_id,
        purchase_order_id=order.id if order else None,
        po_item_id=data.po_item_id,
        truck_number=data.truck_number,
        truck_type_id=data.truck_type_id,
        driver_name=data.driver_name,
        driver_phone=data.driver_phone,
        challan_number=data.challan_number,
        challan_date=data.challan_date,
        captured_lat=data.latitude,
        captured_lng=data.longitude,
        captured_point=to_db_point(float(data.latitude), float(data.longitude))
        if data.latitude is not None and data.longitude is not None
        else None,
        gps_accuracy_m=data.gps_accuracy_m,
        location_source=(data.location_source if has_point else LocationSource.MANUAL.value),
        distance_from_site_m=distance,
        is_inside_geofence=inside,
        captured_at=data.captured_at,
        received_at=received_at,
        clock_skew_seconds=skew,
        device_id=data.device_id,
        app_version=data.app_version,
        was_offline=data.was_offline,
        submitted_by_id=ctx.user_id,
        submitted_at=received_at,
        remarks=data.remarks,
        flag_count=len(flags),
        has_open_flags=any(f.severity.needs_review for f in flags),
        created_by_id=ctx.user_id,
        items=items,
        flags=flag_rows(ctx, flags),
    )
    session.add(delivery)
    await session.flush()
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="delivery.received",
            aggregate_type="Delivery",
            aggregate_id=delivery.id,
            payload={
                "delivery_number": delivery.delivery_number,
                "site_id": str(site.id),
                "site_code": site.code,
                "flag_count": len(flags),
                "needs_review": needs_review,
                "link_path": f"/deliveries/{delivery.id}",
            },
            company_id=ctx.company_id,
        ),
    )
    return delivery


async def get_or_404(session: AsyncSession, ctx: AccessContext, delivery_id: UUID) -> Delivery:
    row = (
        await session.execute(select(Delivery).where(Delivery.id == delivery_id))
    ).scalar_one_or_none()
    if row is None or row.company_id != ctx.company_id:
        raise NotFoundError("Delivery", delivery_id)
    return row


# -----------------------------------------------------------------------------
# Correction
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class CorrectionResult:
    delivery: Delivery
    previous_status: str


async def correct(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID, data: DeliveryInput
) -> CorrectionResult:
    """The capturer's answer to a reviewer's "please correct this".

    The delivery is put through exactly the checks a new one is, so a correction
    cannot dodge a flag by editing it away unseen: flags from the earlier
    version are kept as CORRECTED (with what replaced them) and the new version
    raises its own.
    """
    delivery = await get_or_404(session, ctx, delivery_id)
    await session.refresh(delivery, attribute_names=["items", "flags"])
    if not DeliveryStatus(delivery.status).is_editable:
        raise BusinessRuleError(
            "delivery_not_editable",
            f"{delivery.delivery_number} is {delivery.status.lower().replace('_', ' ')}; only a "
            "delivery a reviewer sent back can be corrected.",
        )
    if delivery.submitted_by_id != ctx.user_id and not ctx.has("deliveries.update_draft"):
        raise BusinessRuleError(
            "delivery_not_yours", "Only the person who captured a delivery can correct it."
        )
    previous_status = delivery.status
    keyed = _with_id(data, delivery.id)
    prepared = await _prepare(session, ctx, keyed, received_at=delivery.received_at)

    now = utcnow()
    for old in delivery.flags:
        if old.status == FlagStatus.OPEN.value:
            old.status = FlagStatus.CORRECTED.value
            old.resolved_by_id = ctx.user_id
            old.resolved_at = now
            old.resolution_note = "Replaced by the corrected entry"
    delivery.items.clear()
    await session.flush()
    delivery.items.extend(prepared.items)
    delivery.flags.extend(flag_rows(ctx, prepared.flags))

    needs_review = any(f.severity.needs_review for f in prepared.flags)
    delivery.vendor_id = data.vendor_id
    delivery.site_id = prepared.site.id
    delivery.project_id = prepared.site.project_id
    delivery.purchase_order_id = prepared.order.id if prepared.order else None
    delivery.po_item_id = data.po_item_id
    delivery.truck_number = data.truck_number
    delivery.truck_type_id = data.truck_type_id
    delivery.driver_name = data.driver_name
    delivery.driver_phone = data.driver_phone
    delivery.challan_number = data.challan_number
    delivery.challan_date = data.challan_date
    delivery.remarks = data.remarks
    delivery.captured_at = data.captured_at
    delivery.status = (
        DeliveryStatus.UNDER_REVIEW if needs_review else DeliveryStatus.SUBMITTED
    ).value
    delivery.flag_count = len(prepared.flags)
    delivery.has_open_flags = needs_review
    delivery.updated_by_id = ctx.user_id
    delivery.version += 1
    await session.flush()
    return CorrectionResult(delivery, previous_status)


def _with_id(data: DeliveryInput, delivery_id: UUID) -> DeliveryInput:
    from dataclasses import replace

    return replace(data, id=delivery_id)


# -----------------------------------------------------------------------------
# Attaching an order after the fact
# -----------------------------------------------------------------------------


async def evaluate_attachment(
    session: AsyncSession,
    ctx: AccessContext,
    delivery: Delivery,
    order: po_lookup.OrderInfo,
) -> list[FlagDraft]:
    """The purchase-order balance check for a delivery that had no order when
    it was captured. Only the order-related check: everything else was judged
    at capture and stands."""
    site = await site_lookup.get_site(session, company_id=ctx.company_id, site_id=delivery.site_id)
    vendor = (
        await vendor_lookup.vendors(
            session, company_id=ctx.company_id, vendor_ids={delivery.vendor_id}
        )
    ).get(delivery.vendor_id)
    if site is None or vendor is None:
        return []
    materials = await material_lookup.materials(
        session,
        company_id=ctx.company_id,
        material_ids={i.material_id for i in delivery.items},
    )
    data = DeliveryInput(
        id=delivery.id,
        site_id=delivery.site_id,
        vendor_id=delivery.vendor_id,
        captured_at=delivery.captured_at,
        items=[
            LineInput(material_id=i.material_id, quantity=i.quantity, unit_id=i.unit_id)
            for i in delivery.items
        ],
    )
    converter = UnitConverter(session, ctx.company_id)
    verdict = _Evaluation()
    for line in data.items:
        await _evaluate_po_balance(
            session,
            ctx,
            converter,
            verdict,
            line=line,
            material=materials[line.material_id],
            data=data,
            site=site,
            vendor=vendor,
            order=order,
            on=delivery.captured_at.date(),
        )
    return verdict.flags
