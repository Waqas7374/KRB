"""Procurement enumerations."""

from __future__ import annotations

from enum import StrEnum


class PurchaseRequestStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    CANCELLED = "CANCELLED"
    # Set by purchase orders as they source the request's lines (Phase 2, POs).
    PARTIALLY_SOURCED = "PARTIALLY_SOURCED"
    SOURCED = "SOURCED"

    @property
    def is_editable(self) -> bool:
        """The author may change the request and (re)submit it."""
        return self in _EDITABLE


_EDITABLE = frozenset(
    {
        PurchaseRequestStatus.DRAFT,
        PurchaseRequestStatus.REJECTED,
        PurchaseRequestStatus.CHANGES_REQUESTED,
    }
)


class PurchaseRequestPriority(StrEnum):
    NORMAL = "NORMAL"
    URGENT = "URGENT"
