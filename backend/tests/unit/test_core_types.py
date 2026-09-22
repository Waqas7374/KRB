"""Tests for the primitives everything else is built on."""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.types import money, quantity, to_minor_units, utcnow, uuid7


class TestUuid7:
    def test_returns_a_uuid_of_version_7(self) -> None:
        value = uuid7()
        assert isinstance(value, UUID)
        assert value.version == 7

    def test_values_are_unique(self) -> None:
        values = {uuid7() for _ in range(1000)}
        assert len(values) == 1000

    def test_values_are_time_ordered(self) -> None:
        """Index locality depends on this; UUIDv4 would fail it."""
        generated = [uuid7() for _ in range(200)]
        assert generated == sorted(generated, key=lambda u: u.hex)


class TestMoney:
    def test_quantises_to_four_places(self) -> None:
        assert money(Decimal("12.5")) == Decimal("12.5000")

    def test_rounds_half_up(self) -> None:
        # Banker's rounding would give 12.3456 here; finance expects half-up.
        assert money(Decimal("12.34565")) == Decimal("12.3457")
        assert money(Decimal("12.34555")) == Decimal("12.3456")

    def test_accepts_string_input_without_float_error(self) -> None:
        assert money("0.1") + money("0.2") == money("0.3")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("0", "0.0000"), ("-5.55555", "-5.5556"), ("1000000.00004", "1000000.0000")],
    )
    def test_edge_values(self, value: str, expected: str) -> None:
        assert money(value) == Decimal(expected)


class TestQuantity:
    def test_tonnage_precision_is_preserved(self) -> None:
        assert quantity("12.5") == Decimal("12.5000")
        assert quantity("0.0001") == Decimal("0.0001")


class TestMinorUnits:
    def test_pkr_has_two_decimal_places(self) -> None:
        assert to_minor_units(Decimal("23400.4567")) == Decimal("23400.46")

    def test_zero_decimal_currency(self) -> None:
        assert to_minor_units(Decimal("1234.56"), exponent=0) == Decimal("1235")


class TestUtcNow:
    def test_is_timezone_aware_utc(self) -> None:
        now = utcnow()
        assert now.tzinfo is not None
        assert now.utcoffset() == UTC.utcoffset(None)
