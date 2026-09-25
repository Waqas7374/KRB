"""Workflow definitions: parse, validate, select.

A workflow *version* is one immutable document:

    {"rules": [
       {"sequence": 1, "name": "Under 100k",
        "condition": {"<": [{"var": "total_amount"}, 100000]},
        "steps": [{"step_no": 1, "name": "Project manager",
                   "approver_type": "DYNAMIC", "approver_ref": "project_manager",
                   "quorum_type": "ANY", "sla_hours": 24}]},
       {"sequence": 99, "name": "Everything else", "condition": true, "steps": [...]}
    ]}

Stored whole (JSONB) rather than spread over rule and step tables: a version
is never edited, only superseded, and the request snapshot is this same shape,
so "what were the rules when this was approved?" is answered by reading one
value. Everything here is pure; checks that need the database (does the role
exist, can it approve this document type) live in the workflow service.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from app.modules.approvals.domain import conditions
from app.modules.approvals.domain.enums import ApproverType, DynamicApprover, QuorumType

MAX_SLA_HOURS = 24 * 30


class DefinitionError(ValueError):
    """A workflow definition that cannot be saved. Carries every problem found."""

    def __init__(self, errors: list[tuple[str, str]]) -> None:
        super().__init__("; ".join(f"{path}: {message}" for path, message in errors))
        self.errors = errors


@dataclass(frozen=True, slots=True)
class StepDef:
    step_no: int
    name: str
    approver_type: str
    approver_ref: str | None = None
    approver_refs: tuple[str, ...] = ()
    quorum_type: str = QuorumType.ANY.value
    quorum_count: int | None = None
    sla_hours: int = 24
    escalate_to_type: str | None = None
    escalate_to_ref: str | None = None
    allow_self_approve: bool = False
    condition: Any = None


@dataclass(frozen=True, slots=True)
class RuleDef:
    sequence: int
    name: str
    condition: Any
    steps: tuple[StepDef, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class WorkflowDef:
    rules: tuple[RuleDef, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "rules": [
                {
                    **{k: v for k, v in asdict(rule).items() if k != "steps"},
                    "steps": [
                        {**asdict(step), "approver_refs": list(step.approver_refs)}
                        for step in rule.steps
                    ],
                }
                for rule in self.rules
            ]
        }


def is_catch_all(condition: Any) -> bool:
    """Only the literal `true` counts. `{"==": [1, 1]}` is always true too, but
    a reader scanning the workflow should not have to prove it."""
    return condition is True


# -----------------------------------------------------------------------------
# Parsing and validation
# -----------------------------------------------------------------------------


def parse(raw: Mapping[str, Any], *, variables: frozenset[str] | None = None) -> WorkflowDef:
    """Parse and validate. Raises `DefinitionError` listing every problem."""
    errors: list[tuple[str, str]] = []
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise DefinitionError([("rules", "at least one rule is required")])

    rules: list[RuleDef] = []
    seen_sequences: set[int] = set()
    for r_index, rule_raw in enumerate(rules_raw):
        path = f"rules[{r_index}]"
        if not isinstance(rule_raw, Mapping):
            errors.append((path, "must be an object"))
            continue
        sequence = rule_raw.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            errors.append((f"{path}.sequence", "must be a non-negative integer"))
            sequence = -1
        elif sequence in seen_sequences:
            errors.append((f"{path}.sequence", f"sequence {sequence} is used twice"))
        seen_sequences.add(sequence)

        name = str(rule_raw.get("name") or f"Rule {sequence}")
        condition = rule_raw.get("condition", True)
        try:
            conditions.validate(condition, variables=variables)
        except conditions.ConditionError as exc:
            errors.append((f"{path}.condition", str(exc)))

        steps = _parse_steps(rule_raw.get("steps"), f"{path}.steps", errors, variables)
        rules.append(RuleDef(sequence=sequence, name=name, condition=condition, steps=steps))

    ordered = sorted(rules, key=lambda r: r.sequence)
    if ordered and not is_catch_all(ordered[-1].condition):
        errors.append(
            (
                "rules",
                "the last rule (highest sequence) must have condition `true` so that no "
                "document can be submitted into a void",
            )
        )
    for rule in ordered[:-1]:
        if is_catch_all(rule.condition):
            errors.append(
                (
                    f"rules[sequence={rule.sequence}]",
                    "only the last rule may be a catch-all; later rules would never match",
                )
            )

    if errors:
        raise DefinitionError(errors)
    return WorkflowDef(rules=tuple(ordered))


def _parse_steps(
    raw: Any,
    path: str,
    errors: list[tuple[str, str]],
    variables: frozenset[str] | None,
) -> tuple[StepDef, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        errors.append((path, "must be a list"))
        return ()

    steps: list[StepDef] = []
    for s_index, step_raw in enumerate(raw):
        spath = f"{path}[{s_index}]"
        if not isinstance(step_raw, Mapping):
            errors.append((spath, "must be an object"))
            continue

        approver_type = str(step_raw.get("approver_type", ""))
        approver_ref = step_raw.get("approver_ref")
        approver_refs = tuple(str(x) for x in step_raw.get("approver_refs") or ())
        quorum_type = str(step_raw.get("quorum_type", QuorumType.ANY.value))
        quorum_count = step_raw.get("quorum_count")
        sla_hours = step_raw.get("sla_hours", 24)
        escalate_to_type = step_raw.get("escalate_to_type")
        escalate_to_ref = step_raw.get("escalate_to_ref")
        condition = step_raw.get("condition")

        if approver_type not in {t.value for t in ApproverType}:
            errors.append((f"{spath}.approver_type", f"must be one of {_names(ApproverType)}"))
        elif approver_type == ApproverType.GROUP.value:
            if len(approver_refs) < 1:
                errors.append((f"{spath}.approver_refs", "a GROUP step needs at least one user"))
        elif not isinstance(approver_ref, str) or not approver_ref:
            errors.append((f"{spath}.approver_ref", f"required for a {approver_type} step"))
        elif approver_type == ApproverType.DYNAMIC.value and approver_ref not in {
            d.value for d in DynamicApprover
        }:
            errors.append(
                (
                    f"{spath}.approver_ref",
                    f"unsupported dynamic approver '{approver_ref}'; available: "
                    f"{_names(DynamicApprover)}",
                )
            )

        if quorum_type not in {q.value for q in QuorumType}:
            errors.append((f"{spath}.quorum_type", f"must be one of {_names(QuorumType)}"))
        elif quorum_type == QuorumType.N_OF_M.value and (
            not isinstance(quorum_count, int) or quorum_count < 1
        ):
            errors.append((f"{spath}.quorum_count", "N_OF_M needs a quorum_count of at least 1"))

        if (
            not isinstance(sla_hours, int)
            or isinstance(sla_hours, bool)
            or not (1 <= sla_hours <= MAX_SLA_HOURS)
        ):
            errors.append((f"{spath}.sla_hours", f"must be between 1 and {MAX_SLA_HOURS}"))
            sla_hours = 24

        if escalate_to_type is not None:
            if escalate_to_type not in {ApproverType.ROLE.value, ApproverType.USER.value}:
                errors.append((f"{spath}.escalate_to_type", "must be ROLE or USER"))
            if not isinstance(escalate_to_ref, str) or not escalate_to_ref:
                errors.append((f"{spath}.escalate_to_ref", "required when escalating"))

        if condition is not None:
            try:
                conditions.validate(condition, variables=variables)
            except conditions.ConditionError as exc:
                errors.append((f"{spath}.condition", str(exc)))

        steps.append(
            StepDef(
                step_no=int(step_raw.get("step_no") or s_index + 1),
                name=str(step_raw.get("name") or f"Step {s_index + 1}"),
                approver_type=approver_type,
                approver_ref=approver_ref if isinstance(approver_ref, str) else None,
                approver_refs=approver_refs,
                quorum_type=quorum_type,
                quorum_count=quorum_count if isinstance(quorum_count, int) else None,
                sla_hours=sla_hours,
                escalate_to_type=escalate_to_type,
                escalate_to_ref=escalate_to_ref,
                allow_self_approve=bool(step_raw.get("allow_self_approve", False)),
                condition=condition,
            )
        )

    numbers = [s.step_no for s in steps]
    if numbers and sorted(numbers) != list(range(1, len(numbers) + 1)):
        errors.append((path, "steps must be numbered 1, 2, 3 ... without gaps or repeats"))
    return tuple(sorted(steps, key=lambda s: s.step_no))


def _names(enum: Any) -> str:
    return ", ".join(e.value for e in enum)


# -----------------------------------------------------------------------------
# Selection
# -----------------------------------------------------------------------------


def select_rule(workflow: WorkflowDef, context: Mapping[str, Any]) -> RuleDef:
    """First rule, by sequence, whose condition matches. The catch-all
    guarantees one always does."""
    for rule in workflow.rules:
        if conditions.matches(rule.condition, context):
            return rule
    # parse() refuses a definition without a trailing catch-all.
    raise DefinitionError([("rules", "no rule matched and there is no catch-all")])


def applicable_steps(rule: RuleDef, context: Mapping[str, Any]) -> list[StepDef]:
    """Steps whose own condition (if any) holds, renumbered 1..n so the runtime
    never has holes in its step sequence."""
    kept = [
        s for s in rule.steps if s.condition is None or conditions.matches(s.condition, context)
    ]
    return [
        StepDef(**{**_step_fields(step), "step_no": index})
        for index, step in enumerate(kept, start=1)
    ]


def _step_fields(step: StepDef) -> dict[str, Any]:
    return {name: getattr(step, name) for name in StepDef.__slots__}
