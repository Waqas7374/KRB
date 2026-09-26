"""Vendor-rate endpoints.

There is no PUT for a rate's value, by design (docs/05 §4): a change is a new
period, proposed with POST and brought into force by approval.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.access import AccessContext
from app.core.errors import BusinessRuleError
from app.core.pagination import Page
from app.core.scoping import assert_in_scope
from app.core.types import utcnow
from app.modules.approvals.services import engine as approvals
from app.modules.identity.services import user_lookup
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import document_lookup
from app.modules.rates.domain.enums import RateStatus
from app.modules.rates.models import VendorRate
from app.modules.rates.schemas import (
    RateCreate,
    RateHistoryRead,
    RateNotes,
    RateRead,
    ResolvedRateRead,
)
from app.modules.rates.services import rate_service, resolver
from app.modules.vendors.services import vendor_lookup

router = APIRouter(prefix="/vendor-rates", tags=["vendor-rates"])

IfMatch = Annotated[int | None, Header(alias="If-Match", description="The version you loaded")]


async def _views(
    session: AsyncSession, ctx: AccessContext, rows: list[VendorRate]
) -> list[RateRead]:
    company = ctx.company_id
    vendors = await vendor_lookup.vendors(
        session, company_id=company, vendor_ids={r.vendor_id for r in rows}
    )
    materials = await material_lookup.materials(
        session, company_id=company, material_ids={r.material_id for r in rows}
    )
    units = await material_lookup.unit_codes(
        session, company_id=company, unit_ids={r.unit_id for r in rows}
    )
    codes = await document_lookup.codes(
        session,
        company_id=company,
        project_ids={r.project_id for r in rows if r.project_id},
        site_ids={r.site_id for r in rows if r.site_id},
    )
    people = await user_lookup.people(
        session,
        company_id=company,
        user_ids={r.requested_by_id for r in rows if r.requested_by_id},
    )
    today = utcnow().date()
    out: list[RateRead] = []
    for r in rows:
        v = RateRead.model_validate(r)
        vendor = vendors.get(r.vendor_id)
        material = materials.get(r.material_id)
        if vendor:
            v.vendor_code, v.vendor_name = vendor.code, vendor.name
        if material:
            v.material_sku, v.material_name = material.sku, material.name
        v.unit_code = units.get(r.unit_id)
        if r.project_id:
            v.project_code = codes.get(r.project_id, (None, None))[0]
        if r.site_id:
            v.site_code = codes.get(r.site_id, (None, None))[0]
        v.scope = (
            f"Site {v.site_code}"
            if r.site_id
            else f"Project {v.project_code}"
            if r.project_id
            else "Company-wide"
        )
        v.is_current = (
            r.status == RateStatus.ACTIVE.value
            and r.effective_from <= today
            and (r.effective_to is None or r.effective_to >= today)
        )
        if r.requested_by_id in people:
            v.requested_by_name = people[r.requested_by_id].full_name
        v.can_withdraw = (
            r.status == RateStatus.PENDING_APPROVAL.value
            and r.requested_by_id == ctx.user_id
            and r.approval_request_id is not None
        )
        out.append(v)
    return out


@router.get("", response_model=Page[RateRead], dependencies=[require(rate_service.PERM_VIEW)])
async def list_rates(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    vendor_id: UUID | None = None,
    material_id: UUID | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    site_id: UUID | None = None,
    project_id: UUID | None = None,
    current: bool = False,
) -> Page[RateRead]:
    rows, total = await rate_service.list_rates(
        session,
        ctx,
        page=page,
        filters={
            "vendor_id": vendor_id,
            "material_id": material_id,
            "status": status,
            "site_id": site_id,
            "project_id": project_id,
        },
        current_only=current,
    )
    return Page.of(await _views(session, ctx, rows), params=page, total=total)


@router.post(
    "",
    response_model=RateRead,
    status_code=201,
    dependencies=[require(rate_service.PERM_CREATE)],
    summary="Propose a new rate period. It comes into force when approved.",
)
async def propose_rate(payload: RateCreate, ctx: Access, uow: UowDep) -> RateRead:
    row = await rate_service.propose(
        uow.session,
        ctx,
        rate_service.RateInput(
            vendor_id=payload.vendor_id,
            material_id=payload.material_id,
            unit_id=payload.unit_id,
            rate=payload.rate,
            currency_code=payload.currency_code,
            project_id=payload.project_id,
            site_id=payload.site_id,
            effective_from=payload.effective_from,
            reason=payload.reason,
            source=payload.source.value,
        ),
    )
    await uow.session.flush()
    await uow.session.refresh(row)
    return (await _views(uow.session, ctx, [row]))[0]


@router.get(
    "/history",
    response_model=Page[RateHistoryRead],
    dependencies=[require(rate_service.PERM_HISTORY)],
    summary="Every rate change: old value, new value, who and why. Append-only.",
)
async def rate_history(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    vendor_id: UUID | None = None,
    material_id: UUID | None = None,
) -> Page[RateHistoryRead]:
    rows, total = await rate_service.history(
        session, ctx, vendor_id=vendor_id, material_id=material_id, page=page
    )
    vendors = await vendor_lookup.vendors(
        session, company_id=ctx.company_id, vendor_ids={r.vendor_id for r in rows}
    )
    materials = await material_lookup.materials(
        session, company_id=ctx.company_id, material_ids={r.material_id for r in rows}
    )
    people = await user_lookup.people(
        session,
        company_id=ctx.company_id,
        user_ids={r.changed_by_id for r in rows if r.changed_by_id},
    )
    items = []
    for r in rows:
        item = RateHistoryRead.model_validate(r)
        item.vendor_name = vendors[r.vendor_id].name if r.vendor_id in vendors else None
        item.material_name = materials[r.material_id].name if r.material_id in materials else None
        if r.changed_by_id in people:
            item.changed_by_name = people[r.changed_by_id].full_name
        items.append(item)
    return Page.of(items, params=page, total=total)


@router.get(
    "/resolve",
    response_model=ResolvedRateRead | None,
    dependencies=[require(rate_service.PERM_VIEW)],
    summary="The rate that applies to a vendor, material, place and date — or null",
)
async def resolve_rate(
    ctx: Access,
    session: SessionDep,
    vendor_id: UUID,
    material_id: UUID,
    at: date | None = None,
    project_id: UUID | None = None,
    site_id: UUID | None = None,
    unit_id: UUID | None = None,
) -> ResolvedRateRead | None:
    if site_id is not None or project_id is not None:
        assert_in_scope(
            ctx,
            rate_service.PERM_VIEW,
            company_id=ctx.company_id,
            project_id=project_id,
            site_id=site_id,
            entity="Vendor rate",
        )
    found = await resolver.resolve(
        session,
        company_id=ctx.company_id,
        vendor_id=vendor_id,
        material_id=material_id,
        at=at,
        project_id=project_id,
        site_id=site_id,
        unit_id=unit_id,
    )
    if found is None:
        return None
    units = await material_lookup.unit_codes(
        session, company_id=ctx.company_id, unit_ids={found.unit_id}
    )
    return ResolvedRateRead(
        rate_id=found.id,
        rate=found.rate,
        unit_id=found.unit_id,
        unit_code=units.get(found.unit_id),
        currency_code=found.currency_code,
        scope="site" if found.site_id else "project" if found.project_id else "company",
        effective_from=found.effective_from,
        effective_to=found.effective_to,
        source=found.source,
    )


@router.get("/{rate_id}", response_model=RateRead, dependencies=[require(rate_service.PERM_VIEW)])
async def get_rate(rate_id: UUID, ctx: Access, session: SessionDep) -> RateRead:
    return (await _views(session, ctx, [await rate_service.get(session, ctx, rate_id)]))[0]


@router.patch(
    "/{rate_id}",
    response_model=RateRead,
    dependencies=[require(rate_service.PERM_CREATE)],
    summary="Correct a rate's notes. The value is never editable.",
)
async def update_notes(
    rate_id: UUID, payload: RateNotes, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> RateRead:
    row = await rate_service.update_notes(
        uow.session, ctx, rate_id, payload.notes, expected_version=if_match
    )
    await uow.session.flush()
    await uow.session.refresh(row)
    return (await _views(uow.session, ctx, [row]))[0]


@router.post(
    "/{rate_id}/withdraw",
    response_model=RateRead,
    dependencies=[require(rate_service.PERM_CREATE)],
    summary="Withdraw a proposal that has not been approved yet",
)
async def withdraw_rate(rate_id: UUID, ctx: Access, uow: UowDep) -> RateRead:
    row = await rate_service.get(uow.session, ctx, rate_id)
    if row.status != RateStatus.PENDING_APPROVAL.value or row.approval_request_id is None:
        raise BusinessRuleError(
            "rate_not_pending", "Only a rate change awaiting approval can be withdrawn."
        )
    await approvals.recall(
        uow.session, ctx, row.approval_request_id, "Withdrawn by the person who proposed it"
    )
    await uow.session.flush()
    await uow.session.refresh(row)
    return (await _views(uow.session, ctx, [row]))[0]
