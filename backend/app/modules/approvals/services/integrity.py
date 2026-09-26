"""Nightly proof that approvals and the documents they govern still agree.

Approval and document status change in one transaction, so they should never
drift. This exists for the day they do — a manual database fix, a bug, a
half-applied restore — and turns silent drift into an alarm instead of a
request that sits "pending" forever (or a document approved by nobody).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.approvals.domain.enums import RequestStatus, StepStatus
from app.modules.approvals.models import (
    ApprovalRequest,
    ApprovalRequestStep,
    ApprovalStepApprover,
)
from app.modules.approvals.services import registry


@dataclass(frozen=True, slots=True)
class Finding:
    company_id: UUID
    kind: str
    doc_type: str | None
    doc_id: UUID | None
    request_id: UUID | None
    detail: str


async def find_inconsistencies(session: AsyncSession) -> list[Finding]:
    findings: list[Finding] = []
    pending = RequestStatus.PENDING.value

    # 1. A pending request with no pending step can never finish.
    rows = await session.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.status == pending,
            ~select(ApprovalRequestStep.id)
            .where(
                ApprovalRequestStep.request_id == ApprovalRequest.id,
                ApprovalRequestStep.status == StepStatus.PENDING.value,
            )
            .exists(),
        )
        .order_by(ApprovalRequest.submitted_at)
    )
    for request in rows.scalars():
        findings.append(
            Finding(
                request.company_id,
                "request_without_active_step",
                request.doc_type,
                request.doc_id,
                request.id,
                f"{request.doc_number or request.doc_type} is pending but no step is "
                "waiting for anyone.",
            )
        )

    # 2. A pending step whose request has ended is a ghost in someone's inbox.
    rows = await session.execute(
        select(ApprovalRequest, ApprovalRequestStep)
        .join(ApprovalRequestStep, ApprovalRequestStep.request_id == ApprovalRequest.id)
        .where(
            ApprovalRequestStep.status == StepStatus.PENDING.value,
            ApprovalRequest.status != pending,
        )
    )
    for request, step in rows.all():
        findings.append(
            Finding(
                request.company_id,
                "step_pending_on_ended_request",
                request.doc_type,
                request.doc_id,
                request.id,
                f"Step {step.step_no} of {request.doc_number or request.doc_type} is pending "
                f"although the request is {request.status.lower()}.",
            )
        )

    # 3. A pending step nobody may decide.
    rows = await session.execute(
        select(ApprovalRequest, ApprovalRequestStep)
        .join(ApprovalRequestStep, ApprovalRequestStep.request_id == ApprovalRequest.id)
        .where(
            ApprovalRequestStep.status == StepStatus.PENDING.value,
            ApprovalRequest.status == pending,
            select(func.count())
            .select_from(ApprovalStepApprover)
            .where(ApprovalStepApprover.request_step_id == ApprovalRequestStep.id)
            .scalar_subquery()
            == 0,
        )
    )
    for request, step in rows.all():
        findings.append(
            Finding(
                request.company_id,
                "step_without_approvers",
                request.doc_type,
                request.doc_id,
                request.id,
                f"Step {step.step_no} ({step.name}) of {request.doc_number or request.doc_type} "
                "has no one who can decide it.",
            )
        )

    # 4. The document and its request must agree about being "pending".
    for spec in registry.specs():
        handler = registry.get(spec.doc_type)
        pending_docs = getattr(handler, "pending_document_ids", None)
        if pending_docs is None:
            continue
        in_approval: dict[UUID, UUID] = await pending_docs(session)  # doc id -> company id
        rows = await session.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.doc_type == spec.doc_type, ApprovalRequest.status == pending
            )
        )
        requests = {r.doc_id: r for r in rows.scalars()}
        for doc_id in sorted(in_approval.keys() - requests.keys(), key=str):
            findings.append(
                Finding(
                    in_approval[doc_id],
                    "document_pending_without_request",
                    spec.doc_type,
                    doc_id,
                    None,
                    f"A {spec.label.lower()} is marked pending approval but has no "
                    "pending request.",
                )
            )
        for doc_id, request in requests.items():
            if doc_id not in in_approval:
                findings.append(
                    Finding(
                        request.company_id,
                        "request_pending_on_settled_document",
                        spec.doc_type,
                        doc_id,
                        request.id,
                        f"{request.doc_number or spec.label} has a pending approval request "
                        "although the document is no longer awaiting approval.",
                    )
                )
    return findings
