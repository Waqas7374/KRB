"""Purchase requests: raise, edit, submit for approval, cancel.

Approval is delegated entirely to the engine. This module supplies what the
engine asks for — the routing context, a content hash, and what to do to the
request when a decision lands — through `PurchaseRequestApprovals`, which is
registered with the engine at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, ValidationError, VersionConflictError
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope
from app.core.types import utcnow
from app.modules.approvals.services import engine as approvals
from app.modules.approvals.services import registry
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import company_service, document_lookup
from app.modules.procurement.domain.enums import PurchaseRequestStatus
from app.modules.procurement.models import PurchaseRequest, PurchaseRequestItem
from app.platform.numbering import DocumentType, next_number

DOC_TYPE = DocumentType.PURCHASE_REQUEST
PERM_VIEW = "procurement.pr.view"
PERM_CREATE = "procurement.pr.create"
PERM_SUBMIT = "procurement.pr.submit"
PERM_APPROVE = "procurement.pr.approve"

_CENT = Decimal("0.0001")
# A request cannot be raised against a project that has finished.
_CLOSED_PROJECT_STATUSES = frozenset({"COMPLETED", "CLOSED", "CANCELLED"})


def repository(session: AsyncSession) -> ScopedRepository[PurchaseRequest]:
    return ScopedRepository(
        session,
        PurchaseRequest,
        entity_name="Purchase request",
        sortable={
            "pr_number",
            "status",
            "priority",
            "required_date",
            "estimated_amount",
            "submitted_at",
            "created_at",
            "updated_at",
        },
        searchable=("pr_number", "justification"),
        default_sort="-created_at",
    )


@dataclass(frozen=True, slots=True)
class LineInput:
    material_id: UUID
    quantity: Decimal
    unit_id: UUID
    estimated_rate: Decimal | None
    description: str | None
    required_date: Any


@dataclass(frozen=True, slots=True)
class RequestInput:
    project_id: UUID
    site_id: UUID | None
    department_id: UUID | None
    cost_center_id: UUID | None
    phase_id: UUID | None
    required_date: Any
    priority: str
    justification: str
    items: list[LineInput]


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


async def _validate_place(
    session: AsyncSession, ctx: AccessContext, data: RequestInput
) -> document_lookup.DocumentPlace:
    try:
        place = await document_lookup.resolve_place(
            session,
            company_id=ctx.company_id,
            project_id=data.project_id,
            site_id=data.site_id,
            department_id=data.department_id,
            phase_id=data.phase_id,
            cost_center_id=data.cost_center_id,
        )
    except document_lookup.PlaceMismatchError as exc:
        raise ValidationError(
            str(exc), errors=[{"field": exc.field, "code": "invalid", "message": str(exc)}]
        ) from exc
    if place.project_status in _CLOSED_PROJECT_STATUSES:
        raise BusinessRuleError(
            "project_not_open",
            f"Project {place.project_code} is {place.project_status.lower()}; "
            "purchase requests can no longer be raised against it.",
        )
    # The author must be able to raise requests for this place (a site
    # manager for their own site, a project manager for their project).
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=ctx.company_id,
        project_id=data.project_id,
        site_id=data.site_id,
        department_id=data.department_id,
        entity="Project",
    )
    return place


async def _build_lines(
    session: AsyncSession, ctx: AccessContext, lines: list[LineInput]
) -> list[PurchaseRequestItem]:
    materials = await material_lookup.materials(
        session, company_id=ctx.company_id, material_ids={line.material_id for line in lines}
    )
    errors: list[dict[str, str]] = []
    items: list[PurchaseRequestItem] = []
    for index, line in enumerate(lines):
        info = materials.get(line.material_id)
        path = f"items.{index}"
        if info is None or not info.is_active:
            errors.append(
                {
                    "field": f"{path}.material_id",
                    "code": "invalid",
                    "message": "unknown or deactivated material",
                }
            )
            continue
        if not info.is_purchasable:
            errors.append(
                {
                    "field": f"{path}.material_id",
                    "code": "invalid",
                    "message": f"{info.sku} is not purchasable",
                }
            )
        if line.unit_id not in info.unit_ids:
            errors.append(
                {
                    "field": f"{path}.unit_id",
                    "code": "invalid",
                    "message": f"not a unit configured for {info.sku}",
                }
            )
        amount = (
            (line.quantity * line.estimated_rate).quantize(_CENT, rounding=ROUND_HALF_UP)
            if line.estimated_rate is not None
            else Decimal(0)
        )
        items.append(
            PurchaseRequestItem(
                line_no=index + 1,
                material_id=line.material_id,
                description=line.description,
                quantity=line.quantity,
                unit_id=line.unit_id,
                estimated_rate=line.estimated_rate,
                estimated_amount=amount,
                required_date=line.required_date,
                sourced_quantity=Decimal(0),
                created_by_id=ctx.user_id,
            )
        )
    if errors:
        raise ValidationError("Some lines are not valid.", errors=errors)
    return items


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------


async def create(session: AsyncSession, ctx: AccessContext, data: RequestInput) -> PurchaseRequest:
    await _validate_place(session, ctx, data)
    items = await _build_lines(session, ctx, data.items)
    number = await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DOC_TYPE,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )
    request = PurchaseRequest(
        company_id=ctx.company_id,
        pr_number=number,
        status=PurchaseRequestStatus.DRAFT.value,
        priority=data.priority,
        project_id=data.project_id,
        site_id=data.site_id,
        department_id=data.department_id,
        cost_center_id=data.cost_center_id,
        phase_id=data.phase_id,
        required_date=data.required_date,
        justification=data.justification.strip(),
        estimated_amount=sum((i.estimated_amount for i in items), Decimal(0)),
        requested_by_id=ctx.user_id,
        created_by_id=ctx.user_id,
        items=items,
    )
    session.add(request)
    await session.flush()
    return request


async def _get_for_update(
    session: AsyncSession, ctx: AccessContext, request_id: UUID
) -> PurchaseRequest:
    request = await repository(session).get_for_update(ctx, PERM_VIEW, request_id)
    # get_for_update disables eager loads; the lines are needed for hashing
    # and replacement, so load them explicitly.
    await session.refresh(request, attribute_names=["items"])
    return request


def _check_version(request: PurchaseRequest, expected: int | None) -> None:
    if expected is not None and request.version != expected:
        raise VersionConflictError(
            f"{request.pr_number} was changed by someone else (version {request.version}, "
            f"you had {expected}). Reload and try again."
        )


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    request_id: UUID,
    data: RequestInput,
    *,
    expected_version: int | None,
) -> PurchaseRequest:
    request = await _get_for_update(session, ctx, request_id)
    _check_version(request, expected_version)
    if not PurchaseRequestStatus(request.status).is_editable:
        raise BusinessRuleError(
            "purchase_request_locked",
            f"{request.pr_number} is {request.status.lower().replace('_', ' ')} and can no longer "
            "be edited."
            + (
                " Recall it from approval first."
                if request.status == PurchaseRequestStatus.PENDING_APPROVAL.value
                else ""
            ),
        )
    await _validate_place(session, ctx, data)
    items = await _build_lines(session, ctx, data.items)

    request.project_id = data.project_id
    request.site_id = data.site_id
    request.department_id = data.department_id
    request.cost_center_id = data.cost_center_id
    request.phase_id = data.phase_id
    request.required_date = data.required_date
    request.priority = data.priority
    request.justification = data.justification.strip()
    request.items.clear()
    await session.flush()  # delete old lines before inserting renumbered ones
    request.items.extend(items)
    request.estimated_amount = sum((i.estimated_amount for i in items), Decimal(0))
    request.updated_by_id = ctx.user_id
    request.version += 1
    await session.flush()
    return request


async def submit(
    session: AsyncSession, ctx: AccessContext, request_id: UUID, *, expected_version: int | None
) -> PurchaseRequest:
    request = await _get_for_update(session, ctx, request_id)
    _check_version(request, expected_version)
    assert_in_scope(
        ctx,
        PERM_SUBMIT,
        company_id=request.company_id,
        project_id=request.project_id,
        site_id=request.site_id,
        department_id=request.department_id,
        entity="Purchase request",
    )
    if not PurchaseRequestStatus(request.status).is_editable:
        raise BusinessRuleError(
            "purchase_request_not_submittable",
            f"{request.pr_number} is {request.status.lower().replace('_', ' ')}; only a draft, "
            "rejected or returned request can be submitted.",
        )
    unpriced = [item.line_no for item in request.items if item.estimated_rate is None]
    if unpriced:
        # The estimate decides who must approve. Without a rate on every line a
        # 5 000 000 request could be routed as if it cost nothing.
        raise BusinessRuleError(
            "purchase_request_unpriced",
            "Every line needs an estimated rate before submission, because the total decides "
            f"who must approve. Missing on line(s) {', '.join(map(str, unpriced))}.",
        )

    place = await document_lookup.resolve_place(
        session,
        company_id=request.company_id,
        project_id=request.project_id,
        site_id=request.site_id,
        department_id=request.department_id,
        phase_id=request.phase_id,
    )
    subject = approvals.ApprovalSubject(
        doc_type=DOC_TYPE,
        doc_id=request.id,
        company_id=request.company_id,
        initiated_by=ctx.user_id,
        context=_approval_context(request, place, ctx),
        document_hash=_hash(request),
        doc_number=request.pr_number,
        summary=f"{place.project_code}"
        + (f" / {place.site_code}" if place.site_code else "")
        + f": {request.justification[:200]}",
        amount=request.estimated_amount,
        currency_code=request.currency_code,
        link_path=f"/purchase-requests/{request.id}",
        project_id=request.project_id,
        site_id=request.site_id,
        department_id=request.department_id,
    )
    # One savepoint around "mark pending" and "route it": if routing is
    # refused (no workflow, nobody eligible), the request must stay a draft
    # rather than sit in PENDING_APPROVAL with no approval behind it. The
    # unit of work would roll the whole request back anyway; this makes the
    # service correct on its own instead of relying on its caller.
    async with session.begin_nested():
        request.status = PurchaseRequestStatus.PENDING_APPROVAL.value
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
) -> PurchaseRequest:
    request = await _get_for_update(session, ctx, request_id)
    allowed = {
        PurchaseRequestStatus.DRAFT.value,
        PurchaseRequestStatus.REJECTED.value,
        PurchaseRequestStatus.CHANGES_REQUESTED.value,
        PurchaseRequestStatus.APPROVED.value,
    }
    if request.status not in allowed:
        raise BusinessRuleError(
            "purchase_request_not_cancellable",
            f"{request.pr_number} is {request.status.lower().replace('_', ' ')} and cannot be "
            "cancelled."
            + (
                " Recall it from approval first."
                if request.status == PurchaseRequestStatus.PENDING_APPROVAL.value
                else ""
            ),
        )
    previous = request.status
    request.status = PurchaseRequestStatus.CANCELLED.value
    request.cancelled_at = utcnow()
    request.cancel_reason = reason.strip()
    request.version += 1
    await record_audit(
        session,
        action=AuditAction.CANCEL,
        entity_type="PurchaseRequest",
        entity_id=request.id,
        entity_label=request.pr_number,
        project_id=request.project_id,
        site_id=request.site_id,
        summary=f"{request.pr_number} cancelled ({previous} -> CANCELLED): {reason.strip()}",
    )
    return request


async def list_requests(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[PurchaseRequest], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, request_id: UUID) -> PurchaseRequest:
    return await repository(session).get(ctx, PERM_VIEW, request_id)


# -----------------------------------------------------------------------------
# Approval integration
# -----------------------------------------------------------------------------

CONTEXT_VARIABLES = frozenset(
    {
        "total_amount",
        "currency",
        "item_count",
        "max_line_amount",
        "priority",
        "is_urgent",
        "project.*",
        "site.*",
        "department.*",
        "phase.*",
        "requester.*",
    }
)


def _approval_context(
    request: PurchaseRequest, place: document_lookup.DocumentPlace, ctx: AccessContext
) -> dict[str, Any]:
    """The documented variables a workflow condition may use (docs/04 §1).

    Budget variables (has_budget, budget_remaining) and is_capex arrive with
    budgets in Phase 4; until then they are absent, and a condition that uses
    them simply does not match.
    """
    return {
        "total_amount": request.estimated_amount,
        "currency": request.currency_code,
        "item_count": len(request.items),
        "max_line_amount": max((i.estimated_amount for i in request.items), default=Decimal(0)),
        "priority": request.priority,
        "is_urgent": request.priority == "URGENT",
        "project": {"id": place.project_id, "code": place.project_code, "name": place.project_name},
        "site": {"id": place.site_id, "code": place.site_code},
        "department": {"id": place.department_id, "code": place.department_code},
        "phase": {"id": place.phase_id, "code": place.phase_code},
        "requester": {"id": ctx.user_id, "roles": sorted(ctx.role_codes)},
    }


def _hash(request: PurchaseRequest) -> str:
    return approvals.document_hash(
        {
            "project_id": request.project_id,
            "site_id": request.site_id,
            "department_id": request.department_id,
            "cost_center_id": request.cost_center_id,
            "phase_id": request.phase_id,
            "required_date": request.required_date,
            "priority": request.priority,
            "justification": request.justification,
            "currency": request.currency_code,
            "estimated_amount": request.estimated_amount,
            "items": [
                {
                    "material_id": i.material_id,
                    "quantity": i.quantity,
                    "unit_id": i.unit_id,
                    "estimated_rate": i.estimated_rate,
                    "required_date": i.required_date,
                    "description": i.description,
                }
                for i in sorted(request.items, key=lambda i: i.line_no)
            ],
        }
    )


class PurchaseRequestApprovals:
    spec = registry.DocumentTypeSpec(
        doc_type=DOC_TYPE,
        label="Purchase request",
        approve_permission=PERM_APPROVE,
        context_variables=CONTEXT_VARIABLES,
    )

    async def _load(self, session: AsyncSession, doc_id: UUID) -> PurchaseRequest:
        request = (
            await session.execute(select(PurchaseRequest).where(PurchaseRequest.id == doc_id))
        ).scalar_one()
        await session.refresh(request, attribute_names=["items"])
        return request

    async def current_hash(self, session: AsyncSession, doc_id: UUID) -> str | None:
        return _hash(await self._load(session, doc_id))

    async def pending_document_ids(self, session: AsyncSession) -> dict[UUID, UUID]:
        rows = await session.execute(
            select(PurchaseRequest.id, PurchaseRequest.company_id).where(
                PurchaseRequest.status == PurchaseRequestStatus.PENDING_APPROVAL.value
            )
        )
        return dict(rows.tuples().all())

    async def _set(
        self,
        session: AsyncSession,
        outcome: registry.ApprovalOutcome,
        status: PurchaseRequestStatus,
    ) -> None:
        request = await self._load(session, outcome.doc_id)
        request.status = status.value
        request.decision_reason = outcome.reason
        if status is PurchaseRequestStatus.APPROVED:
            request.approved_at = utcnow()
        request.version += 1

    async def on_approved(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._set(session, outcome, PurchaseRequestStatus.APPROVED)

    async def on_rejected(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._set(session, outcome, PurchaseRequestStatus.REJECTED)

    async def on_changes_requested(
        self, session: AsyncSession, outcome: registry.ApprovalOutcome
    ) -> None:
        await self._set(session, outcome, PurchaseRequestStatus.CHANGES_REQUESTED)

    async def on_recalled(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._set(session, outcome, PurchaseRequestStatus.DRAFT)


registry.register(PurchaseRequestApprovals())
