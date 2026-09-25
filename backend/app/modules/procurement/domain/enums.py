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


class RfqStatus(StrEnum):
    DRAFT = "DRAFT"
    # Sent to the invited vendors; quotations may now be recorded.
    ISSUED = "ISSUED"
    # A quotation was selected and ordered, or the buyer closed it.
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class RfqVendorStatus(StrEnum):
    INVITED = "INVITED"
    QUOTED = "QUOTED"
    DECLINED = "DECLINED"
    NO_RESPONSE = "NO_RESPONSE"


class QuotationStatus(StrEnum):
    RECEIVED = "RECEIVED"
    SHORTLISTED = "SHORTLISTED"
    # The winner. Never set automatically (§10): a person picks it and says why.
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    # Set by goods receipt (Phase 3).
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

    @property
    def is_editable(self) -> bool:
        return self in _PO_EDITABLE

    @property
    def is_committed(self) -> bool:
        """The order stands: quantities count against the purchase request."""
        return self in _PO_COMMITTED


_PO_EDITABLE = frozenset(
    {
        PurchaseOrderStatus.DRAFT,
        PurchaseOrderStatus.REJECTED,
        PurchaseOrderStatus.CHANGES_REQUESTED,
    }
)
_PO_COMMITTED = frozenset(
    {
        PurchaseOrderStatus.APPROVED,
        PurchaseOrderStatus.SENT,
        PurchaseOrderStatus.ACKNOWLEDGED,
        PurchaseOrderStatus.PARTIALLY_RECEIVED,
        PurchaseOrderStatus.RECEIVED,
    }
)
