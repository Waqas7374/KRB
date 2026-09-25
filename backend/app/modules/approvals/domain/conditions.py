"""The condition language: structured JSON, evaluated by a small interpreter.

docs/04 §1: never `eval`, never a string DSL that reaches Python. An
expression is either a literal or a one-key object `{operator: [args]}` whose
operator is on a fixed whitelist:

    {"and": [{">=": [{"var": "total_amount"}, 100000]},
             {"in": [{"var": "project.code"}, ["GVH", "RSD"]]}]}

Semantics chosen to fail safe:

* A missing variable is `None`, and any ordering comparison involving `None`
  is False — a rule that cannot be evaluated does not match, so documents fall
  through to later rules and ultimately the mandatory catch-all.
* Numbers compare as `Decimal`, so 0.1 + 0.2 never decides who signs a PO.
* `between` is inclusive at both ends.
* Expressions are validated when a workflow is saved; evaluation assumes a
  valid expression and never has to guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

OPERATORS: frozenset[str] = frozenset(
    {"and", "or", "not", "==", "!=", ">", ">=", "<", "<=", "in", "not_in", "between", "var", "if"}
)

_ARITY: dict[str, tuple[int, int | None]] = {
    "and": (1, None),
    "or": (1, None),
    "not": (1, 1),
    "==": (2, 2),
    "!=": (2, 2),
    ">": (2, 2),
    ">=": (2, 2),
    "<": (2, 2),
    "<=": (2, 2),
    "in": (2, 2),
    "not_in": (2, 2),
    "between": (3, 3),
    "var": (1, 2),
    "if": (3, 3),
}

MAX_DEPTH = 16
MAX_NODES = 256


class ConditionError(ValueError):
    """An expression that is not valid in the condition language."""

    def __init__(self, message: str, path: str = "$") -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


def validate(expr: Any, *, variables: frozenset[str] | None = None) -> None:
    """Raise `ConditionError` unless `expr` is well formed.

    With `variables`, every `var` must name one of them (or a dotted child of
    one ending in `.*` in the catalogue) — a typo in a variable name is caught
    when the workflow is saved, not when a 5 000 000 PO silently falls through
    to the wrong rule.
    """
    counter = [0]
    _validate(expr, "$", 0, counter, variables)


def _validate(
    expr: Any, path: str, depth: int, counter: list[int], variables: frozenset[str] | None
) -> None:
    counter[0] += 1
    if counter[0] > MAX_NODES:
        raise ConditionError(f"expression has more than {MAX_NODES} nodes", path)
    if depth > MAX_DEPTH:
        raise ConditionError(f"expression is nested deeper than {MAX_DEPTH}", path)

    if expr is None or isinstance(expr, bool | int | float | str):
        return
    if isinstance(expr, list):
        for i, item in enumerate(expr):
            _validate(item, f"{path}[{i}]", depth + 1, counter, variables)
        return
    if not isinstance(expr, Mapping):
        raise ConditionError(f"unsupported value of type {type(expr).__name__}", path)
    if len(expr) != 1:
        raise ConditionError("an operator object must have exactly one key", path)

    ((operator, args),) = expr.items()
    if operator not in OPERATORS:
        raise ConditionError(f"unknown operator '{operator}'", path)
    args_list = args if isinstance(args, list) else [args]
    low, high = _ARITY[operator]
    if len(args_list) < low or (high is not None and len(args_list) > high):
        expected = str(low) if low == high else f"{low}..{high if high is not None else 'n'}"
        raise ConditionError(f"'{operator}' takes {expected} argument(s)", path)

    if operator == "var":
        name = args_list[0]
        if not isinstance(name, str) or not name:
            raise ConditionError("'var' needs a variable name", path)
        if variables is not None and not _variable_known(name, variables):
            raise ConditionError(f"unknown variable '{name}'", path)
        return

    for i, arg in enumerate(args_list):
        _validate(arg, f"{path}.{operator}[{i}]", depth + 1, counter, variables)


def _variable_known(name: str, variables: frozenset[str]) -> bool:
    if name in variables:
        return True
    # "project.*" in the catalogue admits "project.code", "project.id", ...
    head = name.split(".", 1)[0]
    return f"{head}.*" in variables


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------


def evaluate(expr: Any, context: Mapping[str, Any]) -> Any:
    """Evaluate a validated expression against a context."""
    if expr is None or isinstance(expr, bool | int | float | str):
        return expr
    if isinstance(expr, list):
        return [evaluate(item, context) for item in expr]

    ((operator, args),) = expr.items()
    args_list = args if isinstance(args, list) else [args]

    if operator == "var":
        return _lookup(context, args_list[0], args_list[1] if len(args_list) > 1 else None)
    if operator == "and":
        return all(_truthy(evaluate(a, context)) for a in args_list)
    if operator == "or":
        return any(_truthy(evaluate(a, context)) for a in args_list)
    if operator == "not":
        return not _truthy(evaluate(args_list[0], context))
    if operator == "if":
        cond, then, otherwise = args_list
        return evaluate(then if _truthy(evaluate(cond, context)) else otherwise, context)

    values = [evaluate(a, context) for a in args_list]
    if operator == "==":
        return _equal(values[0], values[1])
    if operator == "!=":
        return not _equal(values[0], values[1])
    if operator in {">", ">=", "<", "<="}:
        return _compare(operator, values[0], values[1])
    if operator == "in":
        return _contains(values[1], values[0])
    if operator == "not_in":
        return not _contains(values[1], values[0])
    if operator == "between":
        return _compare(">=", values[0], values[1]) and _compare("<=", values[0], values[2])
    raise ConditionError(f"unknown operator '{operator}'")  # unreachable after validate()


def matches(expr: Any, context: Mapping[str, Any]) -> bool:
    return _truthy(evaluate(expr, context))


def _lookup(context: Mapping[str, Any], name: str, default: Any) -> Any:
    current: Any = context
    for part in name.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return default
    return current


def _truthy(value: Any) -> bool:
    return bool(value)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def _equal(left: Any, right: Any) -> bool:
    left_num, right_num = _as_decimal(left), _as_decimal(right)
    # Numeric equality only when both sides are genuinely numeric; "12" == 12
    # is true, but "GVH" is never coerced.
    if (
        left_num is not None
        and right_num is not None
        and not (isinstance(left, str) and isinstance(right, str))
    ):
        return left_num == right_num
    return bool(left == right)


def _compare(operator: str, left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    left_num, right_num = _as_decimal(left), _as_decimal(right)
    if left_num is not None and right_num is not None:
        a: Any = left_num
        b: Any = right_num
    elif isinstance(left, str) and isinstance(right, str):
        a, b = left, right
    else:
        return False
    if operator == ">":
        return bool(a > b)
    if operator == ">=":
        return bool(a >= b)
    if operator == "<":
        return bool(a < b)
    return bool(a <= b)


def _contains(container: Any, item: Any) -> bool:
    if isinstance(container, list):
        return any(_equal(item, candidate) for candidate in container)
    if isinstance(container, str) and isinstance(item, str):
        return item in container
    return False
