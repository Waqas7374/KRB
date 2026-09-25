"""Requests for quotation: draft, invite vendors, issue, close.

An RFQ turns approved purchase-request lines into a question put to several
vendors. It carries no approval of its own: nothing is spent by asking.
Quotations (services/quotations.py) are the answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, ValidationError, VersionConflictError
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope
from app.core.types import utcnow
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.org.services import company_service, document_lookup
from app.modules.procurement.domain.enums import (
    PurchaseRequestStatus,
    RfqStatus,
    RfqVendorStatus,
)
from app.modules.procurement.models import PurchaseRequest, Rfq, RfqItem, RfqVendor
from app.modules.procurement.services import line_checks
from app.modules.procurement.services import purchase_requests as pr_service
from app.modules.vendors.services import vendor_lookup
from app.platform.numbering import DocumentType, next_number

DOC_TYPE = DocumentType.RFQ
PERM_VIEW = "procurement.rfq.view"
PERM_CREATE = "procurement.rfq.create"
PERM_ISSUE = "procurement.rfq.issue"

# A request can be sourced only after it has been approved.
_SOURCEABLE = frozenset(
    {PurchaseRequestStatus.APPROVED.value, PurchaseRequestStatus.PARTIALLY_SOURCED.value}
)


def repository(session: AsyncSession) -> ScopedRepository[Rfq]:
    return ScopedRepository(
        session,
        Rfq,
        entity_name="RFQ",
        sortable={"rfq_number", "status", "due_date", "issue_date", "created_at", "updated_at"},
        searchable=("rfq_number", "title"),
        default_sort="-created_at",
    )


@dataclass(frozen=True, slots=True)
class RfqLineInput:
    material_id: UUID
    quantity: Decimal
    unit_id: UUID
    description: str | None = None
    pr_item_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RfqInput:
    project_id: UUID | None
    site_id: UUID | None
    purchase_request_id: UUID | None
    title: str
    due_date: date | None
    terms: str | None
    # None means "the unsourced lines of the linked purchase request".
    items: list[RfqLineInput] | None


@dataclass(frozen=True, slots=True)
class RfqEdit:
    title: str
    due_date: date | None
    terms: str | None
    items: list[RfqLineInput]


def _check_version(rfq: Rfq, expected: int | None) -> None:
    if expected is not None and rfq.version != expected:
        raise VersionConflictError(
            f"{rfq.rfq_number} was changed by someone else (version {rfq.version}, "
            f"you had {expected}). Reload and try again."
        )


def _check_due_date(due: date | None) -> None:
    if due is not None and due < utcnow().date():
        raise ValidationError(
            "The due date is in the past.",
            errors=[{"field": "due_date", "code": "invalid", "message": "must not be in the past"}],
        )


async def _source_request(
    session: AsyncSession, ctx: AccessContext, request_id: UUID
) -> PurchaseRequest:
    request = await pr_service.get(session, ctx, request_id)
    if request.status not in _SOURCEABLE:
        raise BusinessRuleError(
            "purchase_request_not_sourceable",
            f"{request.pr_number} is {request.status.lower().replace('_', ' ')}; only an approved "
            "request can be sourced.",
        )
    return request


def outstanding(item: Any) -> Decimal:
    """How much of a request line no purchase order has taken yet."""
    return Decimal(item.quantity) - Decimal(item.sourced_quantity)


def lines_from_request(request: PurchaseRequest) -> list[RfqLineInput]:
    """The unsourced remainder of each line. A fully sourced line is left out:
    asking vendors to quote what has already been ordered is noise."""
    return [
        RfqLineInput(
            material_id=item.material_id,
            quantity=outstanding(item),
            unit_id=item.unit_id,
            description=item.description,
            pr_item_id=item.id,
        )
        for item in request.items
        if outstanding(item) > 0
    ]


async def _build(
    session: AsyncSession,
    ctx: AccessContext,
    lines: list[RfqLineInput],
    request: PurchaseRequest | None,
) -> list[RfqItem]:
    if not lines:
        raise ValidationError(
            "An RFQ needs at least one line.",
            errors=[{"field": "items", "code": "required", "message": "add at least one line"}],
        )
    await line_checks.check_materials_and_units(
        session,
        company_id=ctx.company_id,
        lines=[(i.material_id, i.unit_id) for i in lines],
    )
    by_id = {i.id: i for i in request.items} if request is not None else {}
    errors: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if line.pr_item_id is None:
            continue
        source = by_id.get(line.pr_item_id)
        if source is None:
            errors.append(
                {
                    "field": f"items.{index}.pr_item_id",
                    "code": "invalid",
                    "message": "not a line of the linked purchase request",
                }
            )
        elif line.quantity > outstanding(source):
            errors.append(
                {
                    "field": f"items.{index}.quantity",
                    "code": "invalid",
                    "message": f"only {outstanding(source):f} remains to be sourced",
                }
            )
    if errors:
        raise ValidationError("Some lines are not valid.", errors=errors)
    return [
        RfqItem(
            line_no=index + 1,
            material_id=line.material_id,
            description=line.description,
            quantity=line.quantity,
            unit_id=line.unit_id,
            pr_item_id=line.pr_item_id,
            created_by_id=ctx.user_id,
        )
        for index, line in enumerate(lines)
    ]


async def _validate_place(
    session: AsyncSession, ctx: AccessContext, project_id: UUID, site_id: UUID | None
) -> None:
    try:
        place = await document_lookup.resolve_place(
            session, company_id=ctx.company_id, project_id=project_id, site_id=site_id
        )
    except document_lookup.PlaceMismatchError as exc:
        raise ValidationError(
            str(exc), errors=[{"field": exc.field, "code": "invalid", "message": str(exc)}]
        ) from exc
    if place.project_status in {"COMPLETED", "CLOSED", "CANCELLED"}:
        raise BusinessRuleError(
            "project_not_open",
            f"Project {place.project_code} is {place.project_status.lower()}; "
            "no RFQ can be raised against it.",
        )
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=ctx.company_id,
        project_id=project_id,
        site_id=site_id,
        entity="Project",
    )


async def _invite(
    session: AsyncSession, ctx: AccessContext, rfq: Rfq, vendor_ids: list[UUID]
) -> None:
    """Add vendors to `rfq`. Only active vendors may be asked to quote: a
    suspended or blacklisted vendor cannot be ordered from, so inviting one
    wastes everybody's time."""
    have = {v.vendor_id for v in rfq.vendors}
    wanted = [v for v in dict.fromkeys(vendor_ids) if v not in have]
    if not wanted:
        return
    info = await vendor_lookup.vendors(session, company_id=ctx.company_id, vendor_ids=set(wanted))
    errors = []
    for index, vendor_id in enumerate(vendor_ids):
        vendor = info.get(vendor_id)
        if vendor_id in have:
            continue
        if vendor is None:
            errors.append(
                {"field": f"vendor_ids.{index}", "code": "invalid", "message": "unknown vendor"}
            )
        elif not vendor.can_receive_orders:
            errors.append(
                {
                    "field": f"vendor_ids.{index}",
                    "code": "invalid",
                    "message": f"{vendor.name} is {vendor.status.lower()} and cannot be invited",
                }
            )
    if errors:
        raise ValidationError("Some vendors cannot be invited.", errors=errors)
    now = utcnow()
    for vendor_id in wanted:
        rfq.vendors.append(
            RfqVendor(
                vendor_id=vendor_id,
                invited_at=now,
                status=RfqVendorStatus.INVITED.value,
                created_by_id=ctx.user_id,
            )
        )


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------


async def create(
    session: AsyncSession, ctx: AccessContext, data: RfqInput, vendor_ids: list[UUID]
) -> Rfq:
    request: PurchaseRequest | None = None
    project_id, site_id = data.project_id, data.site_id
    if data.purchase_request_id is not None:
        request = await _source_request(session, ctx, data.purchase_request_id)
        # The RFQ inherits the request's place: it is the same need.
        project_id, site_id = request.project_id, request.site_id
    if project_id is None:
        raise ValidationError(
            "A project is required.",
            errors=[{"field": "project_id", "code": "required", "message": "choose a project"}],
        )
    await _validate_place(session, ctx, project_id, site_id)
    _check_due_date(data.due_date)
    lines = data.items
    if lines is None:
        assert request is not None  # RfqCreate guarantees a source of lines
        lines = lines_from_request(request)
    items = await _build(session, ctx, lines, request)

    number = await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DOC_TYPE,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )
    rfq = Rfq(
        company_id=ctx.company_id,
        rfq_number=number,
        status=RfqStatus.DRAFT.value,
        project_id=project_id,
        site_id=site_id,
        purchase_request_id=data.purchase_request_id,
        title=data.title.strip(),
        due_date=data.due_date,
        terms=data.terms,
        currency_code=request.currency_code if request is not None else "PKR",
        created_by_id=ctx.user_id,
        items=items,
        vendors=[],
    )
    session.add(rfq)
    await session.flush()
    await _invite(session, ctx, rfq, vendor_ids)
    await session.flush()
    return rfq


async def _get_for_update(session: AsyncSession, ctx: AccessContext, rfq_id: UUID) -> Rfq:
    rfq = await repository(session).get_for_update(ctx, PERM_VIEW, rfq_id)
    await session.refresh(rfq, attribute_names=["items", "vendors"])
    return rfq


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    rfq_id: UUID,
    data: RfqEdit,
    *,
    expected_version: int | None,
) -> Rfq:
    rfq = await _get_for_update(session, ctx, rfq_id)
    _check_version(rfq, expected_version)
    if rfq.status != RfqStatus.DRAFT.value:
        raise BusinessRuleError(
            "rfq_locked",
            f"{rfq.rfq_number} is {rfq.status.lower()}; only a draft can be edited. "
            "Vendors have already been asked to quote against these lines.",
        )
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=rfq.company_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        entity="RFQ",
    )
    _check_due_date(data.due_date)
    request = (
        await _source_request(session, ctx, rfq.purchase_request_id)
        if rfq.purchase_request_id is not None
        else None
    )
    items = await _build(session, ctx, data.items, request)
    rfq.title = data.title.strip()
    rfq.due_date = data.due_date
    rfq.terms = data.terms
    rfq.items.clear()
    await session.flush()
    rfq.items.extend(items)
    rfq.updated_by_id = ctx.user_id
    rfq.version += 1
    await session.flush()
    return rfq


async def invite_vendors(
    session: AsyncSession,
    ctx: AccessContext,
    rfq_id: UUID,
    vendor_ids: list[UUID],
) -> Rfq:
    rfq = await _get_for_update(session, ctx, rfq_id)
    if rfq.status not in {RfqStatus.DRAFT.value, RfqStatus.ISSUED.value}:
        raise BusinessRuleError(
            "rfq_closed",
            f"{rfq.rfq_number} is {rfq.status.lower()}; vendors can no longer be added.",
        )
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=rfq.company_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        entity="RFQ",
    )
    await _invite(session, ctx, rfq, vendor_ids)
    rfq.version += 1
    await session.flush()
    return rfq


async def remove_vendor(
    session: AsyncSession, ctx: AccessContext, rfq_id: UUID, rfq_vendor_id: UUID
) -> Rfq:
    rfq = await _get_for_update(session, ctx, rfq_id)
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=rfq.company_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        entity="RFQ",
    )
    invitation = next((v for v in rfq.vendors if v.id == rfq_vendor_id), None)
    if invitation is None:
        raise ValidationError(
            "That vendor is not on this RFQ.",
            errors=[{"field": "vendor", "code": "invalid", "message": "not invited"}],
        )
    if rfq.status != RfqStatus.DRAFT.value:
        raise BusinessRuleError(
            "rfq_locked",
            "A vendor can be withdrawn only before the RFQ is issued. After that, mark the "
            "vendor as declined instead, so the record of who was asked stays intact.",
        )
    rfq.vendors.remove(invitation)
    rfq.version += 1
    await session.flush()
    return rfq


async def decline_vendor(
    session: AsyncSession, ctx: AccessContext, rfq_id: UUID, rfq_vendor_id: UUID
) -> Rfq:
    rfq = await _get_for_update(session, ctx, rfq_id)
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=rfq.company_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        entity="RFQ",
    )
    if rfq.status != RfqStatus.ISSUED.value:
        raise BusinessRuleError("rfq_not_issued", "Only an issued RFQ has vendor responses.")
    invitation = next((v for v in rfq.vendors if v.id == rfq_vendor_id), None)
    if invitation is None:
        raise ValidationError(
            "That vendor is not on this RFQ.",
            errors=[{"field": "vendor", "code": "invalid", "message": "not invited"}],
        )
    if invitation.status == RfqVendorStatus.QUOTED.value:
        raise BusinessRuleError(
            "vendor_already_quoted",
            "This vendor has already quoted. Reject the quotation instead.",
        )
    invitation.status = RfqVendorStatus.DECLINED.value
    invitation.responded_at = utcnow()
    rfq.version += 1
    await session.flush()
    return rfq


async def issue(
    session: AsyncSession, ctx: AccessContext, rfq_id: UUID, *, expected_version: int | None
) -> Rfq:
    rfq = await _get_for_update(session, ctx, rfq_id)
    _check_version(rfq, expected_version)
    assert_in_scope(
        ctx,
        PERM_ISSUE,
        company_id=rfq.company_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        entity="RFQ",
    )
    if rfq.status != RfqStatus.DRAFT.value:
        raise BusinessRuleError(
            "rfq_not_draft",
            f"{rfq.rfq_number} is {rfq.status.lower()}; only a draft can be issued.",
        )
    if not rfq.items:
        raise BusinessRuleError("rfq_empty", "An RFQ needs at least one line before it is issued.")
    if not rfq.vendors:
        raise BusinessRuleError(
            "rfq_no_vendors", "Invite at least one vendor before issuing the RFQ."
        )
    if rfq.due_date is None:
        raise BusinessRuleError(
            "rfq_no_due_date", "Set the date by which vendors must respond before issuing."
        )
    _check_due_date(rfq.due_date)
    # A vendor suspended since the invitation was drafted must not receive it.
    info = await vendor_lookup.vendors(
        session, company_id=rfq.company_id, vendor_ids={v.vendor_id for v in rfq.vendors}
    )
    blocked = [
        info[v.vendor_id].name
        for v in rfq.vendors
        if v.vendor_id in info and not info[v.vendor_id].can_receive_orders
    ]
    if blocked:
        raise BusinessRuleError(
            "rfq_vendor_not_active",
            f"{', '.join(blocked)} can no longer receive orders. Remove them before issuing.",
        )
    rfq.status = RfqStatus.ISSUED.value
    rfq.issue_date = utcnow().date()
    rfq.version += 1
    await record_audit(
        session,
        action=AuditAction.UPDATE,
        entity_type="Rfq",
        entity_id=rfq.id,
        entity_label=rfq.rfq_number,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        summary=f"{rfq.rfq_number} issued to {len(rfq.vendors)} vendor(s), responses due "
        f"{rfq.due_date:%d %b %Y}",
    )
    await session.flush()
    return rfq


async def close(session: AsyncSession, ctx: AccessContext, rfq_id: UUID, reason: str) -> Rfq:
    rfq = await _get_for_update(session, ctx, rfq_id)
    assert_in_scope(
        ctx,
        PERM_ISSUE,
        company_id=rfq.company_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        entity="RFQ",
    )
    if rfq.status != RfqStatus.ISSUED.value:
        raise BusinessRuleError(
            "rfq_not_issued",
            f"{rfq.rfq_number} is {rfq.status.lower()}; only an issued RFQ closes.",
        )
    mark_closed(rfq, reason.strip())
    await record_audit(
        session,
        action=AuditAction.CLOSE,
        entity_type="Rfq",
        entity_id=rfq.id,
        entity_label=rfq.rfq_number,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        summary=f"{rfq.rfq_number} closed: {reason.strip()}",
    )
    await session.flush()
    return rfq


def mark_closed(rfq: Rfq, reason: str) -> None:
    rfq.status = RfqStatus.CLOSED.value
    rfq.closed_at = utcnow()
    rfq.close_reason = reason
    rfq.version += 1


async def cancel(session: AsyncSession, ctx: AccessContext, rfq_id: UUID, reason: str) -> Rfq:
    rfq = await _get_for_update(session, ctx, rfq_id)
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=rfq.company_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        entity="RFQ",
    )
    if rfq.status not in {RfqStatus.DRAFT.value, RfqStatus.ISSUED.value}:
        raise BusinessRuleError(
            "rfq_not_cancellable",
            f"{rfq.rfq_number} is {rfq.status.lower()} and cannot be cancelled.",
        )
    # Imported here: quotations import this module for the RFQ lookup.
    from app.modules.procurement.services import quotations

    if await quotations.has_selected(session, rfq.id):
        raise BusinessRuleError(
            "rfq_has_selection",
            "A quotation has been selected on this RFQ. Withdraw the selection first.",
        )
    previous = rfq.status
    rfq.status = RfqStatus.CANCELLED.value
    rfq.closed_at = utcnow()
    rfq.close_reason = reason.strip()
    rfq.version += 1
    await record_audit(
        session,
        action=AuditAction.CANCEL,
        entity_type="Rfq",
        entity_id=rfq.id,
        entity_label=rfq.rfq_number,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        summary=f"{rfq.rfq_number} cancelled ({previous} -> CANCELLED): {reason.strip()}",
    )
    await session.flush()
    return rfq


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------


async def list_rfqs(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[Rfq], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, rfq_id: UUID) -> Rfq:
    return await repository(session).get(ctx, PERM_VIEW, rfq_id)
