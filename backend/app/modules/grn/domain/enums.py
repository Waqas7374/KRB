"""GRN enumerations."""

from __future__ import annotations

from enum import StrEnum


class GrnStatus(StrEnum):
    DRAFT = "DRAFT"
    # Stock has moved. (docs/02 also names PENDING_APPROVAL and APPROVED; they
    # arrive with finance in Phase 4, when posting also books the general
    # ledger and a GRN may need an approval of its own.)
    POSTED = "POSTED"
    CANCELLED = "CANCELLED"


class InspectionResult(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
