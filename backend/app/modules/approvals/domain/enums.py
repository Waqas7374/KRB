"""Approval engine enumerations."""

from __future__ import annotations

from enum import StrEnum


class ApproverType(StrEnum):
    ROLE = "ROLE"  # anyone holding role R with a grant covering the document
    USER = "USER"  # one named person
    DYNAMIC = "DYNAMIC"  # derived from the document: its project's manager, ...
    GROUP = "GROUP"  # an explicit list of people


class DynamicApprover(StrEnum):
    """Approvers derived from the document at the moment a step activates.

    docs/04 also lists department_head, requester_manager and budget_owner.
    Those need data that does not exist yet (department heads and reporting
    lines arrive with HR in Phase 5, budget owners with finance in Phase 4),
    so a workflow naming them is refused at save time rather than failing
    later on a live document.
    """

    PROJECT_MANAGER = "project_manager"
    SITE_MANAGER = "site_manager"


class QuorumType(StrEnum):
    ANY = "ANY"  # one approval completes the step
    ALL = "ALL"  # every eligible approver must approve
    N_OF_M = "N_OF_M"  # quorum_count approvals


class RequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RECALLED = "RECALLED"
    # Terminal, like REJECTED: the document returns to its author, and the
    # resubmission is a *new* request evaluated against the rules afresh. See
    # the engine's docstring for why this deviates from the docs/04 diagram.
    CHANGES_REQUESTED = "CHANGES_REQUESTED"

    @property
    def is_open(self) -> bool:
        return self is RequestStatus.PENDING


class StepStatus(StrEnum):
    WAITING = "WAITING"  # a later step, not yet reached
    PENDING = "PENDING"  # the active step
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"  # the request ended before this step decided


class ApproverSource(StrEnum):
    RULE = "RULE"
    ESCALATION = "ESCALATION"


class ActionType(StrEnum):
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    RECALL = "RECALL"
    AUTO_RECALL = "AUTO_RECALL"  # the document changed while pending
    COMMENT = "COMMENT"
    ESCALATE = "ESCALATE"
    AUTO_APPROVE = "AUTO_APPROVE"  # an explicit zero-step workflow


class WorkflowScope(StrEnum):
    COMPANY = "COMPANY"
    PROJECT = "PROJECT"
