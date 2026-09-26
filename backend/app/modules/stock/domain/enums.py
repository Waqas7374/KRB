"""Stock-movement enumerations: issues, transfers, adjustments."""

from __future__ import annotations

from enum import StrEnum


class IssueStatus(StrEnum):
    DRAFT = "DRAFT"
    # Stock has left the store.
    ISSUED = "ISSUED"
    CANCELLED = "CANCELLED"


class IssuedToType(StrEnum):
    EMPLOYEE = "EMPLOYEE"
    CONTRACTOR = "CONTRACTOR"
    WORK_ORDER = "WORK_ORDER"


class TransferStatus(StrEnum):
    DRAFT = "DRAFT"
    # Left the source store, not yet counted in at the destination.
    IN_TRANSIT = "IN_TRANSIT"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class AdjustmentStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    # Approved *and* on the ledger: an adjustment is posted in the same
    # transaction as its approval, so there is no state between the two.
    POSTED = "POSTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

    @property
    def is_editable(self) -> bool:
        return self in {AdjustmentStatus.DRAFT, AdjustmentStatus.REJECTED}


class AdjustmentReason(StrEnum):
    """Why stock is being corrected. Required: an adjustment without a reason
    is exactly how stock quietly disappears (docs/02 §22)."""

    COUNT_CORRECTION = "COUNT_CORRECTION"
    DAMAGE = "DAMAGE"
    WASTAGE = "WASTAGE"
    LOSS = "LOSS"
    THEFT = "THEFT"
    EXPIRED = "EXPIRED"
    OPENING_BALANCE = "OPENING_BALANCE"
    OTHER = "OTHER"
