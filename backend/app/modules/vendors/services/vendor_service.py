"""Vendor use-cases.

The state machine is deliberately narrow: creating a vendor does not make it
tradeable. DRAFT -> ACTIVE requires `vendors.approve`, because the person who
enters a supplier's details and the person who authorises trading with them
should not have to be the same person.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, NotFoundError, StateTransitionError
from app.core.logging import get_logger
from app.core.types import utcnow, uuid7
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.vendors.domain.enums import VendorStatus
from app.modules.vendors.models import (
    Vendor,
    VendorBankAccount,
    VendorContact,
    VendorMaterial,
)
from app.platform import outbox
from app.platform.numbering import DocumentType, next_number

log = get_logger("vendors")

# Which transitions are legal. Anything absent is refused with a clear message
# rather than silently applied.
ALLOWED_TRANSITIONS: dict[VendorStatus, frozenset[VendorStatus]] = {
    VendorStatus.DRAFT: frozenset({VendorStatus.PENDING_APPROVAL, VendorStatus.ACTIVE}),
    VendorStatus.PENDING_APPROVAL: frozenset({VendorStatus.ACTIVE, VendorStatus.DRAFT}),
    VendorStatus.ACTIVE: frozenset(
        {VendorStatus.SUSPENDED, VendorStatus.BLACKLISTED, VendorStatus.INACTIVE}
    ),
    VendorStatus.SUSPENDED: frozenset({VendorStatus.ACTIVE, VendorStatus.BLACKLISTED}),
    # Terminal: a blacklisted vendor is reinstated by an explicit new decision,
    # not by an edit.
    VendorStatus.BLACKLISTED: frozenset(),
    VendorStatus.INACTIVE: frozenset({VendorStatus.ACTIVE}),
}


async def get_creator_id(session: AsyncSession, vendor_id: UUID) -> UUID | None:
    """Who created this vendor, for the outbox handler that notifies them of
    a status change. A narrow lookup rather than the full repository, so a
    background worker does not need an `AccessContext` to use it."""
    return await session.scalar(select(Vendor.created_by_id).where(Vendor.id == vendor_id))


def repository(session: AsyncSession) -> ScopedRepository[Vendor]:
    return ScopedRepository(
        session,
        Vendor,
        entity_name="Vendor",
        sortable={
            "code",
            "legal_name",
            "trade_name",
            "vendor_type",
            "status",
            "payment_terms_days",
            "created_at",
            "updated_at",
        },
        searchable=("code", "legal_name", "trade_name", "ntn", "phone", "email"),
        default_sort="legal_name",
    )


async def create_vendor(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    payload: dict[str, Any],
    contacts: list[dict[str, Any]],
    material_ids: list[UUID],
) -> Vendor:
    repo = repository(session)

    code = payload.pop("code", None)
    if code:
        await repo.assert_code_available(ctx.company_id, "code", code)
    else:
        code = await next_number(session, company_id=ctx.company_id, doc_type=DocumentType.VENDOR)

    vendor = Vendor(
        id=uuid7(),
        company_id=ctx.company_id,
        code=code,
        # A new vendor is not tradeable until approved.
        status=VendorStatus.DRAFT.value,
        **payload,
    )
    session.add(vendor)
    await session.flush()

    for contact in contacts:
        session.add(
            VendorContact(id=uuid7(), company_id=ctx.company_id, vendor_id=vendor.id, **contact)
        )

    await _set_materials(session, ctx, vendor, material_ids)

    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="vendor.created",
            aggregate_type="Vendor",
            aggregate_id=vendor.id,
            payload={"code": vendor.code, "legal_name": vendor.legal_name},
            company_id=ctx.company_id,
        ),
    )
    log.info("vendor.created", vendor_id=str(vendor.id), code=vendor.code)
    return vendor


async def update_vendor(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    vendor_id: UUID,
    changes: dict[str, Any],
    expected_version: int | None = None,
) -> Vendor:
    repo = repository(session)
    vendor = await repo.get_for_update(ctx, "vendors.view", vendor_id)

    if vendor.status == VendorStatus.BLACKLISTED.value:
        raise BusinessRuleError(
            "vendor_blacklisted",
            "A blacklisted vendor cannot be edited. Reinstate it first.",
        )

    repo.apply_update(vendor, changes, expected_version=expected_version)
    return vendor


async def approve_vendor(
    session: AsyncSession, ctx: AccessContext, *, vendor_id: UUID, note: str | None
) -> Vendor:
    """Make a vendor tradeable."""
    repo = repository(session)
    vendor = await repo.get_for_update(ctx, "vendors.view", vendor_id)

    _assert_transition(vendor, VendorStatus.ACTIVE)

    # Refusing to approve an unidentifiable supplier here as well as in the
    # schema: a vendor created before this rule existed must not slip through.
    if not vendor.ntn and not vendor.cnic:
        raise BusinessRuleError(
            "vendor_tax_identity_missing",
            "This vendor has neither an NTN nor a CNIC. Withholding tax cannot be "
            "computed, so it cannot be approved for trading.",
        )

    previous = vendor.status
    vendor.status = VendorStatus.ACTIVE.value
    vendor.approved_at = utcnow()
    vendor.approved_by_id = ctx.user_id
    vendor.suspended_at = None
    vendor.suspension_reason = None
    vendor.version += 1

    await record_audit(
        session,
        action=AuditAction.APPROVE,
        entity_type="Vendor",
        entity_id=vendor.id,
        entity_label=f"{vendor.code} — {vendor.display_name}",
        company_id=ctx.company_id,
        summary=f"Vendor approved for trading ({previous} -> ACTIVE)"
        + (f": {note}" if note else ""),
        old_values={"status": previous},
        new_values={"status": VendorStatus.ACTIVE.value},
    )
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="vendor.approved",
            aggregate_type="Vendor",
            aggregate_id=vendor.id,
            payload={"code": vendor.code},
            company_id=ctx.company_id,
        ),
    )
    return vendor


async def suspend_vendor(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    vendor_id: UUID,
    reason: str,
    blacklist: bool = False,
) -> Vendor:
    repo = repository(session)
    vendor = await repo.get_for_update(ctx, "vendors.view", vendor_id)

    target = VendorStatus.BLACKLISTED if blacklist else VendorStatus.SUSPENDED
    _assert_transition(vendor, target)

    previous = vendor.status
    vendor.status = target.value
    vendor.suspended_at = utcnow()
    vendor.suspension_reason = reason
    vendor.version += 1

    await record_audit(
        session,
        action=AuditAction.CANCEL if blacklist else AuditAction.UPDATE,
        entity_type="Vendor",
        entity_id=vendor.id,
        entity_label=f"{vendor.code} — {vendor.display_name}",
        company_id=ctx.company_id,
        summary=f"Vendor {target.value.lower()} ({previous} -> {target.value}): {reason}",
        old_values={"status": previous},
        new_values={"status": target.value, "suspension_reason": reason},
    )
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="vendor.suspended" if not blacklist else "vendor.blacklisted",
            aggregate_type="Vendor",
            aggregate_id=vendor.id,
            payload={"code": vendor.code, "reason": reason},
            company_id=ctx.company_id,
        ),
    )
    return vendor


def _assert_transition(vendor: Vendor, target: VendorStatus) -> None:
    current = VendorStatus(vendor.status)
    if current == target:
        raise StateTransitionError("Vendor", current.value, target.value)
    if target not in ALLOWED_TRANSITIONS[current]:
        raise StateTransitionError("Vendor", current.value, target.value)


# -----------------------------------------------------------------------------
# Materials supplied
# -----------------------------------------------------------------------------


async def _set_materials(
    session: AsyncSession, ctx: AccessContext, vendor: Vendor, material_ids: list[UUID]
) -> None:
    if not material_ids:
        return
    existing = {
        row.material_id
        for row in (
            await session.execute(
                select(VendorMaterial).where(VendorMaterial.vendor_id == vendor.id)
            )
        )
        .scalars()
        .all()
    }
    for material_id in material_ids:
        if material_id in existing:
            continue
        session.add(
            VendorMaterial(
                id=uuid7(),
                company_id=ctx.company_id,
                vendor_id=vendor.id,
                material_id=material_id,
            )
        )


async def material_counts(session: AsyncSession, vendor_ids: list[UUID]) -> dict[UUID, int]:
    """How many materials each vendor supplies, for the list view.

    One grouped query rather than N+1 — a vendor list of 50 rows should be two
    queries, not fifty-one.
    """
    if not vendor_ids:
        return {}
    rows = (
        await session.execute(
            select(VendorMaterial.vendor_id, func.count())
            .where(
                VendorMaterial.vendor_id.in_(vendor_ids),
                VendorMaterial.deleted_at.is_(None),
            )
            .group_by(VendorMaterial.vendor_id)
        )
    ).tuples()
    return dict(rows.all())


# -----------------------------------------------------------------------------
# Bank accounts
# -----------------------------------------------------------------------------


async def list_bank_accounts(session: AsyncSession, vendor_id: UUID) -> list[VendorBankAccount]:
    return list(
        (
            await session.execute(
                select(VendorBankAccount)
                .where(
                    VendorBankAccount.vendor_id == vendor_id,
                    VendorBankAccount.deleted_at.is_(None),
                )
                .order_by(VendorBankAccount.is_primary.desc(), VendorBankAccount.created_at)
            )
        )
        .scalars()
        .all()
    )


async def add_bank_account(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    vendor_id: UUID,
    payload: dict[str, Any],
) -> VendorBankAccount:
    """Add a payment destination.

    Always audited with masked values, because this is the highest-value fraud
    surface in procurement: change the account, and the next payment run sends
    the money elsewhere.
    """
    vendor = await repository(session).get(ctx, "vendors.view", vendor_id)

    if payload.get("is_primary"):
        for existing in await list_bank_accounts(session, vendor_id):
            if existing.is_primary:
                existing.is_primary = False

    account = VendorBankAccount(
        id=uuid7(), company_id=ctx.company_id, vendor_id=vendor_id, **payload
    )
    session.add(account)
    await session.flush()

    await record_audit(
        session,
        action=AuditAction.BANK_DETAILS_CHANGE,
        entity_type="VendorBankAccount",
        entity_id=account.id,
        entity_label=f"{vendor.code} — {account.bank_name}",
        company_id=ctx.company_id,
        summary=(
            f"Bank account added for {vendor.display_name}: "
            f"{account.bank_name} {account.masked_account}"
            + (" (primary)" if account.is_primary else "")
        ),
        new_values={
            "bank_name": account.bank_name,
            "account": account.masked_account,
            "is_primary": account.is_primary,
        },
    )
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type="vendor.bank_account_changed",
            aggregate_type="Vendor",
            aggregate_id=vendor_id,
            payload={"vendor_code": vendor.code, "account_id": str(account.id)},
            company_id=ctx.company_id,
        ),
    )
    log.warning("vendor.bank_account_added", vendor_id=str(vendor_id), account_id=str(account.id))
    return account


async def verify_bank_account(
    session: AsyncSession, ctx: AccessContext, *, account_id: UUID, note: str | None
) -> VendorBankAccount:
    """Record that a human checked the details against a document."""
    account = (
        await session.execute(
            select(VendorBankAccount).where(
                VendorBankAccount.id == account_id,
                VendorBankAccount.company_id == ctx.company_id,
                VendorBankAccount.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise NotFoundError("Bank account", account_id)

    if account.created_by_id == ctx.user_id:
        raise BusinessRuleError(
            "self_verification",
            "Bank details must be verified by someone other than the person who entered them.",
        )

    account.verified_at = utcnow()
    account.verified_by_id = ctx.user_id
    account.verification_note = note

    await record_audit(
        session,
        action=AuditAction.BANK_DETAILS_CHANGE,
        entity_type="VendorBankAccount",
        entity_id=account.id,
        entity_label=f"{account.bank_name} {account.masked_account}",
        company_id=ctx.company_id,
        summary=f"Bank account verified: {account.bank_name} {account.masked_account}"
        + (f" — {note}" if note else ""),
        new_values={"verified": True},
    )
    return account
