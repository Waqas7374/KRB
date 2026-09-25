"""Line and document pricing shared by quotations and purchase orders."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.procurement.domain import pricing

D = Decimal


class TestPriceLine:
    def test_discount_comes_off_before_tax(self) -> None:
        line = pricing.price_line(D(100), D(80), D(10), D(17))
        assert (line.gross, line.discount, line.net, line.tax, line.total) == (
            D(8000),
            D(800),
            D(7200),
            D(1224),
            D(8424),
        )

    def test_no_discount_and_no_tax_is_plain_multiplication(self) -> None:
        line = pricing.price_line(D("2.5"), D("40"), D(0), D(0))
        assert line.total == D(100)

    def test_rounds_half_up_to_four_places(self) -> None:
        # 3 x 0.33335 = 1.00005 -> 1.0001 (half up, not banker's rounding)
        assert pricing.price_line(D(3), D("0.33335"), D(0), D(0)).gross == D("1.0001")

    def test_a_fully_discounted_line_costs_nothing(self) -> None:
        assert pricing.price_line(D(5), D(10), D(100), D(17)).total == D(0)


class TestTotals:
    def test_totals_add_up_line_by_line(self) -> None:
        totals = pricing.total_lines(
            [
                pricing.price_line(D(100), D(80), D(10), D(17)),
                pricing.price_line(D(50), D(60), D(0), D(0)),
            ]
        )
        assert totals.subtotal == D(11000)
        assert totals.discount_amount == D(800)
        assert totals.tax_amount == D(1224)
        assert totals.total_amount == D(11424)

    def test_total_is_subtotal_less_discount_plus_tax(self) -> None:
        totals = pricing.total_lines([pricing.price_line(D(7), D("13.37"), D("3.5"), D("16"))])
        assert totals.total_amount == totals.subtotal - totals.discount_amount + totals.tax_amount

    def test_no_lines_total_zero(self) -> None:
        assert pricing.total_lines([]).total_amount == 0


@pytest.mark.parametrize(
    ("rate", "discount", "expected"),
    [(D(100), D(0), D(100)), (D(100), D(10), D(90)), (D("80"), D("12.5"), D(70))],
)
def test_net_unit_rate_is_what_vendors_are_compared_on(
    rate: Decimal, discount: Decimal, expected: Decimal
) -> None:
    assert pricing.net_unit_rate(rate, discount) == expected
