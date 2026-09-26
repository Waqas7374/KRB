"""Approval definitions (versioned workflows) and runtime (requests, steps,
approvers, actions).

Runtime rows carry the document's project/site/department so that the
generic `scope_filter` decides who may *see* a request, exactly as it does for
the document itself.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check
from app.core.db import BaseModel, CompanyModel
from app.modules.approvals.domain.enums import (
    ActionType,
    ApproverSource,
    RequestStatus,
    StepStatus,
    WorkflowScope,
)

# -----------------------------------------------------------------------------
# Definitions
# -----------------------------------------------------------------------------


class ApprovalWorkflow(CompanyModel):
    """One immutable version of a workflow for one document type and scope.

    Editing a workflow inserts a new version and deactivates the old one; the
    old row stays, because in-flight requests and the audit trail refer to it.
    """

    __tablename__ = "approval_workflows"
    __audited__ = True

    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WorkflowScope.COMPANY.value
    )
    # A PROJECT-scoped workflow overrides the company one for that project.
    scope_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "doc_type",
            "scope_type",
            "scope_id",
            "version",
            name="uq_approval_workflows_version",
            postgresql_nulls_not_distinct=True,
        ),
        # At most one live version per document type and scope.
        Index(
            "uq_approval_workflows_one_active",
            "company_id",
            "doc_type",
            "scope_type",
            text("coalesce(scope_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text("is_active"),
        ),
        enum_check("scope_type", WorkflowScope),
    )


# -----------------------------------------------------------------------------
# Runtime
# -----------------------------------------------------------------------------


class ApprovalRequest(CompanyModel):
    """One attempt to get one document approved.

    `workflow_snapshot` is the critical column (docs/04 §2): the selected rule
    and its resolved step chain, frozen at submission. Editing the workflow
    later cannot change who must approve this document.
    """

    __tablename__ = "approval_requests"
    __audited__ = True

    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    doc_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    doc_number: Mapped[str | None] = mapped_column(String(40))
    doc_summary: Mapped[str | None] = mapped_column(String(300))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency_code: Mapped[str | None] = mapped_column(String(3))
    link_path: Mapped[str | None] = mapped_column(String(200))

    # Scope dimensions copied from the document for scope_filter.
    project_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    site_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    department_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_workflows.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    workflow_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=RequestStatus.PENDING.value
    )
    current_step_no: Mapped[int | None] = mapped_column(Integer)
    initiated_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_reason: Mapped[str | None] = mapped_column(String(1000))

    steps: Mapped[list[ApprovalRequestStep]] = relationship(
        back_populates="request",
        order_by="ApprovalRequestStep.step_no",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_approval_requests_document", "doc_type", "doc_id"),
        # One live request per document: a second submit while one is pending
        # is a double-click, not a second approval.
        Index(
            "uq_approval_requests_one_open",
            "doc_type",
            "doc_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        enum_check("status", RequestStatus),
    )


class ApprovalRequestStep(BaseModel):
    __tablename__ = "approval_request_steps"

    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    approver_type: Mapped[str] = mapped_column(String(20), nullable=False)
    approver_ref: Mapped[str | None] = mapped_column(String(120))
    quorum_type: Mapped[str] = mapped_column(String(10), nullable=False)
    quorum_required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approvals_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sla_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=StepStatus.WAITING.value
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Each SLA reminder is sent once (engine.remind_due).
    reminded_50_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminded_90_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    request: Mapped[ApprovalRequest] = relationship(back_populates="steps", lazy="noload")
    approvers: Mapped[list[ApprovalStepApprover]] = relationship(
        back_populates="step", lazy="selectin", order_by="ApprovalStepApprover.added_at"
    )

    __table_args__ = (
        UniqueConstraint("request_id", "step_no", name="uq_approval_request_steps_no"),
        # The inbox and the escalation sweep (docs/02 index list).
        Index(
            "ix_approval_request_steps_pending_due",
            "status",
            "due_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
        enum_check("status", StepStatus),
    )


class ApprovalStepApprover(BaseModel):
    """Who may decide a step — materialised when the step activates, so the
    approval inbox is an indexed lookup rather than a permission computation
    per request (docs/04 §2 Eligibility)."""

    __tablename__ = "approval_step_approvers"

    request_step_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_request_steps.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ApproverSource.RULE.value
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    step: Mapped[ApprovalRequestStep] = relationship(back_populates="approvers", lazy="noload")

    __table_args__ = (
        UniqueConstraint("request_step_id", "user_id", name="uq_approval_step_approvers_user"),
        enum_check("source", ApproverSource),
    )


class ApprovalAction(BaseModel):
    """Append-only (database trigger): the permanent record of who did what."""

    __tablename__ = "approval_actions"

    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    request_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approval_request_steps.id", ondelete="RESTRICT")
    )
    step_no: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    actor_name: Mapped[str | None] = mapped_column(String(160))
    comments: Mapped[str | None] = mapped_column(String(2000))
    acted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acted_ip: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (enum_check("action", ActionType),)
