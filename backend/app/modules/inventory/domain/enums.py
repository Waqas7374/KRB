"""Inventory enumerations."""

from __future__ import annotations

from enum import StrEnum


class TxnType(StrEnum):
    GRN_IN = "GRN_IN"
    ISSUE_OUT = "ISSUE_OUT"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    ADJUST_IN = "ADJUST_IN"
    ADJUST_OUT = "ADJUST_OUT"
    RETURN_IN = "RETURN_IN"
    RETURN_OUT = "RETURN_OUT"
    # A contra row that undoes an earlier one (a cancelled GRN...). It moves
    # stock the opposite way of the row it reverses, at that row's own cost.
    REVERSAL_IN = "REVERSAL_IN"
    REVERSAL_OUT = "REVERSAL_OUT"

    @property
    def is_inbound(self) -> bool:
        return self in {
            TxnType.GRN_IN,
            TxnType.TRANSFER_IN,
            TxnType.ADJUST_IN,
            TxnType.RETURN_IN,
            TxnType.REVERSAL_IN,
        }
