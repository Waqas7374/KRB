"""Vendor endpoints.

Note the split around bank details: `vendors.view` gets you the vendor and a
masked account summary, `vendors.manage_bank_details` gets you the numbers.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.pagination import Page
from app.modules.vendors.domain.enums import VendorStatus, VendorType
from app.modules.vendors.schemas import (
    VendorApprove,
    VendorBankAccountIn,
    VendorBankAccountRead,
    VendorContactRead,
    VendorCreate,
    VendorDetail,
    VendorListItem,
    VendorRead,
    VendorSuspend,
    VendorUpdate,
)
from app.modules.vendors.services import vendor_service

router = APIRouter()


@router.get(
    "",
    response_model=Page[VendorListItem],
    dependencies=[require("vendors.view")],
    summary="List vendors",
)
async def list_vendors(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(description="Search code, name, NTN, phone or email")] = None,
    status_filter: Annotated[
        list[VendorStatus] | None, Query(alias="status", description="Repeat for OR")
    ] = None,
    vendor_type: Annotated[list[VendorType] | None, Query()] = None,
) -> Page[VendorListItem]:
    repo = vendor_service.repository(session)
    rows, total = await repo.list(
        ctx,
        "vendors.view",
        page=page,
        search=q,
        filters={
            "status": [s.value for s in status_filter] if status_filter else None,
            "vendor_type": [t.value for t in vendor_type] if vendor_type else None,
        },
    )

    counts = await vendor_service.material_counts(session, [row.id for row in rows])
    items = [
        VendorListItem(
            id=row.id,
            code=row.code,
            display_name=row.display_name,
            vendor_type=row.vendor_type,
            status=row.status,
            payment_terms_days=row.payment_terms_days,
            phone=row.phone,
            email=row.email,
            city=(row.address or {}).get("city"),
            material_count=counts.get(row.id, 0),
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return Page[VendorListItem].of(items, params=page, total=total)


@router.post(
    "",
    response_model=VendorRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("vendors.create")],
    summary="Create a vendor (starts as DRAFT)",
)
async def create_vendor(payload: VendorCreate, ctx: Access, uow: UowDep) -> VendorRead:
    data = payload.model_dump(exclude={"contacts", "material_ids"}, exclude_none=False)
    if data.get("address") is not None:
        data["address"] = payload.address.model_dump() if payload.address else None
    data["vendor_type"] = payload.vendor_type.value

    vendor = await vendor_service.create_vendor(
        uow.session,
        ctx,
        payload=data,
        contacts=[contact.model_dump() for contact in payload.contacts],
        material_ids=payload.material_ids,
    )
    return VendorRead.model_validate(vendor)


@router.get(
    "/{vendor_id}",
    response_model=VendorDetail,
    dependencies=[require("vendors.view")],
    summary="One vendor with contacts",
)
async def get_vendor(vendor_id: UUID, ctx: Access, session: SessionDep) -> VendorDetail:
    repo = vendor_service.repository(session)
    vendor = await repo.get(ctx, "vendors.view", vendor_id)
    counts = await vendor_service.material_counts(session, [vendor.id])

    detail = VendorDetail.model_validate(vendor)
    detail = detail.model_copy(
        update={
            "contacts": [
                VendorContactRead.model_validate(contact)
                for contact in vendor.contacts
                if contact.deleted_at is None
            ],
            "material_count": counts.get(vendor.id, 0),
        }
    )

    accounts = await vendor_service.list_bank_accounts(session, vendor.id)
    primary = next((a for a in accounts if a.is_primary), None)

    if ctx.has("vendors.manage_bank_details"):
        return detail.model_copy(
            update={
                "bank_accounts": [VendorBankAccountRead.model_validate(a) for a in accounts],
                "primary_bank_masked": primary.masked_account if primary else None,
            }
        )
    # Without the permission: the existence of a payment destination is useful,
    # the number itself is not the caller's business.
    return detail.model_copy(
        update={
            "bank_accounts": None,
            "primary_bank_masked": primary.masked_account if primary else None,
        }
    )


@router.patch(
    "/{vendor_id}",
    response_model=VendorRead,
    dependencies=[require("vendors.update")],
    summary="Update vendor details",
)
async def update_vendor(
    vendor_id: UUID,
    payload: VendorUpdate,
    ctx: Access,
    uow: UowDep,
    if_match: Annotated[
        int | None, Header(alias="If-Match", description="The version you loaded")
    ] = None,
) -> VendorRead:
    changes = payload.model_dump(exclude_unset=True)
    if "address" in changes and payload.address is not None:
        changes["address"] = payload.address.model_dump()
    if "vendor_type" in changes and payload.vendor_type is not None:
        changes["vendor_type"] = payload.vendor_type.value

    vendor = await vendor_service.update_vendor(
        uow.session,
        ctx,
        vendor_id=vendor_id,
        changes=changes,
        expected_version=if_match,
    )
    return VendorRead.model_validate(vendor)


@router.post(
    "/{vendor_id}/approve",
    response_model=VendorRead,
    dependencies=[require("vendors.approve")],
    summary="Approve a vendor for trading",
)
async def approve_vendor(
    vendor_id: UUID, payload: VendorApprove, ctx: Access, uow: UowDep
) -> VendorRead:
    vendor = await vendor_service.approve_vendor(
        uow.session, ctx, vendor_id=vendor_id, note=payload.note
    )
    return VendorRead.model_validate(vendor)


@router.post(
    "/{vendor_id}/suspend",
    response_model=VendorRead,
    dependencies=[require("vendors.suspend")],
    summary="Suspend or blacklist a vendor",
)
async def suspend_vendor(
    vendor_id: UUID, payload: VendorSuspend, ctx: Access, uow: UowDep
) -> VendorRead:
    vendor = await vendor_service.suspend_vendor(
        uow.session,
        ctx,
        vendor_id=vendor_id,
        reason=payload.reason,
        blacklist=payload.blacklist,
    )
    return VendorRead.model_validate(vendor)


# -----------------------------------------------------------------------------
# Bank accounts — separate permission, separate audit action
# -----------------------------------------------------------------------------


@router.get(
    "/{vendor_id}/bank-accounts",
    response_model=list[VendorBankAccountRead],
    dependencies=[require("vendors.manage_bank_details")],
    summary="Vendor bank accounts",
)
async def list_bank_accounts(
    vendor_id: UUID, ctx: Access, session: SessionDep
) -> list[VendorBankAccountRead]:
    # Confirms scope before disclosing anything.
    await vendor_service.repository(session).get(ctx, "vendors.view", vendor_id)
    accounts = await vendor_service.list_bank_accounts(session, vendor_id)
    return [VendorBankAccountRead.model_validate(a) for a in accounts]


@router.post(
    "/{vendor_id}/bank-accounts",
    response_model=VendorBankAccountRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require("vendors.manage_bank_details")],
    summary="Add a bank account",
)
async def add_bank_account(
    vendor_id: UUID, payload: VendorBankAccountIn, ctx: Access, uow: UowDep
) -> VendorBankAccountRead:
    account = await vendor_service.add_bank_account(
        uow.session, ctx, vendor_id=vendor_id, payload=payload.model_dump()
    )
    return VendorBankAccountRead.model_validate(account)


@router.post(
    "/bank-accounts/{account_id}/verify",
    response_model=VendorBankAccountRead,
    dependencies=[require("vendors.manage_bank_details")],
    summary="Verify a bank account against a document",
)
async def verify_bank_account(
    account_id: UUID,
    ctx: Access,
    uow: UowDep,
    note: Annotated[str | None, Query(max_length=300)] = None,
) -> VendorBankAccountRead:
    account = await vendor_service.verify_bank_account(
        uow.session, ctx, account_id=account_id, note=note
    )
    return VendorBankAccountRead.model_validate(account)
