"""Approval API contract."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.approvals.domain.enums import WorkflowScope


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# --- Definitions ----------------------------------------------------------------


class DocumentTypeRead(ApiModel):
    doc_type: str
    label: str
    approve_permission: str
    context_variables: list[str]


class WorkflowSave(ApiModel):
    """Publishing a workflow always creates a new version."""

    doc_type: Annotated[str, Field(min_length=2, max_length=40)]
    name: Annotated[str, Field(min_length=2, max_length=160)]
    description: Annotated[str | None, Field(max_length=1000)] = None
    scope_type: WorkflowScope = WorkflowScope.COMPANY
    scope_id: UUID | None = None
    definition: dict[str, Any]
    change_note: Annotated[str | None, Field(max_length=500)] = None


class WorkflowRead(ApiModel):
    id: UUID
    doc_type: str
    name: str
    description: str | None
    version: int
    scope_type: str
    scope_id: UUID | None
    is_active: bool
    definition: dict[str, Any]
    change_note: str | None
    created_by_id: UUID | None
    created_at: datetime


class SimulateRequest(ApiModel):
    doc_type: str
    context: dict[str, Any]
    project_id: UUID | None = None


class SimulatedStep(ApiModel):
    step_no: int
    name: str
    approver_type: str
    approver_ref: str | None
    quorum_type: str
    sla_hours: int


class SimulateResponse(ApiModel):
    workflow_id: UUID
    workflow_name: str
    version: int
    rule_sequence: int
    rule_name: str
    steps: list[SimulatedStep]


# --- Runtime ----------------------------------------------------------------------


class DecisionRequest(ApiModel):
    comments: Annotated[str | None, Field(max_length=2000)] = None


class CommentRequest(ApiModel):
    comments: Annotated[str, Field(min_length=1, max_length=2000)]


class ApproverRead(ApiModel):
    user_id: UUID
    full_name: str | None
    source: str
    has_approved: bool


class RequestStepRead(ApiModel):
    step_no: int
    name: str
    approver_type: str
    approver_ref: str | None
    quorum_type: str
    quorum_required: int
    approvals_count: int
    sla_hours: int
    status: str
    activated_at: datetime | None
    due_at: datetime | None
    escalated_at: datetime | None
    decided_at: datetime | None
    is_overdue: bool
    approvers: list[ApproverRead]


class ActionRead(ApiModel):
    id: UUID
    step_no: int | None
    action: str
    actor_user_id: UUID | None
    actor_name: str | None
    comments: str | None
    acted_at: datetime


class RequestRead(ApiModel):
    id: UUID
    doc_type: str
    doc_id: UUID
    doc_number: str | None
    doc_summary: str | None
    amount: Decimal | None
    currency_code: str | None
    link_path: str | None
    status: str
    current_step_no: int | None
    workflow_name: str
    workflow_version: int
    rule_name: str
    initiated_by_id: UUID | None
    initiated_by_name: str | None
    submitted_at: datetime
    completed_at: datetime | None
    outcome_reason: str | None
    # What the caller may do right now, so the UI does not re-derive rules.
    can_decide: bool
    # Set when the caller is an approver but the amount is above their approval
    # limit: they may still reject or return it, just not approve it.
    decision_blocked_reason: str | None = None
    can_recall: bool
    steps: list[RequestStepRead]
    actions: list[ActionRead]


class DecisionResponse(ApiModel):
    request: RequestRead
    auto_recalled: bool
    message: str | None


class InboxItemRead(ApiModel):
    request_id: UUID
    doc_type: str
    doc_label: str
    doc_id: UUID
    doc_number: str | None
    doc_summary: str | None
    amount: Decimal | None
    currency_code: str | None
    link_path: str | None
    initiated_by_name: str | None
    submitted_at: datetime
    step_no: int
    step_name: str
    total_steps: int
    due_at: datetime | None
    is_overdue: bool
    escalated: bool
