"""Payment requests: raise, edit, submit for approval, cancel.

Deliberately blind to which invoice(s) a request will settle — docs/02 §8
gives `payment_requests` no invoice reference at all, only a vendor and an
amount. Allocation to specific invoices happens later, when the `Payment`
this request's approval eventually authorises is created and allocated
(`payments.py`). This is what makes an advance (`is_advance`) simply a
request with nothing behind it yet, not a special code path.

Approval is delegated entirely to the engine, the same shape as a purchase
request: this module supplies the routing context, a content hash, and what
to do to the request when a decision lands, through
`PaymentRequestApprovals`, registered with the engine at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, PermissionDeniedError, VersionConflictError
from app.core.pagination import PageParams
from app.core.types import utcnow
from app.modules.approvals.services import engine as approvals
from app.modules.approvals.services import registry
from app.modules.finance.domain.enums import PaymentRequestStatus
from app.modules.finance.models import PaymentRequest
from app.modules.org.services import company_service
from app.modules.vendors.services import vendor_lookup
from app.platform.numbering import DocumentType, next_number

DOC_TYPE = DocumentType.PAYMENT_REQUEST
PERM_VIEW = "finance.payment.view"
PERM_CREATE = "finance.payment.request"
PERM_APPROVE = "finance.payment.approve"


def repository(session: AsyncSession) -> ScopedRepository[PaymentRequest]:
    return ScopedRepository(
        session,
        PaymentRequest,
        entity_name="Payment request",
        sortable={"request_number", "status", "priority", "amount", "created_at", "updated_at"},
        searchable=("request_number", "reason"),
        default_sort="-created_at",
    )


@dataclass(frozen=True, slots=True)
class RequestInput:
    vendor_id: UUID
    amount: Decimal
    reason: str
    priority: str = "NORMAL"
    is_advance: bool = False
    currency_code: str = "PKR"


async def _validate_vendor(session: AsyncSession, ctx: AccessContext, vendor_id: UUID) -> str:
    vendor = (
        await vendor_lookup.vendors(session, company_id=ctx.company_id, vendor_ids={vendor_id})
    ).get(vendor_id)
    if vendor is None:
        raise BusinessRuleError("vendor_unknown", "That vendor could not be found.")
    return vendor.name


async def create(session: AsyncSession, ctx: AccessContext, data: RequestInput) -> PaymentRequest:
    if not ctx.has(PERM_CREATE):
        raise PermissionDeniedError(PERM_CREATE)
    if data.amount <= 0:
        raise BusinessRuleError("payment_request_amount", "The amount must be more than zero.")
    await _validate_vendor(session, ctx, data.vendor_id)
    number = await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DOC_TYPE,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )
    request = PaymentRequest(
        company_id=ctx.company_id,
        request_number=number,
        vendor_id=data.vendor_id,
        amount=data.amount,
        currency_code=data.currency_code,
        priority=data.priority,
        reason=data.reason.strip(),
        is_advance=data.is_advance,
        status=PaymentRequestStatus.DRAFT.value,
        requested_by_id=ctx.user_id,
        created_by_id=ctx.user_id,
    )
    session.add(request)
    await session.flush()
    return request


async def _get_for_update(
    session: AsyncSession, ctx: AccessContext, request_id: UUID, permission: str
) -> PaymentRequest:
    return await repository(session).get_for_update(ctx, permission, request_id)


def _check_version(request: PaymentRequest, expected: int | None) -> None:
    if expected is not None and request.version != expected:
        raise VersionConflictError(
            f"{request.request_number} was changed by someone else (version {request.version}, "
            f"you had {expected}). Reload and try again."
        )


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    request_id: UUID,
    data: RequestInput,
    *,
    expected_version: int | None,
) -> PaymentRequest:
    request = await _get_for_update(session, ctx, request_id, PERM_CREATE)
    _check_version(request, expected_version)
    if not PaymentRequestStatus(request.status).is_editable:
        raise BusinessRuleError(
            "payment_request_locked",
            f"{request.request_number} is {request.status.lower().replace('_', ' ')} and can no "
            "longer be edited."
            + (
                " Recall it from approval first."
                if request.status == PaymentRequestStatus.PENDING_APPROVAL.value
                else ""
            ),
        )
    if data.amount <= 0:
        raise BusinessRuleError("payment_request_amount", "The amount must be more than zero.")
    await _validate_vendor(session, ctx, data.vendor_id)
    request.vendor_id = data.vendor_id
    request.amount = data.amount
    request.currency_code = data.currency_code
    request.priority = data.priority
    request.reason = data.reason.strip()
    request.is_advance = data.is_advance
    request.decision_reason = None
    request.updated_by_id = ctx.user_id
    request.version += 1
    await session.flush()
    return request


async def delete_draft(session: AsyncSession, ctx: AccessContext, request_id: UUID) -> None:
    request = await _get_for_update(session, ctx, request_id, PERM_CREATE)
    if request.status != PaymentRequestStatus.DRAFT.value:
        raise BusinessRuleError(
            "payment_request_not_draft",
            f"{request.request_number} is not a draft; it cannot be deleted.",
        )
    await session.delete(request)
    await session.flush()


async def submit(
    session: AsyncSession, ctx: AccessContext, request_id: UUID, *, expected_version: int | None
) -> PaymentRequest:
    request = await _get_for_update(session, ctx, request_id, PERM_CREATE)
    _check_version(request, expected_version)
    if not PaymentRequestStatus(request.status).is_editable:
        raise BusinessRuleError(
            "payment_request_not_submittable",
            f"{request.request_number} is {request.status.lower().replace('_', ' ')}; only a "
            "draft, rejected or returned request can be submitted.",
        )
    vendor_name = await _validate_vendor(session, ctx, request.vendor_id)
    subject = approvals.ApprovalSubject(
        doc_type=DOC_TYPE,
        doc_id=request.id,
        company_id=request.company_id,
        initiated_by=ctx.user_id,
        context=_approval_context(request, ctx),
        document_hash=_hash(request),
        doc_number=request.request_number,
        summary=f"{vendor_name}: {request.reason[:200]}",
        amount=request.amount,
        currency_code=request.currency_code,
        link_path=f"/finance/payment-requests/{request.id}",
    )
    # Same reasoning as purchase_requests.submit: if routing is refused, the
    # request must stay a draft rather than sit in PENDING_APPROVAL with
    # nothing behind it.
    async with session.begin_nested():
        request.status = PaymentRequestStatus.PENDING_APPROVAL.value
        request.submitted_at = utcnow()
        request.decision_reason = None
        request.version += 1
        await session.flush()
        approval = await approvals.submit(session, subject)
        request.approval_request_id = approval.id
        await session.flush()
    return request


async def cancel(
    session: AsyncSession, ctx: AccessContext, request_id: UUID, reason: str
) -> PaymentRequest:
    request = await _get_for_update(session, ctx, request_id, PERM_CREATE)
    allowed = {
        PaymentRequestStatus.DRAFT.value,
        PaymentRequestStatus.REJECTED.value,
        PaymentRequestStatus.CHANGES_REQUESTED.value,
        PaymentRequestStatus.APPROVED.value,
    }
    if request.status not in allowed:
        raise BusinessRuleError(
            "payment_request_not_cancellable",
            f"{request.request_number} is {request.status.lower().replace('_', ' ')} and cannot "
            "be cancelled."
            + (
                " Recall it from approval first."
                if request.status == PaymentRequestStatus.PENDING_APPROVAL.value
                else ""
            ),
        )
    request.status = PaymentRequestStatus.CANCELLED.value
    request.cancelled_at = utcnow()
    request.cancel_reason = reason.strip()
    request.updated_by_id = ctx.user_id
    request.version += 1
    await session.flush()
    return request


async def list_requests(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[PaymentRequest], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, request_id: UUID) -> PaymentRequest:
    return await repository(session).get(ctx, PERM_VIEW, request_id)


# -----------------------------------------------------------------------------
# Approval integration
# -----------------------------------------------------------------------------

CONTEXT_VARIABLES = frozenset(
    {"amount", "currency", "vendor.*", "method", "is_advance", "priority", "requester.*"}
)


def _approval_context(request: PaymentRequest, ctx: AccessContext) -> dict[str, Any]:
    """docs/04 §1's `payment` context. `method` is not yet known at request
    time (that is decided when the payment is actually executed) — always
    None here, present only so a workflow condition that names it fails to
    match rather than fails to save."""
    return {
        "amount": request.amount,
        "currency": request.currency_code,
        "vendor": {"id": request.vendor_id},
        "method": None,
        "is_advance": request.is_advance,
        "priority": request.priority,
        "requester": {"id": ctx.user_id, "roles": sorted(ctx.role_codes)},
    }


def _hash(request: PaymentRequest) -> str:
    return approvals.document_hash(
        {
            "vendor_id": request.vendor_id,
            "amount": request.amount,
            "currency": request.currency_code,
            "priority": request.priority,
            "reason": request.reason,
            "is_advance": request.is_advance,
        }
    )


class PaymentRequestApprovals:
    spec = registry.DocumentTypeSpec(
        doc_type=DOC_TYPE,
        label="Payment request",
        approve_permission=PERM_APPROVE,
        context_variables=CONTEXT_VARIABLES,
    )

    async def _load(self, session: AsyncSession, doc_id: UUID) -> PaymentRequest:
        return (
            await session.execute(select(PaymentRequest).where(PaymentRequest.id == doc_id))
        ).scalar_one()

    async def current_hash(self, session: AsyncSession, doc_id: UUID) -> str | None:
        return _hash(await self._load(session, doc_id))

    async def pending_document_ids(self, session: AsyncSession) -> dict[UUID, UUID]:
        rows = await session.execute(
            select(PaymentRequest.id, PaymentRequest.company_id).where(
                PaymentRequest.status == PaymentRequestStatus.PENDING_APPROVAL.value
            )
        )
        return dict(rows.tuples().all())

    async def _set(
        self,
        session: AsyncSession,
        outcome: registry.ApprovalOutcome,
        status: PaymentRequestStatus,
    ) -> None:
        request = await self._load(session, outcome.doc_id)
        request.status = status.value
        request.decision_reason = outcome.reason
        if status is PaymentRequestStatus.APPROVED:
            request.approved_at = utcnow()
        request.version += 1

    async def on_approved(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._set(session, outcome, PaymentRequestStatus.APPROVED)

    async def on_rejected(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._set(session, outcome, PaymentRequestStatus.REJECTED)

    async def on_changes_requested(
        self, session: AsyncSession, outcome: registry.ApprovalOutcome
    ) -> None:
        await self._set(session, outcome, PaymentRequestStatus.CHANGES_REQUESTED)

    async def on_recalled(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._set(session, outcome, PaymentRequestStatus.DRAFT)


registry.register(PaymentRequestApprovals())
