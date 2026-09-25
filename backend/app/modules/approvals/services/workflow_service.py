"""Approval workflow definitions: save a version, list, simulate.

Saving never edits a row. It validates the whole definition, inserts version
N+1 and deactivates version N in one transaction, so requests already in
flight keep pointing at — and snapshotting — the version they started under.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import NotFoundError, ValidationError
from app.modules.access.services import approver_lookup
from app.modules.approvals.domain.definition import (
    DefinitionError,
    RuleDef,
    StepDef,
    WorkflowDef,
    applicable_steps,
    parse,
    select_rule,
)
from app.modules.approvals.domain.enums import ApproverType, WorkflowScope
from app.modules.approvals.models import ApprovalWorkflow
from app.modules.approvals.services import registry
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.identity.services import user_lookup
from app.modules.org.services import document_lookup


def _definition_errors(exc: DefinitionError) -> ValidationError:
    return ValidationError(
        "The workflow definition is not valid.",
        errors=[
            {"field": f"definition.{path}", "code": "invalid", "message": message}
            for path, message in exc.errors
        ],
    )


async def _check_references(
    session: AsyncSession, company_id: UUID, spec: registry.DocumentTypeSpec, workflow: WorkflowDef
) -> None:
    """The checks `parse` cannot make: do the named roles and people exist,
    and can each role actually approve this document type?"""
    problems: list[dict[str, Any]] = []
    role_permissions: dict[str, frozenset[str] | None] = {}
    user_refs: set[UUID] = set()

    def note(path: str, message: str) -> None:
        problems.append({"field": f"definition.{path}", "code": "invalid", "message": message})

    async def check_role(path: str, code: str) -> None:
        if code not in role_permissions:
            role_permissions[code] = await approver_lookup.role_permission_codes(
                session, company_id=company_id, role_code=code
            )
        perms = role_permissions[code]
        if perms is None:
            note(path, f"no role with code '{code}'")
        elif spec.approve_permission not in perms:
            note(
                path,
                f"role '{code}' cannot approve a {spec.label.lower()}: it lacks "
                f"{spec.approve_permission}",
            )

    def collect_user(path: str, ref: str) -> None:
        try:
            user_refs.add(UUID(ref))
        except ValueError:
            note(path, f"'{ref}' is not a user id")

    for rule in workflow.rules:
        for step in rule.steps:
            path = f"rules[sequence={rule.sequence}].steps[{step.step_no}]"
            if step.approver_type == ApproverType.ROLE.value and step.approver_ref:
                await check_role(f"{path}.approver_ref", step.approver_ref)
            elif step.approver_type == ApproverType.USER.value and step.approver_ref:
                collect_user(f"{path}.approver_ref", step.approver_ref)
            elif step.approver_type == ApproverType.GROUP.value:
                for ref in step.approver_refs:
                    collect_user(f"{path}.approver_refs", ref)
            if step.escalate_to_type == ApproverType.ROLE.value and step.escalate_to_ref:
                await check_role(f"{path}.escalate_to_ref", step.escalate_to_ref)
            elif step.escalate_to_type == ApproverType.USER.value and step.escalate_to_ref:
                collect_user(f"{path}.escalate_to_ref", step.escalate_to_ref)

    if user_refs:
        found = await user_lookup.people(session, company_id=company_id, user_ids=user_refs)
        for missing in user_refs - set(found):
            note("approvers", f"no user with id {missing}")
        for person in found.values():
            if not person.can_act:
                note("approvers", f"{person.full_name} is not an active user")

    if problems:
        raise ValidationError(
            "The workflow refers to roles or people that cannot approve.", errors=problems
        )


async def save_version(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    doc_type: str,
    name: str,
    description: str | None,
    definition: dict[str, Any],
    scope_type: str = WorkflowScope.COMPANY.value,
    scope_id: UUID | None = None,
    change_note: str | None = None,
) -> ApprovalWorkflow:
    spec = registry.get(doc_type).spec
    try:
        workflow = parse(definition, variables=spec.context_variables)
    except DefinitionError as exc:
        raise _definition_errors(exc) from exc

    if scope_type == WorkflowScope.PROJECT.value:
        if scope_id is None:
            raise ValidationError(
                "A project workflow needs a project.",
                errors=[{"field": "scope_id", "code": "required", "message": "choose a project"}],
            )
        try:
            await document_lookup.resolve_place(
                session, company_id=ctx.company_id, project_id=scope_id
            )
        except document_lookup.PlaceMismatchError as exc:
            raise ValidationError(
                str(exc), errors=[{"field": "scope_id", "code": "invalid", "message": str(exc)}]
            ) from exc
    else:
        scope_type, scope_id = WorkflowScope.COMPANY.value, None

    await _check_references(session, ctx.company_id, spec, workflow)

    same_scope = (
        ApprovalWorkflow.company_id == ctx.company_id,
        ApprovalWorkflow.doc_type == doc_type,
        ApprovalWorkflow.scope_type == scope_type,
        ApprovalWorkflow.scope_id.is_(None)
        if scope_id is None
        else ApprovalWorkflow.scope_id == scope_id,
    )
    # Lock the live version so two admins saving at once cannot both become
    # "the" active version; the partial unique index is the backstop.
    current = (
        await session.execute(
            select(ApprovalWorkflow)
            .where(*same_scope, ApprovalWorkflow.is_active)
            .with_for_update()
        )
    ).scalar_one_or_none()
    latest = await session.scalar(select(func.max(ApprovalWorkflow.version)).where(*same_scope))

    if current is not None:
        current.is_active = False
        await session.flush()  # free the one-active index slot before inserting

    row = ApprovalWorkflow(
        company_id=ctx.company_id,
        doc_type=doc_type,
        name=name,
        description=description,
        version=(latest or 0) + 1,
        scope_type=scope_type,
        scope_id=scope_id,
        is_active=True,
        definition=workflow.to_json(),
        change_note=change_note,
        created_by_id=ctx.user_id,
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session,
        action=AuditAction.WORKFLOW_CHANGE,
        entity_type="ApprovalWorkflow",
        entity_id=row.id,
        entity_label=f"{spec.label} v{row.version}",
        summary=(
            f"{spec.label} approval workflow v{row.version} published"
            + (f" (replaces v{current.version})" if current else "")
            + (f": {change_note}" if change_note else "")
        ),
    )
    return row


async def deactivate(
    session: AsyncSession, ctx: AccessContext, workflow_id: UUID
) -> ApprovalWorkflow:
    """Retire a workflow version without replacing it. For a project override
    this falls back to the company workflow; for the company workflow it means
    documents of that type can no longer be submitted — deliberately loud."""
    row = await get(session, ctx, workflow_id, for_update=True)
    if row.is_active:
        row.is_active = False
        await record_audit(
            session,
            action=AuditAction.WORKFLOW_CHANGE,
            entity_type="ApprovalWorkflow",
            entity_id=row.id,
            entity_label=f"{row.doc_type} v{row.version}",
            summary=f"Approval workflow {row.doc_type} v{row.version} deactivated",
        )
    return row


async def get(
    session: AsyncSession, ctx: AccessContext, workflow_id: UUID, *, for_update: bool = False
) -> ApprovalWorkflow:
    stmt = select(ApprovalWorkflow).where(
        ApprovalWorkflow.id == workflow_id, ApprovalWorkflow.company_id == ctx.company_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Approval workflow", workflow_id)
    return row


async def list_workflows(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    doc_type: str | None = None,
    include_inactive: bool = False,
) -> list[ApprovalWorkflow]:
    stmt = select(ApprovalWorkflow).where(ApprovalWorkflow.company_id == ctx.company_id)
    if doc_type:
        stmt = stmt.where(ApprovalWorkflow.doc_type == doc_type)
    if not include_inactive:
        stmt = stmt.where(ApprovalWorkflow.is_active)
    stmt = stmt.order_by(
        ApprovalWorkflow.doc_type, ApprovalWorkflow.scope_type, ApprovalWorkflow.version.desc()
    )
    return list((await session.execute(stmt)).scalars().all())


async def active_workflow_for(
    session: AsyncSession, *, company_id: UUID, doc_type: str, project_id: UUID | None
) -> ApprovalWorkflow | None:
    """The workflow a document is routed by: a project override if one is
    active for its project, otherwise the company workflow."""
    if project_id is not None:
        override = await session.scalar(
            select(ApprovalWorkflow).where(
                ApprovalWorkflow.company_id == company_id,
                ApprovalWorkflow.doc_type == doc_type,
                ApprovalWorkflow.scope_type == WorkflowScope.PROJECT.value,
                ApprovalWorkflow.scope_id == project_id,
                ApprovalWorkflow.is_active,
            )
        )
        if override is not None:
            return override
    company_wide: ApprovalWorkflow | None = await session.scalar(
        select(ApprovalWorkflow).where(
            ApprovalWorkflow.company_id == company_id,
            ApprovalWorkflow.doc_type == doc_type,
            ApprovalWorkflow.scope_type == WorkflowScope.COMPANY.value,
            ApprovalWorkflow.is_active,
        )
    )
    return company_wide


@dataclass(frozen=True, slots=True)
class Simulation:
    workflow: ApprovalWorkflow
    rule: RuleDef
    steps: list[StepDef]


async def simulate(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    doc_type: str,
    context: dict[str, Any],
    project_id: UUID | None = None,
) -> Simulation:
    """Which rule and step chain a document with this context would get —
    so an administrator can check a workflow before anyone depends on it."""
    registry.get(doc_type)
    row = await active_workflow_for(
        session, company_id=ctx.company_id, doc_type=doc_type, project_id=project_id
    )
    if row is None:
        raise NotFoundError("Active approval workflow for", doc_type)
    workflow = parse(row.definition)
    rule = select_rule(workflow, context)
    return Simulation(workflow=row, rule=rule, steps=applicable_steps(rule, context))
