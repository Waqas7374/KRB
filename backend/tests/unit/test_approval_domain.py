"""The approval engine's pure core: condition language and rule selection.

docs/04 §6: the evaluator against a table of expressions and contexts,
including the operator whitelist rejecting anything unknown; rule selection
is first-match-wins with a mandatory catch-all.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.modules.approvals.domain import conditions
from app.modules.approvals.domain.definition import (
    DefinitionError,
    applicable_steps,
    parse,
    select_rule,
)

CONTEXT: dict[str, Any] = {
    "total_amount": Decimal("450000.00"),
    "item_count": 3,
    "is_capex": False,
    "project": {"code": "GVH", "id": "p-1"},
    "requester": {"role": "SITE_MANAGER"},
}


class TestEvaluator:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            (True, True),
            (False, False),
            ({">=": [{"var": "total_amount"}, 100000]}, True),
            ({"<": [{"var": "total_amount"}, 100000]}, False),
            # Decimal vs float/int compares exactly.
            ({"==": [{"var": "total_amount"}, 450000]}, True),
            ({"==": [{"var": "total_amount"}, 450000.0]}, True),
            ({"between": [{"var": "total_amount"}, 100000, 1000000]}, True),
            ({"between": [{"var": "total_amount"}, 450000, 450000]}, True),  # inclusive
            ({"in": [{"var": "project.code"}, ["GVH", "RSD"]]}, True),
            ({"not_in": [{"var": "project.code"}, ["GVH", "RSD"]]}, False),
            (
                {"and": [{">": [{"var": "item_count"}, 1]}, {"==": [{"var": "is_capex"}, False]}]},
                True,
            ),
            ({"or": [{"==": [{"var": "project.code"}, "RSD"]}, {"var": "is_capex"}]}, False),
            ({"not": [{"var": "is_capex"}]}, True),
            ({"if": [{"var": "is_capex"}, "capex", "opex"]}, "opex"),
            ({"var": ["missing.value", 7]}, 7),
        ],
    )
    def test_table(self, expr: Any, expected: Any) -> None:
        conditions.validate(expr)
        assert conditions.evaluate(expr, CONTEXT) == expected

    def test_missing_variables_never_match_an_ordering_comparison(self) -> None:
        """Fail safe: an unevaluable rule falls through to the catch-all."""
        for op in (">", ">=", "<", "<="):
            assert conditions.matches({op: [{"var": "nope"}, 0]}, CONTEXT) is False

    def test_strings_are_not_coerced_into_numbers_for_equality(self) -> None:
        assert conditions.matches({"==": ["007", "7"]}, CONTEXT) is False
        assert conditions.matches({"==": [{"var": "item_count"}, "3"]}, CONTEXT) is True


class TestValidation:
    @pytest.mark.parametrize(
        "expr",
        [
            {"__import__": ["os"]},
            {"eval": ["1+1"]},
            {"lambda": []},
            {">": [1]},  # wrong arity
            {"and": [], "or": []},  # two keys
            {"var": [""]},
            {"between": [1, 2]},
            object(),
        ],
    )
    def test_rejects_anything_off_the_whitelist(self, expr: Any) -> None:
        with pytest.raises(conditions.ConditionError):
            conditions.validate(expr)

    def test_unknown_variables_are_refused_when_a_catalogue_is_given(self) -> None:
        catalogue = frozenset({"total_amount", "project.*"})
        conditions.validate({"var": "project.code"}, variables=catalogue)
        with pytest.raises(conditions.ConditionError, match="unknown variable 'totl_amount'"):
            conditions.validate({">": [{"var": "totl_amount"}, 1]}, variables=catalogue)

    def test_depth_is_bounded(self) -> None:
        expr: Any = True
        for _ in range(conditions.MAX_DEPTH + 2):
            expr = {"not": [expr]}
        with pytest.raises(conditions.ConditionError, match="nested deeper"):
            conditions.validate(expr)


def _step(no: int, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "step_no": no,
        "name": name,
        "approver_type": "ROLE",
        "approver_ref": name.upper(),
        "sla_hours": 24,
        **extra,
    }


# The §25 three-tier purchase-request workflow.
THREE_TIER: dict[str, Any] = {
    "rules": [
        {
            "sequence": 1,
            "name": "Under 100k",
            "condition": {"<": [{"var": "total_amount"}, 100000]},
            "steps": [
                {
                    "step_no": 1,
                    "name": "Project manager",
                    "approver_type": "DYNAMIC",
                    "approver_ref": "project_manager",
                }
            ],
        },
        {
            "sequence": 2,
            "name": "100k to 1M",
            "condition": {"<": [{"var": "total_amount"}, 1000000]},
            "steps": [
                {
                    "step_no": 1,
                    "name": "Project manager",
                    "approver_type": "DYNAMIC",
                    "approver_ref": "project_manager",
                },
                _step(2, "procurement_manager"),
            ],
        },
        {
            "sequence": 3,
            "name": "1M and above",
            "condition": True,
            "steps": [
                {
                    "step_no": 1,
                    "name": "Project manager",
                    "approver_type": "DYNAMIC",
                    "approver_ref": "project_manager",
                },
                _step(2, "procurement_manager"),
                _step(3, "finance_manager", sla_hours=48),
                _step(4, "executive", sla_hours=72),
            ],
        },
    ]
}


class TestRuleSelection:
    @pytest.mark.parametrize(
        ("amount", "rule", "chain"),
        [
            ("50000", "Under 100k", ["Project manager"]),
            ("500000", "100k to 1M", ["Project manager", "procurement_manager"]),
            (
                "5000000",
                "1M and above",
                ["Project manager", "procurement_manager", "finance_manager", "executive"],
            ),
            # Boundaries: 100 000 is not "under 100k"; 1 000 000 is not "under 1M".
            ("100000", "100k to 1M", ["Project manager", "procurement_manager"]),
            (
                "1000000",
                "1M and above",
                ["Project manager", "procurement_manager", "finance_manager", "executive"],
            ),
        ],
    )
    def test_the_section_25_tiers(self, amount: str, rule: str, chain: list[str]) -> None:
        workflow = parse(THREE_TIER)
        selected = select_rule(workflow, {"total_amount": Decimal(amount)})
        assert selected.name == rule
        assert [s.name for s in applicable_steps(selected, {})] == chain

    def test_rules_are_matched_in_sequence_order_regardless_of_list_order(self) -> None:
        shuffled = {"rules": list(reversed(THREE_TIER["rules"]))}
        rule = select_rule(parse(shuffled), {"total_amount": Decimal("10")})
        assert rule.name == "Under 100k"

    def test_step_conditions_skip_steps_and_renumber(self) -> None:
        definition = {
            "rules": [
                {
                    "sequence": 1,
                    "condition": True,
                    "steps": [
                        _step(1, "pm"),
                        _step(2, "finance", condition={"var": "is_capex"}),
                        _step(3, "executive"),
                    ],
                }
            ]
        }
        rule = select_rule(parse(definition), {})
        steps = applicable_steps(rule, {"is_capex": False})
        assert [(s.step_no, s.name) for s in steps] == [(1, "pm"), (2, "executive")]

    def test_a_zero_step_rule_is_allowed_as_explicit_auto_approval(self) -> None:
        rule = select_rule(parse({"rules": [{"sequence": 1, "condition": True, "steps": []}]}), {})
        assert applicable_steps(rule, {}) == []


class TestDefinitionValidation:
    def _errors(self, definition: dict[str, Any]) -> str:
        with pytest.raises(DefinitionError) as info:
            parse(definition)
        return str(info.value)

    def test_the_last_rule_must_be_a_literal_catch_all(self) -> None:
        errors = self._errors(
            {"rules": [{"sequence": 1, "condition": {"==": [1, 1]}, "steps": [_step(1, "pm")]}]}
        )
        assert "must have condition `true`" in errors

    def test_an_early_catch_all_is_refused(self) -> None:
        errors = self._errors(
            {
                "rules": [
                    {"sequence": 1, "condition": True, "steps": [_step(1, "pm")]},
                    {"sequence": 2, "condition": True, "steps": [_step(1, "pm")]},
                ]
            }
        )
        assert "only the last rule may be a catch-all" in errors

    def test_step_problems_are_all_reported_together(self) -> None:
        errors = self._errors(
            {
                "rules": [
                    {
                        "sequence": 1,
                        "condition": True,
                        "steps": [
                            {"step_no": 1, "approver_type": "WIZARD"},
                            {
                                "step_no": 3,
                                "approver_type": "DYNAMIC",
                                "approver_ref": "department_head",
                            },
                            {
                                "step_no": 4,
                                "approver_type": "ROLE",
                                "approver_ref": "X",
                                "quorum_type": "N_OF_M",
                            },
                        ],
                    }
                ]
            }
        )
        assert "approver_type" in errors
        assert "unsupported dynamic approver 'department_head'" in errors
        assert "quorum_count" in errors
        assert "numbered 1, 2, 3" in errors

    def test_round_trips_through_json(self) -> None:
        workflow = parse(THREE_TIER)
        assert parse(workflow.to_json()) == workflow
