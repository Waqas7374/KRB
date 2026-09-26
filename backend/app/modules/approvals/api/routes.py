"""Approval endpoints: workflow configuration, the inbox, decisions, trails."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require, require_any
from app.core.access import AccessContext
from app.core.pagination import Page
from app.core.types import utcnow
from app.modules.approvals.domain.enums import ActionType, RequestStatus, StepStatus
from app.modules.approvals.models import ApprovalRequest
from app.modules.approvals.schemas import (
    ActionRead,
    ApproverRead,
    CommentRequest,
    DecisionRequest,
    DecisionResponse,
    DocumentTypeRead,
    InboxItemRead,
    RequestRead,
    RequestStepRead,
    SimulatedStep,
    SimulateRequest,
    SimulateResponse,
    WorkflowRead,
    WorkflowSave,
)
from app.modules.approvals.services import engine, registry, workflow_service
from app.modules.identity.services import user_lookup

router = APIRouter()

_VIEW_WORKFLOWS = require_any("approvals.view", "settings.manage_workflows")
_MANAGE_WORKFLOWS = require("settings.manage_workflows")


# -----------------------------------------------------------------------------
# Serialisation
# -----------------------------------------------------------------------------


async def _request_views(
    session: AsyncSession, ctx: AccessContext, requests: list[ApprovalRequest]
) -> list[RequestRead]:
    actions = await engine.actions_for(session, [r.id for r in requests])
    people_ids = {r.initiated_by_id for r in requests if r.initiated_by_id}
    for r in requests:
        for step in r.steps:
            people_ids |= {a.user_id for a in step.approvers}
    people = await user_lookup.people(session, company_id=ctx.company_id, user_ids=people_ids)
    now = utcnow()

    views: list[RequestRead] = []
    for r in requests:
        mine = [a for a in actions if a.request_id == r.id]
        approved_by = {
            (a.request_step_id, a.actor_user_id)
            for a in mine
            if a.action == ActionType.APPROVE.value
        }
        any_approval = any(a.action == ActionType.APPROVE.value for a in mine)
        steps: list[RequestStepRead] = []
        can_decide = False
        for step in r.steps:
            is_current = (
                r.status == RequestStatus.PENDING.value and step.step_no == r.current_step_no
            )
            if is_current and any(
                a.user_id == ctx.user_id and (step.id, ctx.user_id) not in approved_by
                for a in step.approvers
            ):
                can_decide = True
            steps.append(
                RequestStepRead(
                    step_no=step.step_no,
                    name=step.name,
                    approver_type=step.approver_type,
                    approver_ref=step.approver_ref,
                    quorum_type=step.quorum_type,
                    quorum_required=step.quorum_required,
                    approvals_count=step.approvals_count,
                    sla_hours=step.sla_hours,
                    status=step.status,
                    activated_at=step.activated_at,
                    due_at=step.due_at,
                    escalated_at=step.escalated_at,
                    decided_at=step.decided_at,
                    is_overdue=bool(
                        step.status == StepStatus.PENDING.value
                        and step.due_at
                        and step.due_at < now
                    ),
                    approvers=[
                        ApproverRead(
                            user_id=a.user_id,
                            full_name=people[a.user_id].full_name if a.user_id in people else None,
                            source=a.source,
                            has_approved=(step.id, a.user_id) in approved_by,
                        )
                        for a in step.approvers
                    ],
                )
            )
        initiator = people.get(r.initiated_by_id) if r.initiated_by_id else None
        blocked = await engine.limit_block_reason(session, r, ctx.user_id) if can_decide else None
        views.append(
            RequestRead(
                id=r.id,
                doc_type=r.doc_type,
                doc_id=r.doc_id,
                doc_number=r.doc_number,
                doc_summary=r.doc_summary,
                amount=r.amount,
                currency_code=r.currency_code,
                link_path=r.link_path,
                status=r.status,
                current_step_no=r.current_step_no,
                workflow_name=r.workflow_snapshot.get("workflow_name", ""),
                workflow_version=r.workflow_version,
                rule_name=r.workflow_snapshot.get("rule", {}).get("name", ""),
                initiated_by_id=r.initiated_by_id,
                initiated_by_name=initiator.full_name if initiator else None,
                submitted_at=r.submitted_at,
                completed_at=r.completed_at,
                outcome_reason=r.outcome_reason,
                can_decide=can_decide,
                decision_blocked_reason=blocked,
                can_recall=(
                    r.status == RequestStatus.PENDING.value
                    and r.initiated_by_id == ctx.user_id
                    and not any_approval
                ),
                steps=steps,
                actions=[ActionRead.model_validate(a) for a in mine],
            )
        )
    return views


async def _one(session: AsyncSession, ctx: AccessContext, request_id: UUID) -> RequestRead:
    request = await engine.get_visible(session, ctx, request_id)
    return (await _request_views(session, ctx, [request]))[0]


# -----------------------------------------------------------------------------
# Workflow configuration
# -----------------------------------------------------------------------------


@router.get(
    "/approval-workflows/document-types",
    response_model=list[DocumentTypeRead],
    dependencies=[_VIEW_WORKFLOWS],
    summary="Document types that route through approvals, with their context variables",
)
async def document_types() -> list[DocumentTypeRead]:
    return [
        DocumentTypeRead(
            doc_type=s.doc_type,
            label=s.label,
            approve_permission=s.approve_permission,
            context_variables=sorted(s.context_variables),
        )
        for s in registry.specs()
    ]


@router.get(
    "/approval-workflows",
    response_model=list[WorkflowRead],
    dependencies=[_VIEW_WORKFLOWS],
    summary="Approval workflows (active versions by default)",
)
async def list_workflows(
    ctx: Access,
    session: SessionDep,
    doc_type: str | None = None,
    include_inactive: bool = False,
) -> list[WorkflowRead]:
    rows = await workflow_service.list_workflows(
        session, ctx, doc_type=doc_type, include_inactive=include_inactive
    )
    return [WorkflowRead.model_validate(r) for r in rows]


@router.post(
    "/approval-workflows",
    response_model=WorkflowRead,
    status_code=201,
    dependencies=[_MANAGE_WORKFLOWS],
    summary="Publish a new workflow version (in-flight requests keep theirs)",
)
async def save_workflow(payload: WorkflowSave, ctx: Access, uow: UowDep) -> WorkflowRead:
    row = await workflow_service.save_version(
        uow.session,
        ctx,
        doc_type=payload.doc_type,
        name=payload.name,
        description=payload.description,
        definition=payload.definition,
        scope_type=payload.scope_type.value,
        scope_id=payload.scope_id,
        change_note=payload.change_note,
    )
    return WorkflowRead.model_validate(row)


@router.get(
    "/approval-workflows/{workflow_id}",
    response_model=WorkflowRead,
    dependencies=[_VIEW_WORKFLOWS],
)
async def get_workflow(workflow_id: UUID, ctx: Access, session: SessionDep) -> WorkflowRead:
    return WorkflowRead.model_validate(await workflow_service.get(session, ctx, workflow_id))


@router.post(
    "/approval-workflows/{workflow_id}/deactivate",
    response_model=WorkflowRead,
    dependencies=[_MANAGE_WORKFLOWS],
)
async def deactivate_workflow(workflow_id: UUID, ctx: Access, uow: UowDep) -> WorkflowRead:
    return WorkflowRead.model_validate(
        await workflow_service.deactivate(uow.session, ctx, workflow_id)
    )


@router.post(
    "/approval-workflows/simulate",
    response_model=SimulateResponse,
    dependencies=[_VIEW_WORKFLOWS],
    summary="Which rule and step chain a document with this context would be routed through",
)
async def simulate(payload: SimulateRequest, ctx: Access, session: SessionDep) -> SimulateResponse:
    result = await workflow_service.simulate(
        session,
        ctx,
        doc_type=payload.doc_type,
        context=payload.context,
        project_id=payload.project_id,
    )
    return SimulateResponse(
        workflow_id=result.workflow.id,
        workflow_name=result.workflow.name,
        version=result.workflow.version,
        rule_sequence=result.rule.sequence,
        rule_name=result.rule.name,
        steps=[
            SimulatedStep(
                step_no=s.step_no,
                name=s.name,
                approver_type=s.approver_type,
                approver_ref=s.approver_ref or (",".join(s.approver_refs) or None),
                quorum_type=s.quorum_type,
                sla_hours=s.sla_hours,
            )
            for s in result.steps
        ],
    )


# -----------------------------------------------------------------------------
# Inbox and requests
# -----------------------------------------------------------------------------


@router.get(
    "/approvals/inbox",
    response_model=Page[InboxItemRead],
    summary="Every pending step you can decide, most urgent first",
)
async def inbox(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    doc_type: Annotated[str | None, Query()] = None,
) -> Page[InboxItemRead]:
    items, total = await engine.inbox(session, ctx, page=page, doc_type=doc_type)
    initiators = await user_lookup.people(
        session,
        company_id=ctx.company_id,
        user_ids={i.request.initiated_by_id for i in items if i.request.initiated_by_id},
    )
    labels = {s.doc_type: s.label for s in registry.specs()}
    now = utcnow()
    return Page.of(
        [
            InboxItemRead(
                request_id=i.request.id,
                doc_type=i.request.doc_type,
                doc_label=labels.get(i.request.doc_type, i.request.doc_type),
                doc_id=i.request.doc_id,
                doc_number=i.request.doc_number,
                doc_summary=i.request.doc_summary,
                amount=i.request.amount,
                currency_code=i.request.currency_code,
                link_path=i.request.link_path,
                initiated_by_name=(
                    initiators[i.request.initiated_by_id].full_name
                    if i.request.initiated_by_id in initiators
                    else None
                ),
                submitted_at=i.request.submitted_at,
                step_no=i.step.step_no,
                step_name=i.step.name,
                total_steps=i.total_steps,
                due_at=i.step.due_at,
                is_overdue=bool(i.step.due_at and i.step.due_at < now),
                escalated=i.step.escalated_at is not None,
            )
            for i in items
        ],
        params=page,
        total=total,
    )


@router.get(
    "/approvals/requests",
    response_model=list[RequestRead],
    summary="The approval trail of one document: every attempt, newest first",
)
async def requests_for_document(
    ctx: Access, session: SessionDep, doc_type: str, doc_id: UUID
) -> list[RequestRead]:
    rows = await engine.for_document(session, ctx, doc_type, doc_id)
    return await _request_views(session, ctx, rows)


@router.get("/approvals/requests/{request_id}", response_model=RequestRead)
async def get_request(request_id: UUID, ctx: Access, session: SessionDep) -> RequestRead:
    return await _one(session, ctx, request_id)


async def _decide(
    request_id: UUID, action: ActionType, payload: DecisionRequest, ctx: AccessContext, uow: UowDep
) -> DecisionResponse:
    result = await engine.decide(uow.session, ctx, request_id, action, payload.comments)
    await uow.session.flush()
    return DecisionResponse(
        request=await _one(uow.session, ctx, result.request.id),
        auto_recalled=result.auto_recalled,
        message=result.message,
    )


@router.post("/approvals/requests/{request_id}/approve", response_model=DecisionResponse)
async def approve(
    request_id: UUID, payload: DecisionRequest, ctx: Access, uow: UowDep
) -> DecisionResponse:
    return await _decide(request_id, ActionType.APPROVE, payload, ctx, uow)


@router.post("/approvals/requests/{request_id}/reject", response_model=DecisionResponse)
async def reject(
    request_id: UUID, payload: DecisionRequest, ctx: Access, uow: UowDep
) -> DecisionResponse:
    return await _decide(request_id, ActionType.REJECT, payload, ctx, uow)


@router.post("/approvals/requests/{request_id}/request-changes", response_model=DecisionResponse)
async def request_changes(
    request_id: UUID, payload: DecisionRequest, ctx: Access, uow: UowDep
) -> DecisionResponse:
    return await _decide(request_id, ActionType.REQUEST_CHANGES, payload, ctx, uow)


@router.post("/approvals/requests/{request_id}/recall", response_model=RequestRead)
async def recall(
    request_id: UUID, payload: DecisionRequest, ctx: Access, uow: UowDep
) -> RequestRead:
    request = await engine.recall(uow.session, ctx, request_id, payload.comments)
    await uow.session.flush()
    return await _one(uow.session, ctx, request.id)


@router.post("/approvals/requests/{request_id}/comments", response_model=RequestRead)
async def add_comment(
    request_id: UUID, payload: CommentRequest, ctx: Access, uow: UowDep
) -> RequestRead:
    await engine.comment(uow.session, ctx, request_id, payload.comments)
    return await _one(uow.session, ctx, request_id)
