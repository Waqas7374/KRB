"""The checks a delivery is put through (docs/05 §2, §3). Pure: plain numbers in,
flags out."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.modules.deliveries.domain import evaluation
from app.modules.deliveries.domain.enums import FlagSeverity, FlagType, LocationSource

D = Decimal
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class TestTonnage:
    def test_within_the_limit_raises_nothing(self) -> None:
        assert evaluation.check_tonnage(D("16"), D("16")) is None
        assert evaluation.check_tonnage(D("12.5"), D("16")) is None

    def test_the_docs_example_22_tons_on_a_16_ton_truck(self) -> None:
        flag = evaluation.check_tonnage(D("22"), D("16"), subject="10-wheeler / Crush")
        assert flag is not None
        assert flag.flag_type is FlagType.TONNAGE_ANOMALY
        assert flag.deviation_pct == D("37.50")
        assert flag.expected_value == D("16") and flag.actual_value == D("22")
        assert flag.message == "Tonnage 22.0t exceeds 16.0t maximum for 10-wheeler / Crush"

    def test_a_modest_overload_is_a_warning_and_a_large_one_critical(self) -> None:
        modest = evaluation.check_tonnage(D("19"), D("16"))  # +18.75 %
        heavy = evaluation.check_tonnage(D("22"), D("16"))  # +37.5 %
        boundary = evaluation.check_tonnage(D("20"), D("16"))  # exactly +25 %
        assert modest and modest.severity is FlagSeverity.WARNING
        assert heavy and heavy.severity is FlagSeverity.CRITICAL
        assert boundary and boundary.severity is FlagSeverity.WARNING


class TestPoBalance:
    def _check(self, *, before: str, this: str, pct: str = "2", abs_: str = "0.5") -> object:
        return evaluation.check_po_balance(
            ordered=D("100"),
            received_before=D(before),
            this_delivery=D(this),
            tolerance_pct=D(pct),
            tolerance_abs=D(abs_),
        )

    def test_receiving_up_to_the_order_is_fine(self) -> None:
        assert self._check(before="60", this="40") is None

    def test_the_percentage_tolerance_applies_when_it_is_the_larger(self) -> None:
        assert self._check(before="60", this="42") is None  # 102 == 100 + 2 %
        assert self._check(before="60", this="42.01") is not None

    def test_the_absolute_tolerance_applies_when_it_is_the_larger(self) -> None:
        # 2 % of 10 is 0.2, but 0.5 is allowed.
        flag = evaluation.check_po_balance(
            ordered=D("10"),
            received_before=D("0"),
            this_delivery=D("10.5"),
            tolerance_pct=D("2"),
            tolerance_abs=D("0.5"),
        )
        assert flag is None

    def test_the_message_says_how_far_over(self) -> None:
        flag = self._check(before="60", this="50")
        assert flag is not None
        assert flag.flag_type is FlagType.PO_QTY_EXCEEDED  # type: ignore[attr-defined]
        assert flag.deviation_pct == D("10.00")  # type: ignore[attr-defined]
        assert "110" in flag.message  # type: ignore[attr-defined]


class TestDailyCap:
    def test_delivery_count_cap(self) -> None:
        ok = evaluation.check_daily_cap(
            deliveries_today=5, quantity_today=D("1"), max_deliveries=5, max_quantity=None
        )
        over = evaluation.check_daily_cap(
            deliveries_today=6, quantity_today=D("1"), max_deliveries=5, max_quantity=None
        )
        assert ok is None
        assert over and over.flag_type is FlagType.DAILY_CAP_EXCEEDED

    def test_quantity_cap(self) -> None:
        over = evaluation.check_daily_cap(
            deliveries_today=1, quantity_today=D("101"), max_deliveries=None, max_quantity=D("100")
        )
        assert over and "cap of 100" in over.message

    def test_no_cap_no_flag(self) -> None:
        assert (
            evaluation.check_daily_cap(
                deliveries_today=999,
                quantity_today=D("1e6"),
                max_deliveries=None,
                max_quantity=None,
            )
            is None
        )


class TestGeofence:
    manual = LocationSource.MANUAL.value
    gps = LocationSource.GPS.value

    def test_inside_the_fence_raises_nothing(self) -> None:
        assert (
            evaluation.check_geofence(
                distance_outside_m=D("0"), gps_accuracy_m=D("8"), location_source=self.gps
            )
            is None
        )

    def test_no_position_at_all_is_a_warning(self) -> None:
        for source in (self.gps, self.manual):
            flag = evaluation.check_geofence(
                distance_outside_m=None, gps_accuracy_m=None, location_source=source
            )
            assert flag and flag.severity is FlagSeverity.WARNING and flag.actual_value is None

    def test_a_hand_entered_location_is_flagged_even_with_coordinates(self) -> None:
        flag = evaluation.check_geofence(
            distance_outside_m=D("0"), gps_accuracy_m=None, location_source=self.manual
        )
        assert flag and "entered by hand" in flag.message

    def test_within_gps_accuracy_is_noted_not_accused(self) -> None:
        flag = evaluation.check_geofence(
            distance_outside_m=D("12"), gps_accuracy_m=D("30"), location_source=self.gps
        )
        assert flag and flag.severity is FlagSeverity.INFO

    def test_distance_bands(self) -> None:
        near = evaluation.check_geofence(
            distance_outside_m=D("140"), gps_accuracy_m=D("8"), location_source=self.gps
        )
        far = evaluation.check_geofence(
            distance_outside_m=D("640"), gps_accuracy_m=D("8"), location_source=self.gps
        )
        edge = evaluation.check_geofence(
            distance_outside_m=D("200"), gps_accuracy_m=D("8"), location_source=self.gps
        )
        assert near and near.severity is FlagSeverity.WARNING
        assert far and far.severity is FlagSeverity.CRITICAL and far.message == "640 m outside site"
        assert edge and edge.severity is FlagSeverity.CRITICAL  # 200 m is the first CRITICAL

    def test_a_poor_fix_is_never_critical_by_itself(self) -> None:
        flag = evaluation.check_geofence(
            distance_outside_m=D("640"), gps_accuracy_m=D("150"), location_source=self.gps
        )
        assert flag and flag.severity is FlagSeverity.WARNING


class TestClockSkew:
    def test_within_the_limit(self) -> None:
        assert evaluation.check_clock_skew(skew_seconds=-900, max_seconds=900) is None
        assert evaluation.check_clock_skew(skew_seconds=900, max_seconds=900) is None

    def test_beyond_the_limit_either_way(self) -> None:
        ahead = evaluation.check_clock_skew(skew_seconds=7200, max_seconds=900)
        behind = evaluation.check_clock_skew(skew_seconds=-7200, max_seconds=900)
        assert ahead and "120 min ahead of" in ahead.message
        assert behind and "behind" in behind.message


class TestLateSubmission:
    def test_recent_is_fine(self) -> None:
        assert (
            evaluation.check_late_submission(
                captured_at=NOW - timedelta(days=2), received_at=NOW, max_age_days=2
            )
            is None
        )

    def test_older_than_the_limit_is_flagged_with_the_age(self) -> None:
        flag = evaluation.check_late_submission(
            captured_at=NOW - timedelta(days=5, hours=12), received_at=NOW, max_age_days=2
        )
        assert flag and flag.flag_type is FlagType.LATE_SUBMISSION
        assert flag.actual_value == D("5.50") and "limit 2" in flag.message


class TestFlagDraft:
    def test_attaching_the_rule_keeps_everything_else(self) -> None:
        flag = evaluation.check_tonnage(D("22"), D("16"))
        assert flag is not None
        with_rule = flag.with_rule("rule-1", {"value": {"max": "16"}})
        assert with_rule.rule_id == "rule-1" and with_rule.rule_snapshot == {"value": {"max": "16"}}
        assert with_rule.message == flag.message and with_rule.deviation_pct == flag.deviation_pct

    def test_worst_picks_the_highest_severity(self) -> None:
        info = evaluation.FlagDraft(FlagType.CLOCK_SKEW, FlagSeverity.INFO, "i")
        crit = evaluation.FlagDraft(FlagType.TONNAGE_ANOMALY, FlagSeverity.CRITICAL, "c")
        warn = evaluation.FlagDraft(FlagType.NO_PO, FlagSeverity.WARNING, "w")
        assert evaluation.worst([info, crit, warn]) is FlagSeverity.CRITICAL
        assert evaluation.worst([]) is None
