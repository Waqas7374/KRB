"""Attachment enumerations."""

from __future__ import annotations

from enum import StrEnum


class ScanStatus(StrEnum):
    """Malware-scan state.

    SKIPPED is the honest default in v1: no scanner is wired up, and claiming
    PENDING would imply a queue that does not exist. Uploads are still
    constrained by magic-byte type checking and a size cap.
    """

    SKIPPED = "SKIPPED"
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    FAILED = "FAILED"


class EntityType(StrEnum):
    """Entities that can carry attachments (§28)."""

    VENDOR = "vendor"
    EMPLOYEE = "employee"
    PROJECT = "project"
    SITE = "site"
    PURCHASE_REQUEST = "purchase_request"
    RFQ = "rfq"
    QUOTATION = "quotation"
    PURCHASE_ORDER = "purchase_order"
    DELIVERY = "delivery"
    GRN = "grn"
    VENDOR_INVOICE = "vendor_invoice"
    PAYMENT = "payment"
    JOURNAL_ENTRY = "journal_entry"
    BUDGET = "budget"
    STOCK_ADJUSTMENT = "stock_adjustment"
    LEAVE_REQUEST = "leave_request"
