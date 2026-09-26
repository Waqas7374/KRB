"""Picking the winning rule (docs/05 §1). Pure: no database, no clock.

    candidates = active rules of the type, effective on the date, whose scope
                 matches the context and whose condition holds
    winner     = most specific scope, then highest priority, then latest
                 effective_from, then newest

Most specific wins, so a rule for "10-wheelers carrying crush at site 2"
beats "10-wheelers", which beats the company default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.core import conditions


@dataclass(frozen=True, slots=True)
class RuleView:
    id: UUID
    rule_type: str
    name: str | None
    scope: Mapping[str, str]
    condition: Any
    value: Mapping[str, Any]
    priority: int
    effective_from: date
    effective_to: date | None
    is_active: bool
    created_at: datetime

    @property
    def specificity(self) -> int:
        return len(self.scope)

    def snapshot(self) -> dict[str, Any]:
        """What a flag stores, so later edits never rewrite why it fired."""
        return {
            "rule_id": str(self.id),
            "rule_type": self.rule_type,
            "name": self.name,
            "scope": dict(self.scope),
            "value": dict(self.value),
            "priority": self.priority,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
        }


@dataclass(frozen=True, slots=True)
class Verdict:
    """Why a rule did or did not apply."""

    rule: RuleView
    applies: bool
    reason: str


def _key(rule: RuleView) -> tuple[int, int, date, datetime]:
    return (rule.specificity, rule.priority, rule.effective_from, rule.created_at)


def scope_matches(scope: Mapping[str, str], context: Mapping[str, Any]) -> bool:
    """Every key the rule sets must equal the context's value for it. A context
    that lacks the key does not match: a site-specific rule must not apply to a
    document that names no site."""
    return all(
        key in context and context[key] is not None and str(context[key]) == value
        for key, value in scope.items()
    )


def judge(rule: RuleView, context: Mapping[str, Any], at: date) -> Verdict:
    if not rule.is_active:
        return Verdict(rule, False, "inactive")
    if rule.effective_from > at:
        return Verdict(rule, False, f"starts {rule.effective_from.isoformat()}")
    if rule.effective_to is not None and rule.effective_to < at:
        return Verdict(rule, False, f"ended {rule.effective_to.isoformat()}")
    if not scope_matches(rule.scope, context):
        return Verdict(rule, False, "scope does not match")
    if rule.condition is not None and not conditions.matches(rule.condition, context):
        return Verdict(rule, False, "condition is not met")
    return Verdict(rule, True, "applies")


def pick(rules: Sequence[RuleView], context: Mapping[str, Any], at: date) -> RuleView | None:
    applying = [v.rule for v in (judge(r, context, at) for r in rules) if v.applies]
    return max(applying, key=_key, default=None)


def explain(
    rules: Sequence[RuleView], context: Mapping[str, Any], at: date
) -> tuple[RuleView | None, list[Verdict]]:
    """The winner plus a verdict for every rule considered, for the admin's
    "which rule applies to this?" panel."""
    verdicts = [judge(r, context, at) for r in rules]
    winner = max((v.rule for v in verdicts if v.applies), key=_key, default=None)
    return winner, verdicts
