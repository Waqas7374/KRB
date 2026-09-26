"""Delivery enumerations (docs/02 §6)."""

from __future__ import annotations

from enum import StrEnum


class DeliveryStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    # At least one open flag of WARNING or worse: head office must look at it.
    UNDER_REVIEW = "UNDER_REVIEW"
    # The reviewer sent it back to the person who captured it.
    CORRECTION_REQUESTED = "CORRECTION_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    # Set once a GRN has been raised from it (slice 3e).
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"

    @property
    def is_editable(self) -> bool:
        """The capturer may still correct it."""
        return self in {DeliveryStatus.DRAFT, DeliveryStatus.CORRECTION_REQUESTED}

    @property
    def is_terminal(self) -> bool:
        return self in {
            DeliveryStatus.REJECTED,
            DeliveryStatus.RECEIVED,
            DeliveryStatus.CANCELLED,
        }


class FlagType(StrEnum):
    TONNAGE_ANOMALY = "TONNAGE_ANOMALY"
    GEOFENCE_MISMATCH = "GEOFENCE_MISMATCH"
    CLOCK_SKEW = "CLOCK_SKEW"
    DUPLICATE_SUSPECT = "DUPLICATE_SUSPECT"
    NO_PO = "NO_PO"
    PO_QTY_EXCEEDED = "PO_QTY_EXCEEDED"
    DAILY_CAP_EXCEEDED = "DAILY_CAP_EXCEEDED"
    RATE_MISSING = "RATE_MISSING"
    # A quantity that cannot be priced because no conversion factor is set up
    # between the unit it was counted in and the unit the vendor prices in.
    CONVERSION_MISSING = "CONVERSION_MISSING"
    VENDOR_INACTIVE = "VENDOR_INACTIVE"
    LATE_SUBMISSION = "LATE_SUBMISSION"


class FlagSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def needs_review(self) -> bool:
        return self is not FlagSeverity.INFO

    @property
    def rank(self) -> int:
        return {"INFO": 0, "WARNING": 1, "CRITICAL": 2}[self.value]


class FlagStatus(StrEnum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CORRECTED = "CORRECTED"
    WAIVED = "WAIVED"


class ReviewAction(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REQUEST_CORRECTION = "REQUEST_CORRECTION"
    # The capturer's answer to a correction request.
    CORRECTION_SUBMITTED = "CORRECTION_SUBMITTED"
    REOPEN = "REOPEN"
    # Head office attaching an order after the fact (docs/12 Q3).
    ATTACH_PO = "ATTACH_PO"


class LocationSource(StrEnum):
    GPS = "GPS"
    NETWORK = "NETWORK"
    MANUAL = "MANUAL"
