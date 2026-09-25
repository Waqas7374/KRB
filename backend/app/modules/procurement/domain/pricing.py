"""Line and document pricing shared by quotations and purchase orders.

Pure functions over Decimal. One place decides how a discount and a tax
percentage turn into money, so a quotation and the order raised from it can
never disagree by a paisa because two modules rounded differently.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.0001")
_HUNDRED = Decimal(100)


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class LineAmounts:
    gross: Decimal  # quantity x rate
    discount: Decimal
    net: Decimal  # gross - discount, before tax
    tax: Decimal
    total: Decimal  # net + tax


def price_line(
    quantity: Decimal, rate: Decimal, discount_pct: Decimal, tax_pct: Decimal
) -> LineAmounts:
    gross = _q(quantity * rate)
    discount = _q(gross * discount_pct / _HUNDRED)
    net = gross - discount
    tax = _q(net * tax_pct / _HUNDRED)
    return LineAmounts(gross=gross, discount=discount, net=net, tax=tax, total=net + tax)


@dataclass(frozen=True, slots=True)
class DocumentTotals:
    subtotal: Decimal  # sum of gross
    discount_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal


def total_lines(lines: Iterable[LineAmounts]) -> DocumentTotals:
    gross = discount = tax = Decimal(0)
    for line in lines:
        gross += line.gross
        discount += line.discount
        tax += line.tax
    return DocumentTotals(
        subtotal=gross,
        discount_amount=discount,
        tax_amount=tax,
        total_amount=gross - discount + tax,
    )


def net_unit_rate(rate: Decimal, discount_pct: Decimal) -> Decimal:
    """The rate after discount and before tax: what vendors are compared on."""
    return _q(rate * (_HUNDRED - discount_pct) / _HUNDRED)
