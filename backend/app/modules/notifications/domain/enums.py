"""Notification enumerations."""

from __future__ import annotations

from enum import StrEnum


class NotificationChannel(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    # Adapters not implemented in v1; the column accepts them so enabling a
    # channel is a deployment change rather than a migration.
    PUSH = "PUSH"
    SMS = "SMS"


class NotificationPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    # Bypasses digesting and quiet hours: stock-out at an active site,
    # a payment awaiting approval on its due date.
    URGENT = "URGENT"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    # The user turned this type off for this channel.
    SUPPRESSED = "SUPPRESSED"


class NotificationType(StrEnum):
    """The catalogue of things the system tells people about (§26).

    Kept as an enum so preferences and templates cannot drift apart from the
    code that raises them.
    """

    # Procurement
    PR_SUBMITTED = "PR_SUBMITTED"
    PR_APPROVED = "PR_APPROVED"
    PR_REJECTED = "PR_REJECTED"
    PO_AWAITING_APPROVAL = "PO_AWAITING_APPROVAL"
    PO_APPROVED = "PO_APPROVED"
    RFQ_RESPONSE_RECEIVED = "RFQ_RESPONSE_RECEIVED"

    # Deliveries
    DELIVERY_REQUIRES_REVIEW = "DELIVERY_REQUIRES_REVIEW"
    DELIVERY_APPROVED = "DELIVERY_APPROVED"
    DELIVERY_REJECTED = "DELIVERY_REJECTED"
    DELIVERY_CORRECTION_REQUESTED = "DELIVERY_CORRECTION_REQUESTED"

    # Inventory
    STOCK_BELOW_MINIMUM = "STOCK_BELOW_MINIMUM"
    ADJUSTMENT_AWAITING_APPROVAL = "ADJUSTMENT_AWAITING_APPROVAL"

    # Finance
    INVOICE_MATCH_FAILED = "INVOICE_MATCH_FAILED"
    PAYMENT_AWAITING_APPROVAL = "PAYMENT_AWAITING_APPROVAL"
    BUDGET_THRESHOLD_EXCEEDED = "BUDGET_THRESHOLD_EXCEEDED"

    # Rates and rules
    RATE_CHANGE_AWAITING_APPROVAL = "RATE_CHANGE_AWAITING_APPROVAL"
    RATE_CHANGED = "RATE_CHANGED"

    # Approvals (generic — the document type is in the notification's text and
    # link, so one set of types serves every document the engine routes)
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVAL_REMINDER = "APPROVAL_REMINDER"
    APPROVAL_ESCALATED = "APPROVAL_ESCALATED"
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_CHANGES_REQUESTED = "APPROVAL_CHANGES_REQUESTED"
    APPROVAL_RECALLED = "APPROVAL_RECALLED"
    APPROVAL_STUCK = "APPROVAL_STUCK"
    APPROVAL_INTEGRITY_ALARM = "APPROVAL_INTEGRITY_ALARM"
    INVENTORY_INTEGRITY_ALARM = "INVENTORY_INTEGRITY_ALARM"

    # HR
    LEAVE_REQUESTED = "LEAVE_REQUESTED"
    LEAVE_APPROVED = "LEAVE_APPROVED"
    LEAVE_REJECTED = "LEAVE_REJECTED"
    DOCUMENT_EXPIRING = "DOCUMENT_EXPIRING"

    # Account
    WELCOME = "WELCOME"
    PASSWORD_RESET = "PASSWORD_RESET"
    NEW_DEVICE_SIGN_IN = "NEW_DEVICE_SIGN_IN"
    ROLE_GRANTED = "ROLE_GRANTED"

    # Vendors
    VENDOR_STATUS_CHANGED = "VENDOR_STATUS_CHANGED"
