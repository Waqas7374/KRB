"""Business-rule resolution and value validation (docs/05 §1). Pure: no database."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.rules.domain import resolution
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.domain.resolution import RuleView
from app.modules.rules.domain.rule_types import RULE_TYPES, SCOPE_KEYS, validate_value

TODAY = date(2026, 9, 25)
TRUCK, CRUSH, SITE2, SITE1 = "truck-10w", "mat-crush", "site-2", "site-1"


def rule(
    *,
    scope: dict[str, str] | None = None,
    value: Any = None,
    priority: int = 0,
    start: date = date(2026, 1, 1),
    end: date | None = None,
    active: bool = True,
    condition: Any = None,
    age_days: int = 0,
) -> RuleView:
    return RuleView(
        id=uuid4(),
        rule_type="TONNAGE_MAX",
        name=None,
        scope=scope or {},
        condition=condition,
        value=value or {"max": "16"},
        priority=priority,
        effective_from=start,
        effective_to=end,
        is_active=active,
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=age_days),
    )


class TestSpecificity:
    def test_the_docs_example_most_specific_rule_wins(self) -> None:
        default = rule(scope={"truck_type_id": TRUCK}, value={"max": "16"})
        crush = rule(scope={"truck_type_id": TRUCK, "material_id": CRUSH}, value={"max": "18"})
        site2 = rule(
            scope={"truck_type_id": TRUCK, "material_id": CRUSH, "site_id": SITE2},
            value={"max": "14"},
        )
        rules = [default, crush, site2]

        def resolved(**context: str) -> Any:
            winner = resolution.pick(rules, context, TODAY)
            return winner.value["max"] if winner else None

        assert resolved(truck_type_id=TRUCK) == "16"
        assert resolved(truck_type_id=TRUCK, material_id=CRUSH, site_id=SITE1) == "18"
        assert resolved(truck_type_id=TRUCK, material_id=CRUSH, site_id=SITE2) == "14"
        assert resolved(truck_type_id="other") is None

    def test_a_company_default_is_the_fallback_for_everything(self) -> None:
        company = rule(value={"max": "20"})
        specific = rule(scope={"site_id": SITE2}, value={"max": "10"})
        assert resolution.pick([company, specific], {"site_id": SITE1}, TODAY) is company
        assert resolution.pick([company, specific], {"site_id": SITE2}, TODAY) is specific

    def test_a_rule_scoped_to_something_the_context_lacks_does_not_apply(self) -> None:
        # A site-specific rule must not fire for a document that names no site.
        assert resolution.pick([rule(scope={"site_id": SITE2})], {}, TODAY) is None
        assert resolution.pick([rule(scope={"site_id": SITE2})], {"site_id": None}, TODAY) is None

    def test_ids_are_compared_as_text(self) -> None:
        from uuid import UUID

        site = UUID(int=7)
        assert resolution.pick([rule(scope={"site_id": str(site)})], {"site_id": site}, TODAY)


class TestTieBreaks:
    def test_priority_beats_age_at_equal_specificity(self) -> None:
        low = rule(scope={"site_id": SITE1}, priority=0, age_days=5)
        high = rule(scope={"site_id": SITE1}, priority=5, age_days=0)
        assert resolution.pick([low, high], {"site_id": SITE1}, TODAY) is high

    def test_specificity_beats_priority(self) -> None:
        broad = rule(priority=999)
        narrow = rule(scope={"site_id": SITE1}, priority=0)
        assert resolution.pick([broad, narrow], {"site_id": SITE1}, TODAY) is narrow

    def test_later_start_then_newer_wins_last(self) -> None:
        old = rule(start=date(2026, 1, 1))
        new = rule(start=date(2026, 6, 1))
        assert resolution.pick([old, new], {}, TODAY) is new
        a, b = rule(age_days=0), rule(age_days=3)
        assert resolution.pick([a, b], {}, TODAY) is b


class TestEffectiveDatesAndState:
    def test_a_rule_applies_on_its_first_and_last_day_inclusive(self) -> None:
        r = rule(start=date(2026, 9, 1), end=date(2026, 9, 30))
        assert resolution.pick([r], {}, date(2026, 9, 1)) is r
        assert resolution.pick([r], {}, date(2026, 9, 30)) is r
        assert resolution.pick([r], {}, date(2026, 8, 31)) is None
        assert resolution.pick([r], {}, date(2026, 10, 1)) is None

    def test_history_resolves_against_the_rule_of_the_day(self) -> None:
        before = rule(start=date(2026, 1, 1), end=date(2026, 6, 30), value={"max": "16"})
        after = rule(start=date(2026, 7, 1), value={"max": "14"})
        rules = [before, after]
        assert resolution.pick(rules, {}, date(2026, 3, 1)) is before
        assert resolution.pick(rules, {}, date(2026, 9, 1)) is after

    def test_an_inactive_rule_never_applies(self) -> None:
        assert resolution.pick([rule(active=False)], {}, TODAY) is None


class TestConditions:
    def test_a_condition_is_evaluated_over_the_context(self) -> None:
        big_orders = rule(condition={">": [{"var": "amount"}, 100000]}, value={"max": "1"})
        assert resolution.pick([big_orders], {"amount": 200000}, TODAY) is big_orders
        assert resolution.pick([big_orders], {"amount": 5}, TODAY) is None

    def test_a_missing_variable_does_not_match(self) -> None:
        r = rule(condition={">": [{"var": "amount"}, 1]})
        assert resolution.pick([r], {}, TODAY) is None


class TestExplain:
    def test_every_rule_gets_a_reason(self) -> None:
        winner_rule = rule(scope={"site_id": SITE1})
        other_site = rule(scope={"site_id": SITE2})
        ended = rule(end=date(2026, 1, 31))
        off = rule(active=False)
        winner, verdicts = resolution.explain(
            [winner_rule, other_site, ended, off], {"site_id": SITE1}, TODAY
        )
        assert winner is winner_rule
        reasons = {v.rule.id: v.reason for v in verdicts}
        assert reasons[winner_rule.id] == "applies"
        assert reasons[other_site.id] == "scope does not match"
        assert reasons[ended.id].startswith("ended")
        assert reasons[off.id] == "inactive"

    def test_the_snapshot_records_what_was_in_force(self) -> None:
        r = rule(scope={"site_id": SITE1}, value={"max": "16"}, priority=3, end=date(2026, 12, 31))
        snap = r.snapshot()
        assert snap["rule_id"] == str(r.id)
        assert snap["scope"] == {"site_id": SITE1} and snap["value"] == {"max": "16"}
        assert snap["effective_to"] == "2026-12-31" and snap["priority"] == 3


class TestValueValidation:
    def test_every_rule_type_has_a_spec_whose_example_is_valid(self) -> None:
        assert set(RULE_TYPES) == set(RuleType)
        for rule_type, spec in RULE_TYPES.items():
            stored = validate_value(rule_type, spec.example)
            assert stored, rule_type
            assert spec.scope_keys <= SCOPE_KEYS

    def test_decimals_are_stored_as_strings_so_json_never_loses_precision(self) -> None:
        assert validate_value(RuleType.TONNAGE_MAX, {"max": 16.5}) == {"max": "16.5", "unit": "TON"}
        assert validate_value(RuleType.APPROVAL_LIMIT, {"limit": "500000"}) == {
            "limit": "500000",
            "currency": "PKR",
        }

    @pytest.mark.parametrize(
        ("rule_type", "value"),
        [
            (RuleType.TONNAGE_MAX, {"max": 0}),
            (RuleType.TONNAGE_MAX, {"max": "16", "colour": "red"}),
            (RuleType.GEOFENCE_RADIUS, {"radius_m": -5}),
            (RuleType.GEOFENCE_RADIUS, {"radius_m": 999_999}),
            (RuleType.APPROVAL_LIMIT, {"limit": "-1"}),
            (RuleType.CLOCK_SKEW_MAX, {"seconds": 0}),
            (RuleType.DAILY_DELIVERY_CAP, {}),
            (RuleType.DUPLICATE_WINDOW, {"minutes": "soon"}),
        ],
    )
    def test_nonsense_is_refused(self, rule_type: RuleType, value: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            validate_value(rule_type, value)

    def test_a_daily_cap_may_set_either_limit(self) -> None:
        assert validate_value(RuleType.DAILY_DELIVERY_CAP, {"max_deliveries": 5}) == {
            "max_deliveries": 5
        }
        assert validate_value(RuleType.DAILY_DELIVERY_CAP, {"max_quantity": "100"}) == {
            "max_quantity": "100"
        }
