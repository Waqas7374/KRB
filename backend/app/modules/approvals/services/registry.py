"""The integration contract (docs/04 §3).

The engine knows nothing about purchase requests. A module opts in by
registering a handler for its document type at import time:

    registry.register(PurchaseRequestApprovals())

and then calls `engine.submit(...)`. The engine calls back into the handler,
inside the same transaction, when the request is decided — so the document's
own status changes atomically with the approval.

A registry (rather than the engine importing each module) keeps the
dependency one-way: procurement depends on approvals, never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class DocumentTypeSpec:
    doc_type: str
    label: str
    # The permission an approver must hold, in a scope covering the document,
    # to decide a step. Also checked against every ROLE step when a workflow
    # is saved, so a chain naming a role that cannot approve is refused then.
    approve_permission: str
    # Documented, stable context variables (docs/04 §1). A `.*` entry admits
    # its dotted children: "project.*" allows "project.code".
    context_variables: frozenset[str]


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    """What a handler is told when a request ends."""

    request_id: UUID
    doc_id: UUID
    status: str
    actor_user_id: UUID | None
    reason: str | None


class ApprovalDocumentHandler(Protocol):
    spec: DocumentTypeSpec

    async def current_hash(self, session: AsyncSession, doc_id: UUID) -> str | None:
        """Hash of the document's approval-relevant content right now. A
        mismatch with the hash taken at submission auto-recalls the request."""
        ...

    async def on_approved(self, session: AsyncSession, outcome: ApprovalOutcome) -> None: ...

    async def on_rejected(self, session: AsyncSession, outcome: ApprovalOutcome) -> None: ...

    async def on_changes_requested(
        self, session: AsyncSession, outcome: ApprovalOutcome
    ) -> None: ...

    async def on_recalled(self, session: AsyncSession, outcome: ApprovalOutcome) -> None: ...

    # Optional, used by the nightly integrity check (services/integrity.py):
    #
    #   async def pending_document_ids(self, session) -> dict[UUID, UUID]:
    #       """doc id -> company id for every document awaiting approval."""


_HANDLERS: dict[str, ApprovalDocumentHandler] = {}


def register(handler: ApprovalDocumentHandler) -> None:
    _HANDLERS[handler.spec.doc_type] = handler


def get(doc_type: str) -> ApprovalDocumentHandler:
    try:
        return _HANDLERS[doc_type]
    except KeyError:
        from app.core.errors import BusinessRuleError

        raise BusinessRuleError(
            "approval_doc_type_unknown",
            f"'{doc_type}' is not a document type that goes through approvals.",
        ) from None


def specs() -> list[DocumentTypeSpec]:
    return sorted((h.spec for h in _HANDLERS.values()), key=lambda s: s.label)
