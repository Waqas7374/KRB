"""Vendor-rate resolution (docs/05 §4). Pure: no database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from app.modules.rates.domain import resolution
from app.modules.rates.domain.resolution import RateView

VENDOR, MATERIAL, UNIT_TON, UNIT_CFT = uuid4(), uuid4(), uuid4(), uuid4()
PROJECT, OTHER_PROJECT = uuid4(), uuid4()
SITE_1, SITE_2 = uuid4(), uuid4()
TODAY = date(2026, 9, 25)


def rate(
    value: str,
    *,
    project: UUID | None = None,
    site: UUID | None = None,
    start: date = date(2026, 1, 1),
    end: date | None = None,
    unit: UUID = UNIT_TON,
) -> RateView:
    return RateView(
        id=uuid4(),
        vendor_id=VENDOR,
        material_id=MATERIAL,
        unit_id=unit,
        rate=Decimal(value),
        currency_code="PKR",
        project_id=project,
        site_id=site,
        effective_from=start,
        effective_to=end,
        source="MANUAL",
    )


def picked(rates: list[RateView], **kw: object) -> str | None:
    found = resolution.pick(rates, at=TODAY, **kw)  # type: ignore[arg-type]
    return None if found is None else str(found.rate)


class TestScopeSpecificity:
    def test_a_site_rate_beats_a_project_rate_beats_a_company_rate(self) -> None:
        rates = [
            rate("100"),
            rate("110", project=PROJECT),
            rate("120", project=PROJECT, site=SITE_1),
        ]
        assert picked(rates, project_id=PROJECT, site_id=SITE_1) == "120"
        assert picked(rates, project_id=PROJECT, site_id=SITE_2) == "110"
        assert picked(rates, project_id=OTHER_PROJECT, site_id=None) == "100"

    def test_a_site_rate_never_applies_to_another_site_or_to_no_site(self) -> None:
        rates = [rate("120", project=PROJECT, site=SITE_1)]
        assert picked(rates, project_id=PROJECT, site_id=SITE_2) is None
        assert picked(rates, project_id=PROJECT, site_id=None) is None

    def test_a_project_rate_never_applies_to_another_project(self) -> None:
        assert (
            picked([rate("110", project=PROJECT)], project_id=OTHER_PROJECT, site_id=None) is None
        )

    def test_with_no_place_only_the_company_rate_applies(self) -> None:
        rates = [rate("100"), rate("110", project=PROJECT)]
        assert picked(rates, project_id=None, site_id=None) == "100"


class TestPeriods:
    def test_a_rate_applies_from_its_first_to_its_last_day_inclusive(self) -> None:
        r = rate("100", start=date(2026, 9, 1), end=date(2026, 9, 30))
        for day, expected in (
            (date(2026, 8, 31), None),
            (date(2026, 9, 1), "100"),
            (date(2026, 9, 30), "100"),
            (date(2026, 10, 1), None),
        ):
            found = resolution.pick([r], at=day, project_id=None, site_id=None)
            assert (None if found is None else str(found.rate)) == expected, day

    def test_history_resolves_to_the_rate_of_the_day(self) -> None:
        old = rate("48", start=date(2026, 1, 1), end=date(2026, 8, 11))
        new = rate("52", start=date(2026, 8, 12))
        rates = [old, new]
        assert resolution.pick(rates, at=date(2026, 8, 11), project_id=None, site_id=None) is old
        assert resolution.pick(rates, at=date(2026, 8, 12), project_id=None, site_id=None) is new

    def test_at_equal_specificity_the_later_start_wins(self) -> None:
        a, b = rate("100", start=date(2026, 1, 1)), rate("105", start=date(2026, 6, 1))
        assert resolution.pick([a, b], at=TODAY, project_id=None, site_id=None) is b


class TestUnits:
    def test_a_unit_filter_selects_that_pricing_unit(self) -> None:
        rates = [rate("100", unit=UNIT_TON), rate("3", unit=UNIT_CFT)]
        assert picked(rates, project_id=None, site_id=None, unit_id=UNIT_CFT) == "3"
        assert picked(rates, project_id=None, site_id=None, unit_id=uuid4()) is None

    def test_without_a_unit_the_vendors_own_pricing_unit_is_returned(self) -> None:
        found = resolution.pick([rate("3", unit=UNIT_CFT)], at=TODAY, project_id=None, site_id=None)
        assert found is not None and found.unit_id == UNIT_CFT


class TestChangePct:
    def test_signed_percentage_to_two_places(self) -> None:
        assert resolution.change_pct(Decimal("48"), Decimal("52")) == Decimal("8.33")
        assert resolution.change_pct(Decimal("50"), Decimal("45")) == Decimal("-10.00")

    def test_no_comparison_without_a_current_rate(self) -> None:
        assert resolution.change_pct(None, Decimal("52")) is None
        assert resolution.change_pct(Decimal("0"), Decimal("52")) is None
