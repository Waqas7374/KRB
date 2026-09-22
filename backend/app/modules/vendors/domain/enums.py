"""Vendor enumerations."""

from __future__ import annotations

from enum import StrEnum


class VendorType(StrEnum):
    MATERIAL_SUPPLIER = "MATERIAL_SUPPLIER"
    # Civil contractor. Receives POs in v1; BOQ and running-account billing
    # arrive in Phase 8 (docs/12-confirmed-decisions.md Q4).
    CONTRACTOR = "CONTRACTOR"
    SUBCONTRACTOR = "SUBCONTRACTOR"
    TRANSPORTER = "TRANSPORTER"
    SERVICE_PROVIDER = "SERVICE_PROVIDER"
    CONSULTANT = "CONSULTANT"
    EQUIPMENT_RENTAL = "EQUIPMENT_RENTAL"


class VendorStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    # Temporarily stopped: existing POs stand, no new ones.
    SUSPENDED = "SUSPENDED"
    # Permanently barred. Kept, never deleted, because historical purchases
    # still reference it.
    BLACKLISTED = "BLACKLISTED"
    INACTIVE = "INACTIVE"

    @property
    def can_receive_orders(self) -> bool:
        return self is VendorStatus.ACTIVE
