"""The approval runtime (docs/04 §2).

    submit ─► PENDING ──approve (last step)──► APPROVED
                │ ├──reject──────────────────► REJECTED
                │ ├──request changes─────────► CHANGES_REQUESTED
                │ ├──recall (initiator)──────► RECALLED
                │ └──document changed────────► RECALLED (auto)

Every terminal state is final. Resubmitting a document — after a rejection
*or* after changes were requested — creates a new request that is routed
afresh. docs/04 draws CHANGES_REQUESTED looping back to PENDING on the same
request; that was changed deliberately: the requested change may move the
document into a different rule (a PR edited from 90 000 to 900 000 must
pick up the procurement-manager step), and resuming the old snapshot would
route it by rules that no longer describe it.

Guarantees:

* The rule and step chain are frozen onto the request at submission
  (`workflow_snapshot`); editing the workflow never affects it.
* Nobody approves their own document unless the step explicitly allows it.
* The approver list is materialised when a step activates, and each decision
  re-checks that the actor still may approve (a grant revoked since then
  counts), so the inbox is fast without being stale in a way that matters.
* A document edited while pending is detected by hash at the next decision
  and the request is recalled rather than approved.
* Handlers (the owning module) are called in the same transaction, so a
  document's status and its approval can never disagree.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.core.access import AccessContext
from app.core.context import current_context
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    StateTransitionError,
    ValidationError,
)
from app.core.pagination import PageParams
from app.core.scoping import scope_filter
from app.core.types import utcnow
from app.modules.access.services import approver_lookup
from app.modules.approvals.domain.definition import StepDef, applicable_steps, parse, select_rule
from app.modules.approvals.domain.enums import (
    ActionType,
    ApproverSource,
    ApproverType,
    DynamicApprover,
    QuorumType,
    RequestStatus,
    StepStatus,
)
from app.modules.approvals.models import (
    ApprovalAction,
    ApprovalRequest,
    ApprovalRequestStep,
    ApprovalStepApprover,
)
from app.modules.approvals.services import registry, workflow_service
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.identity.services import user_lookup
from app.modules.org.services import document_lookup
from app.modules.rules.services import approval_limit
from app.platform import outbox

MIN_REASON_LENGTH = 5


# -----------------------------------------------------------------------------
# Inputs and small helpers
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalSubject:
    """Everything the engine needs to know about a document being submitted."""

    doc_type: str
    doc_id: UUID
    company_id: UUID
    initiated_by: UUID
    context: dict[str, Any]
    document_hash: str
    doc_number: str | None = None
    summary: str | None = None
    amount: Decimal | None = None
    currency_code: str | None = None
    link_path: str | None = None
    project_id: UUID | None = None
    site_id: UUID | None = None
    department_id: UUID | None = None


def document_hash(content: dict[str, Any]) -> str:
    """Stable SHA-256 of a document's approval-relevant content."""
    canonical = json.dumps(_jsonable(content), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        # normalize(): 450000.0000 and 450000 hash identically.
        return format(value.normalize(), "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _require_reason(action: ActionType, comments: str | None) -> str:
    text = (comments or "").strip()
    if len(text) < MIN_REASON_LENGTH:
        raise ValidationError(
            f"A reason is required to {action.value.lower().replace('_', ' ')}.",
            errors=[
                {
                    "field": "comments",
                    "code": "required",
                    "message": f"at least {MIN_REASON_LENGTH} characters",
                }
            ],
        )
    return text


async def _actor_name(session: AsyncSession, company_id: UUID, user_id: UUID | None) -> str | None:
    if user_id is None:
        return None
    people = await user_lookup.people(session, company_id=company_id, user_ids={user_id})
    person = people.get(user_id)
    return person.full_name if person else None


async def _add_action(
    session: AsyncSession,
    request: ApprovalRequest,
    action: ActionType,
    *,
    actor_user_id: UUID | None,
    step: ApprovalRequestStep | None = None,
    comments: str | None = None,
) -> ApprovalAction:
    row = ApprovalAction(
        request_id=request.id,
        request_step_id=step.id if step else None,
        step_no=step.step_no if step else None,
        action=action.value,
        actor_user_id=actor_user_id,
        actor_name=await _actor_name(session, request.company_id, actor_user_id),
        comments=comments,
        acted_at=utcnow(),
        acted_ip=current_context().ip,
        created_by_id=actor_user_id,
    )
    session.add(row)
    return row


def _snapshot_step(snapshot: dict[str, Any], step_no: int) -> StepDef:
    for raw in snapshot["steps"]:
        if raw["step_no"] == step_no:
            return StepDef(**{**raw, "approver_refs": tuple(raw.get("approver_refs") or ())})
    raise RuntimeError(f"step {step_no} missing from snapshot")


def _outcome(
    request: ApprovalRequest, actor: UUID | None, reason: str | None
) -> registry.ApprovalOutcome:
    return registry.ApprovalOutcome(
        request_id=request.id,
        doc_id=request.doc_id,
        status=request.status,
        actor_user_id=actor,
        reason=reason,
    )


async def _emit(
    session: AsyncSession, event_type: str, request: ApprovalRequest, **payload: Any
) -> None:
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type=event_type,
            aggregate_type="ApprovalRequest",
            aggregate_id=request.id,
            payload={
                "doc_type": request.doc_type,
                "doc_id": str(request.doc_id),
                "doc_number": request.doc_number,
                "summary": request.doc_summary,
                "link_path": request.link_path,
                "initiated_by": str(request.initiated_by_id) if request.initiated_by_id else None,
                **_jsonable(payload),
            },
            company_id=request.company_id,
        ),
    )


# -----------------------------------------------------------------------------
# Eligibility
# -----------------------------------------------------------------------------


async def _candidates(
    session: AsyncSession,
    request: ApprovalRequest,
    approver_type: str,
    ref: str | None,
    refs: tuple[str, ...] = (),
) -> set[UUID]:
    if approver_type == ApproverType.ROLE.value and ref:
        return await approver_lookup.users_holding_role(
            session,
            company_id=request.company_id,
            role_code=ref,
            project_id=request.project_id,
            site_id=request.site_id,
            department_id=request.department_id,
            on=utcnow().date(),
        )
    if approver_type == ApproverType.USER.value and ref:
        return {UUID(ref)}
    if approver_type == ApproverType.GROUP.value:
        return {UUID(r) for r in refs}
    if approver_type == ApproverType.DYNAMIC.value:
        place = await document_lookup.resolve_place(
            session,
            company_id=request.company_id,
            project_id=request.project_id,
            site_id=request.site_id,
        )
        if ref == DynamicApprover.PROJECT_MANAGER.value and place.project_manager_id:
            return {place.project_manager_id}
        if ref == DynamicApprover.SITE_MANAGER.value and place.site_manager_id:
            return {place.site_manager_id}
    return set()


async def _eligible(
    session: AsyncSession,
    request: ApprovalRequest,
    candidates: set[UUID],
    *,
    allow_self_approve: bool,
) -> list[UUID]:
    """Filter candidates down to people who may actually decide: active,
    not the initiator (unless allowed), and holding the approve permission in
    a scope that covers this document."""
    if not allow_self_approve and request.initiated_by_id is not None:
        candidates = candidates - {request.initiated_by_id}
    people = await user_lookup.people(session, company_id=request.company_id, user_ids=candidates)
    permission = request.workflow_snapshot["approve_permission"]
    eligible: list[UUID] = []
    for user_id in sorted(candidates, key=str):
        person = people.get(user_id)
        if person is None or not person.can_act:
            continue
        if await approver_lookup.can_act(
            session,
            user_id=user_id,
            company_id=request.company_id,
            permission=permission,
            project_id=request.project_id,
            site_id=request.site_id,
            department_id=request.department_id,
        ):
            eligible.append(user_id)
    return eligible


async def _activate(
    session: AsyncSession,
    request: ApprovalRequest,
    step: ApprovalRequestStep,
    *,
    at_submission: bool,
) -> list[UUID]:
    """Make `step` the live step and materialise who may decide it.

    With nobody eligible, the step's escalation target is tried at once. If
    that also yields nobody: at submission the submit fails loudly (the
    author fixes the project manager or an admin fixes the workflow); later
    in the chain the step stays pending and an `approval.stuck` event tells
    the author, since failing would undo the previous approver's decision.
    """
    definition = _snapshot_step(request.workflow_snapshot, step.step_no)
    now = utcnow()
    candidates = await _candidates(
        session,
        request,
        definition.approver_type,
        definition.approver_ref,
        definition.approver_refs,
    )
    approvers = await _eligible(
        session, request, candidates, allow_self_approve=definition.allow_self_approve
    )
    source = ApproverSource.RULE
    if not approvers and definition.escalate_to_type:
        escalation = await _candidates(
            session, request, definition.escalate_to_type, definition.escalate_to_ref
        )
        approvers = await _eligible(session, request, escalation, allow_self_approve=False)
        source = ApproverSource.ESCALATION

    required = {
        QuorumType.ANY.value: 1,
        QuorumType.ALL.value: max(1, len(approvers)),
        QuorumType.N_OF_M.value: definition.quorum_count or 1,
    }[definition.quorum_type]

    if len(approvers) < required:
        message = (
            f"Nobody is able to approve step {step.step_no} ({step.name}) of this "
            f"{request.doc_type.replace('_', ' ')}"
            + (
                f": it needs {required} approvers and only {len(approvers)} are eligible"
                if approvers
                else ": no eligible approver (check the project/site manager and role grants; "
                "the person who submitted cannot approve their own document)"
            )
        )
        if at_submission:
            raise BusinessRuleError("approval_no_eligible_approver", message)
        await _emit(session, "approval.stuck", request, step_no=step.step_no, message=message)

    step.status = StepStatus.PENDING.value
    step.activated_at = now
    step.due_at = now + timedelta(hours=step.sla_hours)
    step.quorum_required = required
    request.current_step_no = step.step_no
    for user_id in approvers:
        session.add(
            ApprovalStepApprover(
                request_step_id=step.id, user_id=user_id, source=source.value, added_at=now
            )
        )
    await session.flush()
    if approvers:
        await _emit(
            session,
            "approval.step_activated",
            request,
            step_no=step.step_no,
            step_name=step.name,
            due_at=step.due_at,
            approver_ids=approvers,
        )
    return approvers


# -----------------------------------------------------------------------------
# Submit
# -----------------------------------------------------------------------------


async def submit(session: AsyncSession, subject: ApprovalSubject) -> ApprovalRequest:
    """Route a document into approval. The caller has already checked that
    the initiator may submit it."""
    handler = registry.get(subject.doc_type)
    spec = handler.spec

    open_request = await session.scalar(
        select(ApprovalRequest.id).where(
            ApprovalRequest.doc_type == subject.doc_type,
            ApprovalRequest.doc_id == subject.doc_id,
            ApprovalRequest.status == RequestStatus.PENDING.value,
        )
    )
    if open_request is not None:
        raise ConflictError("This document is already awaiting approval.")

    workflow_row = await workflow_service.active_workflow_for(
        session,
        company_id=subject.company_id,
        doc_type=subject.doc_type,
        project_id=subject.project_id,
    )
    if workflow_row is None:
        # Never auto-approve for want of configuration (docs/04 §3).
        raise BusinessRuleError(
            "approval_workflow_missing",
            f"No approval workflow is configured for {spec.label.lower()}s. An administrator "
            "must publish one before these documents can be submitted.",
        )

    rule = select_rule(parse(workflow_row.definition), subject.context)
    steps = applicable_steps(rule, subject.context)
    now = utcnow()

    request = ApprovalRequest(
        company_id=subject.company_id,
        doc_type=subject.doc_type,
        doc_id=subject.doc_id,
        doc_number=subject.doc_number,
        doc_summary=(subject.summary or "")[:300] or None,
        amount=subject.amount,
        currency_code=subject.currency_code,
        link_path=subject.link_path,
        project_id=subject.project_id,
        site_id=subject.site_id,
        department_id=subject.department_id,
        workflow_id=workflow_row.id,
        workflow_version=workflow_row.version,
        workflow_snapshot={
            "workflow_id": str(workflow_row.id),
            "workflow_name": workflow_row.name,
            "version": workflow_row.version,
            "approve_permission": spec.approve_permission,
            "rule": {"sequence": rule.sequence, "name": rule.name, "condition": rule.condition},
            "steps": [{**asdict(s), "approver_refs": list(s.approver_refs)} for s in steps],
        },
        context_snapshot=_jsonable(subject.context),
        document_hash=subject.document_hash,
        status=RequestStatus.PENDING.value,
        initiated_by_id=subject.initiated_by,
        submitted_at=now,
        created_by_id=subject.initiated_by,
    )
    session.add(request)
    await session.flush()
    await _add_action(session, request, ActionType.SUBMIT, actor_user_id=subject.initiated_by)

    if not steps:
        # An explicit zero-step rule: approval by configuration, recorded as such.
        request.status = RequestStatus.APPROVED.value
        request.completed_at = now
        request.outcome_reason = f"Approved automatically by rule '{rule.name}' (no steps)"
        await _add_action(
            session,
            request,
            ActionType.AUTO_APPROVE,
            actor_user_id=None,
            comments=request.outcome_reason,
        )
        await session.flush()
        await handler.on_approved(session, _outcome(request, None, request.outcome_reason))
        await _emit(session, "approval.approved", request)
        return request

    step_rows = [
        ApprovalRequestStep(
            request_id=request.id,
            step_no=s.step_no,
            name=s.name,
            approver_type=s.approver_type,
            approver_ref=s.approver_ref or (",".join(s.approver_refs) or None),
            quorum_type=s.quorum_type,
            quorum_required=1,
            sla_hours=s.sla_hours,
            status=StepStatus.WAITING.value,
        )
        for s in steps
    ]
    session.add_all(step_rows)
    await session.flush()
    await _activate(session, request, step_rows[0], at_submission=True)

    await record_audit(
        session,
        action=AuditAction.SUBMIT,
        entity_type="ApprovalRequest",
        entity_id=request.id,
        entity_label=subject.doc_number,
        company_id=subject.company_id,
        project_id=subject.project_id,
        site_id=subject.site_id,
        summary=(
            f"{spec.label} {subject.doc_number or subject.doc_id} submitted; routed by "
            f"'{workflow_row.name}' v{workflow_row.version}, rule '{rule.name}', "
            f"{len(steps)} step(s)"
        ),
    )
    return request


# -----------------------------------------------------------------------------
# Decisions
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class DecisionResult:
    request: ApprovalRequest
    auto_recalled: bool = False
    message: str | None = None
    notes: list[str] = field(default_factory=list)


async def _lock_request(
    session: AsyncSession, company_id: UUID, request_id: UUID
) -> ApprovalRequest:
    request = (
        await session.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == request_id, ApprovalRequest.company_id == company_id)
            .options(noload("*"))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if request is None:
        raise NotFoundError("Approval request", request_id)
    return request


async def _steps(session: AsyncSession, request_id: UUID) -> list[ApprovalRequestStep]:
    rows = await session.execute(
        select(ApprovalRequestStep)
        .where(ApprovalRequestStep.request_id == request_id)
        .order_by(ApprovalRequestStep.step_no)
    )
    return list(rows.scalars().all())


async def _finish(
    session: AsyncSession,
    request: ApprovalRequest,
    steps: list[ApprovalRequestStep],
    status: RequestStatus,
    reason: str | None,
) -> None:
    now = utcnow()
    request.status = status.value
    request.completed_at = now
    request.outcome_reason = reason
    for step in steps:
        if step.status in (StepStatus.WAITING.value, StepStatus.PENDING.value):
            step.status = StepStatus.CANCELLED.value
            step.decided_at = step.decided_at or now


async def limit_block_reason(
    session: AsyncSession, request: ApprovalRequest, user_id: UUID
) -> str | None:
    """Why `user_id` may not approve this request for its amount, or None.

    Limits (docs/04 §4) answer a different question from the workflow: the
    workflow says who must sign, the limit says how much a signer may sign.
    """
    if request.amount is None:
        return None
    handler = registry.get(request.doc_type)
    result = await approval_limit.check(
        session,
        company_id=request.company_id,
        user_id=user_id,
        doc_type=request.doc_type,
        approve_permission=handler.spec.approve_permission,
        amount=request.amount,
        project_id=request.project_id,
        site_id=request.site_id,
        department_id=request.department_id,
    )
    if result.allowed:
        return None
    return result.message(handler.spec.label.lower(), request.amount)


async def decide(
    session: AsyncSession,
    ctx: AccessContext,
    request_id: UUID,
    action: ActionType,
    comments: str | None = None,
) -> DecisionResult:
    if action not in (ActionType.APPROVE, ActionType.REJECT, ActionType.REQUEST_CHANGES):
        raise ValidationError(f"'{action.value}' is not a decision")

    request = await _lock_request(session, ctx.company_id, request_id)
    if request.status != RequestStatus.PENDING.value:
        raise StateTransitionError("Approval request", request.status, action.value)

    steps = await _steps(session, request.id)
    step = next((s for s in steps if s.step_no == request.current_step_no), None)
    if step is None or step.status != StepStatus.PENDING.value:
        raise StateTransitionError("Approval step", step.status if step else "none", action.value)

    is_listed = await session.scalar(
        select(
            exists().where(
                ApprovalStepApprover.request_step_id == step.id,
                ApprovalStepApprover.user_id == ctx.user_id,
            )
        )
    )
    if not is_listed:
        raise PermissionDeniedError(
            detail="You are not an approver for the current step of this request."
        )

    handler = registry.get(request.doc_type)

    # The document must be exactly what was submitted (docs/04 §2).
    current_hash = await handler.current_hash(session, request.doc_id)
    if current_hash != request.document_hash:
        reason = "The document was changed after it was submitted, so the request was recalled."
        await _finish(session, request, steps, RequestStatus.RECALLED, reason)
        await _add_action(
            session, request, ActionType.AUTO_RECALL, actor_user_id=None, comments=reason
        )
        await session.flush()
        await handler.on_recalled(session, _outcome(request, None, reason))
        await _emit(session, "approval.recalled", request, reason=reason, auto=True)
        return DecisionResult(request=request, auto_recalled=True, message=reason)

    # Still allowed *now*: a grant revoked since the step activated counts.
    still_eligible = await _eligible(
        session,
        request,
        {ctx.user_id},
        allow_self_approve=_snapshot_step(
            request.workflow_snapshot, step.step_no
        ).allow_self_approve,
    )
    if ctx.user_id not in still_eligible:
        raise PermissionDeniedError(
            detail=(
                "You can no longer approve this request: your access changed after it reached you."
            )
        )

    if action is ActionType.APPROVE:
        blocked = await limit_block_reason(session, request, ctx.user_id)
        if blocked:
            raise BusinessRuleError("approval_limit_exceeded", blocked)

    already = await session.scalar(
        select(
            exists().where(
                ApprovalAction.request_step_id == step.id,
                ApprovalAction.actor_user_id == ctx.user_id,
                ApprovalAction.action.in_([ActionType.APPROVE.value]),
            )
        )
    )
    if already:
        raise ConflictError("You have already approved this step.")

    now = utcnow()
    label = f"{request.doc_type.replace('_', ' ')} {request.doc_number or request.doc_id}"

    if action is ActionType.APPROVE:
        await _add_action(
            session, request, action, actor_user_id=ctx.user_id, step=step, comments=comments
        )
        step.approvals_count += 1
        if step.approvals_count < step.quorum_required:
            await session.flush()
            return DecisionResult(
                request=request,
                message=(
                    f"Recorded. {step.quorum_required - step.approvals_count} more "
                    "approval(s) needed."
                ),
            )
        step.status = StepStatus.APPROVED.value
        step.decided_at = now
        following = next((s for s in steps if s.step_no == step.step_no + 1), None)
        if following is not None:
            await session.flush()
            await _activate(session, request, following, at_submission=False)
            await record_audit(
                session,
                action=AuditAction.APPROVE,
                entity_type="ApprovalRequest",
                entity_id=request.id,
                entity_label=request.doc_number,
                project_id=request.project_id,
                site_id=request.site_id,
                summary=f"Step {step.step_no} ({step.name}) of {label} approved",
            )
            return DecisionResult(request=request, message=f"Approved. Now with: {following.name}.")

        await _finish(session, request, steps, RequestStatus.APPROVED, comments)
        await session.flush()
        await handler.on_approved(session, _outcome(request, ctx.user_id, comments))
        await _emit(session, "approval.approved", request, actor_id=ctx.user_id)
        await record_audit(
            session,
            action=AuditAction.APPROVE,
            entity_type="ApprovalRequest",
            entity_id=request.id,
            entity_label=request.doc_number,
            project_id=request.project_id,
            site_id=request.site_id,
            summary=f"{label} fully approved (final step: {step.name})",
        )
        return DecisionResult(request=request, message="Approved. This was the final step.")

    reason = _require_reason(action, comments)
    await _add_action(
        session, request, action, actor_user_id=ctx.user_id, step=step, comments=reason
    )
    step.status = StepStatus.REJECTED.value
    step.decided_at = now
    if action is ActionType.REJECT:
        await _finish(session, request, steps, RequestStatus.REJECTED, reason)
        await session.flush()
        await handler.on_rejected(session, _outcome(request, ctx.user_id, reason))
        await _emit(session, "approval.rejected", request, actor_id=ctx.user_id, reason=reason)
        audit_action, verb = AuditAction.REJECT, "rejected"
    else:
        await _finish(session, request, steps, RequestStatus.CHANGES_REQUESTED, reason)
        await session.flush()
        await handler.on_changes_requested(session, _outcome(request, ctx.user_id, reason))
        await _emit(
            session, "approval.changes_requested", request, actor_id=ctx.user_id, reason=reason
        )
        audit_action, verb = AuditAction.REQUEST_CORRECTION, "returned for changes"
    await record_audit(
        session,
        action=audit_action,
        entity_type="ApprovalRequest",
        entity_id=request.id,
        entity_label=request.doc_number,
        project_id=request.project_id,
        site_id=request.site_id,
        summary=f"{label} {verb} at step {step.step_no} ({step.name}): {reason}",
    )
    return DecisionResult(
        request=request, message=f"The {request.doc_type.replace('_', ' ')} was {verb}."
    )


async def recall(
    session: AsyncSession, ctx: AccessContext, request_id: UUID, reason: str | None = None
) -> ApprovalRequest:
    """The initiator withdraws the request — only while nobody has approved
    anything yet, so a recall can never erase a decision already made."""
    request = await _lock_request(session, ctx.company_id, request_id)
    if request.status != RequestStatus.PENDING.value:
        raise StateTransitionError("Approval request", request.status, RequestStatus.RECALLED.value)
    if request.initiated_by_id != ctx.user_id:
        raise PermissionDeniedError(detail="Only the person who submitted a request can recall it.")
    decided = await session.scalar(
        select(
            exists().where(
                ApprovalAction.request_id == request.id,
                ApprovalAction.action == ActionType.APPROVE.value,
            )
        )
    )
    if decided:
        raise BusinessRuleError(
            "approval_recall_after_decision",
            "This request cannot be recalled because an approver has already approved a step. "
            "Ask the current approver to request changes instead.",
        )
    steps = await _steps(session, request.id)
    text = (reason or "").strip() or "Recalled by the submitter"
    await _finish(session, request, steps, RequestStatus.RECALLED, text)
    await _add_action(session, request, ActionType.RECALL, actor_user_id=ctx.user_id, comments=text)
    await session.flush()
    await registry.get(request.doc_type).on_recalled(session, _outcome(request, ctx.user_id, text))
    await _emit(session, "approval.recalled", request, reason=text, auto=False)
    return request


async def comment(
    session: AsyncSession, ctx: AccessContext, request_id: UUID, text: str
) -> ApprovalAction:
    request = await get_visible(session, ctx, request_id)
    body = text.strip()
    if not body:
        raise ValidationError(
            "A comment cannot be empty.",
            errors=[{"field": "comments", "code": "required", "message": "required"}],
        )
    step = next((s for s in request.steps if s.step_no == request.current_step_no), None)
    row = await _add_action(
        session,
        request,
        ActionType.COMMENT,
        actor_user_id=ctx.user_id,
        step=step if request.status == RequestStatus.PENDING.value else None,
        comments=body[:2000],
    )
    await session.flush()
    return row


# -----------------------------------------------------------------------------
# Escalation (Celery beat, hourly)
# -----------------------------------------------------------------------------


async def escalate_overdue(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Apply the escalation policy to every pending step past its due time.

    Each step escalates once. With an escalation target, eligible people from
    it are added as approvers (the original approvers keep the ability to
    decide); without one, the approvers are simply reminded. Either way the
    trail records it.
    """
    now = now or utcnow()
    overdue = (
        (
            await session.execute(
                select(ApprovalRequestStep)
                .join(ApprovalRequest, ApprovalRequest.id == ApprovalRequestStep.request_id)
                .where(
                    ApprovalRequestStep.status == StepStatus.PENDING.value,
                    ApprovalRequestStep.due_at < now,
                    ApprovalRequestStep.escalated_at.is_(None),
                    ApprovalRequest.status == RequestStatus.PENDING.value,
                )
                .options(noload("*"))
                .with_for_update(of=ApprovalRequestStep, skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    for step in overdue:
        request = (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.id == step.request_id)
                .options(noload("*"))
            )
        ).scalar_one()
        definition = _snapshot_step(request.workflow_snapshot, step.step_no)
        existing = set(
            (
                await session.execute(
                    select(ApprovalStepApprover.user_id).where(
                        ApprovalStepApprover.request_step_id == step.id
                    )
                )
            )
            .scalars()
            .all()
        )
        added: list[UUID] = []
        if definition.escalate_to_type:
            targets = await _candidates(
                session, request, definition.escalate_to_type, definition.escalate_to_ref
            )
            for user_id in await _eligible(session, request, targets, allow_self_approve=False):
                if user_id not in existing:
                    session.add(
                        ApprovalStepApprover(
                            request_step_id=step.id,
                            user_id=user_id,
                            source=ApproverSource.ESCALATION.value,
                            added_at=now,
                        )
                    )
                    added.append(user_id)
        step.escalated_at = now
        names = await user_lookup.people(
            session, company_id=request.company_id, user_ids=set(added)
        )
        note = f"SLA of {step.sla_hours}h passed. " + (
            "Escalated to " + ", ".join(p.full_name for p in names.values()) + "."
            if added
            else "Approvers reminded"
            + (" (no eligible escalation target)." if definition.escalate_to_type else ".")
        )
        await _add_action(
            session, request, ActionType.ESCALATE, actor_user_id=None, step=step, comments=note
        )
        await _emit(
            session,
            "approval.escalated",
            request,
            step_no=step.step_no,
            step_name=step.name,
            approver_ids=sorted(existing | set(added), key=str),
            added_ids=added,
        )
    await session.flush()
    return len(overdue)


# -----------------------------------------------------------------------------
# Reads
# -----------------------------------------------------------------------------


def _visible(ctx: AccessContext) -> Any:
    """Rows the caller may see without the scoped `approvals.view`: requests
    they submitted, or that were ever routed to them."""
    return or_(
        ApprovalRequest.initiated_by_id == ctx.user_id,
        exists().where(
            ApprovalRequestStep.request_id == ApprovalRequest.id,
            ApprovalStepApprover.request_step_id == ApprovalRequestStep.id,
            ApprovalStepApprover.user_id == ctx.user_id,
        ),
    )


def _visible_query(ctx: AccessContext) -> Select[Any]:
    base = select(ApprovalRequest).where(ApprovalRequest.company_id == ctx.company_id)
    if ctx.has("approvals.view"):
        scoped = scope_filter(select(ApprovalRequest.id), ApprovalRequest, ctx, "approvals.view")
        return base.where(or_(_visible(ctx), ApprovalRequest.id.in_(scoped.scalar_subquery())))
    return base.where(_visible(ctx))


async def get_visible(
    session: AsyncSession, ctx: AccessContext, request_id: UUID
) -> ApprovalRequest:
    # populate_existing: a decision loads the request with noload("*") and then
    # adds approvers without touching the loaded collections. Without this,
    # re-reading it in the same session returns that stale identity-map copy
    # (no steps, missing approvers) instead of what was just written.
    request: ApprovalRequest | None = (
        await session.execute(
            _visible_query(ctx)
            .where(ApprovalRequest.id == request_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if request is None:
        raise NotFoundError("Approval request", request_id)
    return request


async def for_document(
    session: AsyncSession, ctx: AccessContext, doc_type: str, doc_id: UUID
) -> list[ApprovalRequest]:
    """Every attempt to approve one document, newest first — the trail."""
    rows = await session.execute(
        _visible_query(ctx)
        .where(ApprovalRequest.doc_type == doc_type, ApprovalRequest.doc_id == doc_id)
        .order_by(ApprovalRequest.submitted_at.desc())
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def actions_for(session: AsyncSession, request_ids: list[UUID]) -> list[ApprovalAction]:
    if not request_ids:
        return []
    rows = await session.execute(
        select(ApprovalAction)
        .where(ApprovalAction.request_id.in_(request_ids))
        .order_by(ApprovalAction.acted_at, ApprovalAction.created_at)
    )
    return list(rows.scalars().all())


@dataclass(frozen=True, slots=True)
class InboxItem:
    request: ApprovalRequest
    step: ApprovalRequestStep
    total_steps: int


async def inbox(
    session: AsyncSession, ctx: AccessContext, *, page: PageParams, doc_type: str | None = None
) -> tuple[list[InboxItem], int]:
    """Every pending step the caller can decide, most urgent first."""
    total_steps = (
        select(func.count(ApprovalRequestStep.id))
        .where(ApprovalRequestStep.request_id == ApprovalRequest.id)
        .correlate(ApprovalRequest)
        .scalar_subquery()
    )
    stmt = (
        select(ApprovalRequest, ApprovalRequestStep, total_steps)
        .join(ApprovalRequestStep, ApprovalRequestStep.request_id == ApprovalRequest.id)
        .join(
            ApprovalStepApprover,
            and_(
                ApprovalStepApprover.request_step_id == ApprovalRequestStep.id,
                ApprovalStepApprover.user_id == ctx.user_id,
            ),
        )
        .where(
            ApprovalRequest.company_id == ctx.company_id,
            ApprovalRequest.status == RequestStatus.PENDING.value,
            ApprovalRequestStep.status == StepStatus.PENDING.value,
            ApprovalRequestStep.step_no == ApprovalRequest.current_step_no,
        )
    )
    if doc_type:
        stmt = stmt.where(ApprovalRequest.doc_type == doc_type)
    count = (
        await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    )
    rows = await session.execute(
        stmt.order_by(ApprovalRequestStep.due_at.asc().nulls_last(), ApprovalRequest.submitted_at)
        .limit(page.limit)
        .offset(page.offset)
    )
    return [InboxItem(request=r, step=s, total_steps=n) for r, s, n in rows.tuples().all()], count


# Fractions of a step's SLA at which its approvers are reminded, once each.
_REMINDER_POINTS = ((Decimal("0.9"), 90), (Decimal("0.5"), 50))


async def remind_due(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Remind approvers at 50 % and 90 % of a step's SLA, once each (docs/04 §5).

    Escalation handles a step that is already overdue; this is the nudge before
    that. A step first seen at 95 % gets the 90 % reminder only — two messages
    in one tick would be noise, not help.
    """
    now = now or utcnow()
    candidates = (
        (
            await session.execute(
                select(ApprovalRequestStep)
                .join(ApprovalRequest, ApprovalRequest.id == ApprovalRequestStep.request_id)
                .where(
                    ApprovalRequestStep.status == StepStatus.PENDING.value,
                    ApprovalRequestStep.activated_at.is_not(None),
                    ApprovalRequestStep.due_at > now,
                    ApprovalRequestStep.escalated_at.is_(None),
                    ApprovalRequestStep.reminded_90_at.is_(None),
                    ApprovalRequest.status == RequestStatus.PENDING.value,
                )
                .options(noload("*"))
                .with_for_update(of=ApprovalRequestStep, skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    sent = 0
    for step in candidates:
        assert step.activated_at is not None and step.due_at is not None
        window = (step.due_at - step.activated_at).total_seconds()
        if window <= 0:
            continue
        elapsed = Decimal(str((now - step.activated_at).total_seconds() / window))
        percent = next((p for fraction, p in _REMINDER_POINTS if elapsed >= fraction), None)
        if percent is None or (percent == 50 and step.reminded_50_at is not None):
            continue
        request = (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.id == step.request_id)
                .options(noload("*"))
            )
        ).scalar_one()
        approver_ids = (
            (
                await session.execute(
                    select(ApprovalStepApprover.user_id).where(
                        ApprovalStepApprover.request_step_id == step.id
                    )
                )
            )
            .scalars()
            .all()
        )
        step.reminded_50_at = step.reminded_50_at or now
        if percent == 90:
            step.reminded_90_at = now
        await _emit(
            session,
            "approval.reminder",
            request,
            step_no=step.step_no,
            step_name=step.name,
            percent=percent,
            hours_left=max(0, round((step.due_at - now).total_seconds() / 3600)),
            approver_ids=sorted(approver_ids, key=str),
        )
        sent += 1
    await session.flush()
    return sent
