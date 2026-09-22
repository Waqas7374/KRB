"""Audit enumerations."""

from __future__ import annotations

from enum import StrEnum


class AuditAction(StrEnum):
    """What kind of change a log row records.

    Deliberately coarser than the endpoint list: an auditor asks "who approved
    this" and "who changed the rate", not "which HTTP verb was used".
    """

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    # Master data only; transactional records are cancelled, not deleted.
    SOFT_DELETE = "SOFT_DELETE"
    RESTORE = "RESTORE"

    # State machine transitions
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    CLOSE = "CLOSE"
    REOPEN = "REOPEN"
    REVERSE = "REVERSE"
    POST = "POST"
    REQUEST_CORRECTION = "REQUEST_CORRECTION"
    WAIVE = "WAIVE"

    # Security-relevant events
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    PASSWORD_RESET = "PASSWORD_RESET"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    ROLE_GRANTED = "ROLE_GRANTED"
    ROLE_REVOKED = "ROLE_REVOKED"
    DEVICE_REGISTERED = "DEVICE_REGISTERED"
    DEVICE_REVOKED = "DEVICE_REVOKED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # High-risk business changes that get their own label so they can be
    # reported on directly.
    RATE_CHANGE = "RATE_CHANGE"
    BANK_DETAILS_CHANGE = "BANK_DETAILS_CHANGE"
    RULE_CHANGE = "RULE_CHANGE"
    WORKFLOW_CHANGE = "WORKFLOW_CHANGE"
    PERIOD_CLOSE = "PERIOD_CLOSE"
    EXPORT = "EXPORT"
