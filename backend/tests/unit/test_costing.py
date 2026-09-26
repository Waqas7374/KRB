"""Weighted-average costing (docs/02 §7). Pure arithmetic."""

from __future__ import annotations

from decimal import Decimal

from app.modules.inventory.domain import costing
from app.modules.inventory.domain.enums import TxnType

D = Decimal


class TestReceive:
    def test_the_first_receipt_sets_the_average(self) -> None:
        move = costing.receive(costing.EMPTY, D("12.5"), D("100"))
        assert move.value == D("1250.0000")
        assert (move.after.quantity, move.after.value, move.after.average_cost) == (
            D("12.5"),
            D("1250.0000"),
            D("100.000000"),
        )

    def test_a_second_receipt_blends_into_the_average(self) -> None:
        first = costing.receive(costing.EMPTY, D("10"), D("100")).after
        second = costing.receive(first, D("30"), D("200")).after
        # (10 x 100 + 30 x 200) / 40 = 175
        assert second.quantity == D(40)
        assert second.value == D("7000.0000")
        assert second.average_cost == D("175.000000")

    def test_a_receipt_is_valued_to_four_places_half_up(self) -> None:
        move = costing.receive(costing.EMPTY, D("3"), D("0.333333"))
        assert move.value == D("1.0000")  # 0.999999 rounds up

    def test_a_free_receipt_lowers_the_average(self) -> None:
        first = costing.receive(costing.EMPTY, D("10"), D("100")).after
        gift = costing.receive(first, D("10"), D("0")).after
        assert gift.average_cost == D("50.000000")


class TestIssue:
    def test_an_issue_leaves_at_the_current_average(self) -> None:
        stock = costing.receive(costing.EMPTY, D("40"), D("175")).after
        move = costing.issue(stock, D("10"))
        assert move.unit_cost == D("175.000000")
        assert move.value == D("1750.0000")
        assert move.after.quantity == D(30)
        assert move.after.value == D("5250.0000")
        # An issue never changes the average.
        assert move.after.average_cost == D("175.000000")

    def test_a_reversal_leaves_at_the_cost_it_came_in_at_not_the_average(self) -> None:
        stock = costing.receive(costing.EMPTY, D("10"), D("100")).after
        stock = costing.receive(stock, D("10"), D("300")).after  # average 200
        move = costing.issue(stock, D("10"), unit_cost=D("300"))
        assert move.value == D("3000.0000")
        assert move.after.value == D("1000.0000")

    def test_the_last_unit_out_takes_whatever_value_remains(self) -> None:
        # 3 units at 33.333333 = 99.999999 -> 100.0000; selling 3 x 3 pieces
        # one at a time must not strand a paisa in an empty store.
        stock = costing.receive(costing.EMPTY, D("3"), D("33.333333")).after
        for _ in range(2):
            stock = costing.issue(stock, D("1")).after
        last = costing.issue(stock, D("1"))
        assert last.after.quantity == 0 and last.after.value == 0

    def test_an_empty_store_remembers_its_last_average(self) -> None:
        stock = costing.receive(costing.EMPTY, D("5"), D("80")).after
        empty = costing.issue(stock, D("5")).after
        assert empty.quantity == 0 and empty.value == 0
        assert empty.average_cost == D("80.000000")

    def test_it_can_never_take_out_more_value_than_there_is(self) -> None:
        stock = costing.Position(D("10"), D("500"), D("50"))
        move = costing.issue(stock, D("4"), unit_cost=D("999"))  # a wildly wrong cost
        assert move.value == D("500")
        assert move.after.value == D("0")


class TestTxnType:
    def test_direction(self) -> None:
        assert TxnType.GRN_IN.is_inbound and TxnType.REVERSAL_IN.is_inbound
        assert not TxnType.ISSUE_OUT.is_inbound and not TxnType.REVERSAL_OUT.is_inbound
