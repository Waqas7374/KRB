"""Weighted-average costing (docs/02 §7 valuation). Pure arithmetic.

Stock is valued at the moving weighted average: every receipt blends its cost
into the average, every issue leaves at the current average. FIFO layers can be
added later (a `stock_layers` table) without touching rows written today.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_VALUE = Decimal("0.0001")
_COST = Decimal("0.000001")


def _value(x: Decimal) -> Decimal:
    return x.quantize(_VALUE, rounding=ROUND_HALF_UP)


def _cost(x: Decimal) -> Decimal:
    return x.quantize(_COST, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Position:
    quantity: Decimal
    value: Decimal
    average_cost: Decimal


EMPTY = Position(Decimal(0), Decimal(0), Decimal(0))


@dataclass(frozen=True, slots=True)
class Movement:
    """The outcome of one movement: the value that moved and the new position."""

    unit_cost: Decimal
    value: Decimal
    after: Position


def receive(before: Position, quantity: Decimal, unit_cost: Decimal) -> Movement:
    """A receipt at `unit_cost`: its value joins the pool, the average follows."""
    value = _value(quantity * unit_cost)
    new_quantity = before.quantity + quantity
    new_value = before.value + value
    average = _cost(new_value / new_quantity) if new_quantity > 0 else before.average_cost
    return Movement(unit_cost, value, Position(new_quantity, new_value, average))


def issue(before: Position, quantity: Decimal, unit_cost: Decimal | None = None) -> Movement:
    """An issue: leaves at the current average unless `unit_cost` is given (a
    reversal leaves at the cost the receipt came in at). The last unit out takes
    whatever value remains, so rounding can never strand a paisa in an empty
    store."""
    cost = before.average_cost if unit_cost is None else unit_cost
    if quantity >= before.quantity:
        value = before.value
    else:
        value = min(_value(quantity * cost), before.value)
    new_quantity = before.quantity - quantity
    new_value = before.value - value
    average = _cost(new_value / new_quantity) if new_quantity > 0 else before.average_cost
    return Movement(cost, value, Position(new_quantity, new_value, average))
