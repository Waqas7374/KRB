"""The checks a delivery is put through at ingest (docs/05 §2, §3).

Pure functions: each takes the facts and the threshold, and returns a
`FlagDraft` or None. Nothing here reads the database or the clock, so every
threshold and every band is tested with plain numbers.

**None of these reject a delivery.** A truck arrived; the record must exist
whatever the checks say. A flag routes it to a person; it never discards it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.modules.deliveries.domain.enums import FlagSeverity, FlagType, LocationSource

_HUNDRED = Decimal(100)

# Overloaded by more than a quarter is not a rounding error.
TONNAGE_CRITICAL_PCT = Decimal(25)
# Up to this far outside the fence is worth a look; beyond it, a problem.
GEOFENCE_CRITICAL_M = Decimal(200)
# A fix this poor (a truck under a shed roof) cannot on its own prove a delivery
# was elsewhere, so it is never CRITICAL by itself: otherwise every such
# delivery becomes an exception and reviewers learn to click through.
GPS_UNRELIABLE_M = Decimal(100)


@dataclass(frozen=True, slots=True)
class FlagDraft:
    flag_type: FlagType
    severity: FlagSeverity
    message: str
    expected_value: Decimal | None = None
    actual_value: Decimal | None = None
    deviation_pct: Decimal | None = None
    # Set by the caller from the rule that produced the threshold, so the flag
    # keeps what was in force when it fired.
    rule_id: Any = None
    rule_snapshot: dict[str, Any] = field(default_factory=dict)

    def with_rule(self, rule_id: Any, snapshot: dict[str, Any]) -> FlagDraft:
        return FlagDraft(
            self.flag_type,
            self.severity,
            self.message,
            self.expected_value,
            self.actual_value,
            self.deviation_pct,
            rule_id,
            snapshot,
        )


def _q(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places))


def worst(flags: list[FlagDraft]) -> FlagSeverity | None:
    return max((f.severity for f in flags), key=lambda s: s.rank, default=None)


# -----------------------------------------------------------------------------
# Quantity
# -----------------------------------------------------------------------------


def check_tonnage(
    actual_tons: Decimal, max_tons: Decimal, *, subject: str = ""
) -> FlagDraft | None:
    """A load heavier than the resolved maximum for this truck and material."""
    if actual_tons <= max_tons:
        return None
    deviation = _q((actual_tons - max_tons) / max_tons * _HUNDRED)
    severity = FlagSeverity.CRITICAL if deviation > TONNAGE_CRITICAL_PCT else FlagSeverity.WARNING
    where = f" for {subject}" if subject else ""
    return FlagDraft(
        FlagType.TONNAGE_ANOMALY,
        severity,
        f"Tonnage {actual_tons:.1f}t exceeds {max_tons:.1f}t maximum{where}",
        expected_value=max_tons,
        actual_value=actual_tons,
        deviation_pct=deviation,
    )


def check_po_balance(
    *,
    ordered: Decimal,
    received_before: Decimal,
    this_delivery: Decimal,
    tolerance_pct: Decimal,
    tolerance_abs: Decimal,
) -> FlagDraft | None:
    """Received to date plus this load must stay within the ordered quantity
    plus the tolerance — the greater of a percentage and an absolute amount."""
    allowance = max(ordered * tolerance_pct / _HUNDRED, tolerance_abs)
    limit = ordered + allowance
    total = received_before + this_delivery
    if total <= limit:
        return None
    over = total - ordered
    return FlagDraft(
        FlagType.PO_QTY_EXCEEDED,
        FlagSeverity.WARNING,
        f"With this load {total:f} has arrived against {ordered:f} ordered "
        f"({over:f} over; tolerance {allowance:f})",
        expected_value=ordered,
        actual_value=total,
        deviation_pct=_q(over / ordered * _HUNDRED) if ordered else None,
    )


def check_daily_cap(
    *,
    deliveries_today: int,
    quantity_today: Decimal,
    max_deliveries: int | None,
    max_quantity: Decimal | None,
) -> FlagDraft | None:
    """`deliveries_today` and `quantity_today` include the delivery being checked."""
    if max_deliveries is not None and deliveries_today > max_deliveries:
        return FlagDraft(
            FlagType.DAILY_CAP_EXCEEDED,
            FlagSeverity.WARNING,
            f"{deliveries_today} deliveries today against a cap of {max_deliveries}",
            expected_value=Decimal(max_deliveries),
            actual_value=Decimal(deliveries_today),
        )
    if max_quantity is not None and quantity_today > max_quantity:
        return FlagDraft(
            FlagType.DAILY_CAP_EXCEEDED,
            FlagSeverity.WARNING,
            f"{quantity_today:f} received today against a cap of {max_quantity:f}",
            expected_value=max_quantity,
            actual_value=quantity_today,
        )
    return None


# -----------------------------------------------------------------------------
# Place and time
# -----------------------------------------------------------------------------


def check_geofence(
    *,
    distance_outside_m: Decimal | None,
    gps_accuracy_m: Decimal | None,
    location_source: str,
) -> FlagDraft | None:
    """`distance_outside_m` is 0 inside the fence and the metres beyond it
    otherwise; None means no position was captured at all."""
    if distance_outside_m is None or location_source == LocationSource.MANUAL.value:
        return FlagDraft(
            FlagType.GEOFENCE_MISMATCH,
            FlagSeverity.WARNING,
            "No GPS position was captured; the location was entered by hand",
        )
    if distance_outside_m <= 0:
        return None
    if gps_accuracy_m is not None and distance_outside_m <= gps_accuracy_m:
        # The fix cannot tell "just outside" from "just inside": note it, do not accuse.
        return FlagDraft(
            FlagType.GEOFENCE_MISMATCH,
            FlagSeverity.INFO,
            f"{distance_outside_m:.0f} m outside the boundary, within the GPS accuracy of "
            f"±{gps_accuracy_m:.0f} m",
            actual_value=distance_outside_m,
        )
    severity = (
        FlagSeverity.CRITICAL
        if distance_outside_m >= GEOFENCE_CRITICAL_M
        and not (gps_accuracy_m is not None and gps_accuracy_m > GPS_UNRELIABLE_M)
        else FlagSeverity.WARNING
    )
    return FlagDraft(
        FlagType.GEOFENCE_MISMATCH,
        severity,
        f"{distance_outside_m:.0f} m outside site",
        expected_value=Decimal(0),
        actual_value=distance_outside_m,
    )


def check_clock_skew(*, skew_seconds: int, max_seconds: int) -> FlagDraft | None:
    """How far the device's clock is from the server's. Never corrected: both
    times are stored, and a large gap is itself something a reviewer should see."""
    if abs(skew_seconds) <= max_seconds:
        return None
    direction = "ahead of" if skew_seconds > 0 else "behind"
    return FlagDraft(
        FlagType.CLOCK_SKEW,
        FlagSeverity.WARNING,
        f"The device clock is {abs(skew_seconds) // 60} min {direction} the server",
        expected_value=Decimal(max_seconds),
        actual_value=Decimal(abs(skew_seconds)),
    )


def check_late_submission(
    *, captured_at: datetime, received_at: datetime, max_age_days: int
) -> FlagDraft | None:
    """A delivery reaching the server long after it was captured. Offline
    capture makes some delay normal; beyond the threshold it is worth a look."""
    age = received_at - captured_at
    if age.total_seconds() <= max_age_days * 86_400:
        return None
    days = age.total_seconds() / 86_400
    return FlagDraft(
        FlagType.LATE_SUBMISSION,
        FlagSeverity.WARNING,
        f"Captured {days:.1f} days before it reached the server (limit {max_age_days})",
        expected_value=Decimal(max_age_days),
        actual_value=_q(Decimal(str(days))),
    )
