"""Vendor-rate enumerations."""

from __future__ import annotations

from enum import StrEnum


class RateStatus(StrEnum):
    # Submitted; not in force until the approval engine says so.
    PENDING_APPROVAL = "PENDING_APPROVAL"
    # Approved. Whether it is *current* depends on its dates.
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    # The author recalled it, or it was returned for changes.
    WITHDRAWN = "WITHDRAWN"


class RateSource(StrEnum):
    MANUAL = "MANUAL"
    PO = "PO"
    QUOTATION = "QUOTATION"
