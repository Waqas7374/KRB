"""The unit-conversion engine against the seeded configuration.

§20 requires that no conversion factor is hard-coded and that conversions are
configurable, effective-dated and scoped. These tests are what make that claim
checkable rather than aspirational.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConversionNotConfiguredError
from app.modules.masterdata.domain.enums import ConversionScope
from app.modules.masterdata.models import Material, Unit, UnitConversion
from app.modules.masterdata.services.conversion import UnitConverter
from app.modules.org.models import Company

pytestmark = pytest.mark.integration


@pytest.fixture
async def fixtures(db: AsyncSession) -> dict[str, object]:
    company = (await db.execute(select(Company))).scalars().first()
    assert company is not None
    units = {u.code: u.id for u in (await db.execute(select(Unit))).scalars()}
    materials = {m.sku: m.id for m in (await db.execute(select(Material))).scalars()}
    return {
        "company": company,
        "units": units,
        "materials": materials,
        "converter": UnitConverter(db, company.id),
    }


def _u(fixtures: dict[str, object], code: str) -> UUID:
    return fixtures["units"][code]  # type: ignore[index,return-value]


def _m(fixtures: dict[str, object], sku: str) -> UUID:
    return fixtures["materials"][sku]  # type: ignore[index,return-value]


def _c(fixtures: dict[str, object]) -> UnitConverter:
    return fixtures["converter"]  # type: ignore[return-value]


class TestSameUnit:
    async def test_identity_needs_no_configuration(self, fixtures: dict[str, object]) -> None:
        resolved = await _c(fixtures).factor(_u(fixtures, "TON"), _u(fixtures, "TON"))
        assert resolved.factor == Decimal(1)
        assert resolved.is_identity


class TestGlobalConversions:
    async def test_mass_within_dimension(self, fixtures: dict[str, object]) -> None:
        resolved = await _c(fixtures).factor(_u(fixtures, "TON"), _u(fixtures, "KG"))
        assert resolved.factor == Decimal("1000")
        assert resolved.scope is ConversionScope.GLOBAL

    async def test_the_inverse_direction_uses_the_same_row(
        self, fixtures: dict[str, object]
    ) -> None:
        """One row serves both directions, so the two cannot drift apart."""
        forward = await _c(fixtures).factor(_u(fixtures, "TON"), _u(fixtures, "KG"))
        reverse = await _c(fixtures).factor(_u(fixtures, "KG"), _u(fixtures, "TON"))

        assert reverse.inverted is True
        assert reverse.conversion_id == forward.conversion_id
        assert forward.factor * reverse.factor == Decimal(1)

    async def test_brass_is_a_hundred_cubic_feet(self, fixtures: dict[str, object]) -> None:
        resolved = await _c(fixtures).factor(_u(fixtures, "BRASS"), _u(fixtures, "CFT"))
        assert resolved.factor == Decimal("100")


class TestMaterialScopedConversions:
    async def test_tonnes_to_cubic_feet_depends_on_the_material(
        self, fixtures: dict[str, object]
    ) -> None:
        """Crush and sand have different densities, so the factors differ.

        This is why a tonne-to-cft row is material-scoped rather than global:
        a single global factor would misprice every load of one of them.
        """
        converter = _c(fixtures)
        crush = await converter.factor(
            _u(fixtures, "TON"), _u(fixtures, "CFT"), material_id=_m(fixtures, "AGG-CRUSH-20")
        )
        sand = await converter.factor(
            _u(fixtures, "TON"), _u(fixtures, "CFT"), material_id=_m(fixtures, "SAND-RIVER")
        )

        assert crush.scope is ConversionScope.MATERIAL
        assert sand.scope is ConversionScope.MATERIAL
        assert crush.factor != sand.factor

    async def test_the_worked_example_from_the_brief(self, fixtures: dict[str, object]) -> None:
        """12.5 tonnes of crush at Rs 52/cft.

        The brief's §20 example. Every number here comes from configuration:
        the factor from `unit_conversions`, the rate from the material master.
        """
        result = await _c(fixtures).convert(
            Decimal("12.5"),
            _u(fixtures, "TON"),
            _u(fixtures, "CFT"),
            material_id=_m(fixtures, "AGG-CRUSH-20"),
        )

        assert result.converted_quantity == Decimal("281.25")
        amount = (result.converted_quantity * Decimal("52")).quantize(Decimal("0.01"))
        assert amount == Decimal("14625.00")
        # The factor is returned so the caller can snapshot it onto the line.
        assert result.conversion_id is not None
        assert result.factor == Decimal("22.500000000000")

    async def test_a_material_scoped_row_beats_a_global_one(
        self, db: AsyncSession, fixtures: dict[str, object]
    ) -> None:
        """Most specific wins."""
        company = fixtures["company"]
        # A deliberately wrong global factor for the same pair.
        db.add(
            UnitConversion(
                company_id=company.id,  # type: ignore[attr-defined]
                from_unit_id=_u(fixtures, "TON"),
                to_unit_id=_u(fixtures, "CFT"),
                factor=Decimal("1.000000000000"),
                scope_type=ConversionScope.GLOBAL.value,
                effective_from=date(2025, 7, 1),
                basis_note="Deliberately wrong, to prove specificity ordering",
            )
        )
        await db.flush()

        resolved = await UnitConverter(db, company.id).factor(  # type: ignore[attr-defined]
            _u(fixtures, "TON"), _u(fixtures, "CFT"), material_id=_m(fixtures, "AGG-CRUSH-20")
        )
        assert resolved.scope is ConversionScope.MATERIAL
        assert resolved.factor == Decimal("22.500000000000")


class TestEffectiveDating:
    async def test_a_future_factor_does_not_apply_to_a_past_date(
        self, db: AsyncSession, fixtures: dict[str, object]
    ) -> None:
        """A factor corrected in September must not restate an August delivery."""
        company = fixtures["company"]
        material_id = _m(fixtures, "AGG-CRUSH-12")

        current = (
            await db.execute(
                select(UnitConversion).where(
                    UnitConversion.material_id == material_id,
                    UnitConversion.scope_type == ConversionScope.MATERIAL.value,
                )
            )
        ).scalar_one()
        original_factor = current.factor

        # Supersede from 1 September.
        current.effective_to = date(2026, 8, 31)
        db.add(
            UnitConversion(
                company_id=company.id,  # type: ignore[attr-defined]
                from_unit_id=current.from_unit_id,
                to_unit_id=current.to_unit_id,
                factor=Decimal("21.000000000000"),
                scope_type=ConversionScope.MATERIAL.value,
                material_id=material_id,
                effective_from=date(2026, 9, 1),
                basis_note="Re-measured after the monsoon",
                supersedes_id=current.id,
            )
        )
        await db.flush()

        converter = UnitConverter(db, company.id)  # type: ignore[attr-defined]
        august = await converter.factor(
            current.from_unit_id,
            current.to_unit_id,
            material_id=material_id,
            at=date(2026, 8, 15),
        )
        september = await converter.factor(
            current.from_unit_id,
            current.to_unit_id,
            material_id=material_id,
            at=date(2026, 9, 15),
        )

        assert august.factor == original_factor
        assert september.factor == Decimal("21.000000000000")


class TestChainedConversions:
    async def test_two_hops_resolve_through_a_shared_unit(
        self, fixtures: dict[str, object]
    ) -> None:
        """BAG -> KG (material) then KG -> TON (global)."""
        resolved = await _c(fixtures).factor(
            _u(fixtures, "BAG"), _u(fixtures, "TON"), material_id=_m(fixtures, "CEM-OPC-50")
        )
        assert resolved.via_unit_code == "KG"
        assert resolved.factor == Decimal("0.050000000000000")
        # A chained factor is not one stored row, so nothing is cited as its
        # source; the caller stores the factor itself.
        assert resolved.conversion_id is None


class TestMissingConfiguration:
    async def test_an_unconfigured_pair_is_refused_not_guessed(
        self, fixtures: dict[str, object]
    ) -> None:
        """A missing factor is a configuration gap, not a number to invent."""
        with pytest.raises(ConversionNotConfiguredError) as exc_info:
            await _c(fixtures).factor(_u(fixtures, "TON"), _u(fixtures, "RFT"))

        assert exc_info.value.status_code == 422
        assert "administrator must configure" in exc_info.value.detail

    async def test_a_material_without_a_density_row_is_refused(
        self, fixtures: dict[str, object]
    ) -> None:
        """Steel has no tonne-to-cft row, and must not borrow one from crush."""
        with pytest.raises(ConversionNotConfiguredError):
            await _c(fixtures).factor(
                _u(fixtures, "TON"),
                _u(fixtures, "CFT"),
                material_id=_m(fixtures, "STEEL-REBAR-12"),
            )


class TestRounding:
    async def test_the_result_respects_the_target_unit_precision(
        self, fixtures: dict[str, object]
    ) -> None:
        """CFT is seeded at 2 decimal places."""
        result = await _c(fixtures).convert(
            Decimal("1.333"),
            _u(fixtures, "TON"),
            _u(fixtures, "CFT"),
            material_id=_m(fixtures, "AGG-CRUSH-20"),
        )
        assert result.converted_quantity.as_tuple().exponent == -2

    async def test_bags_round_to_whole_numbers(self, fixtures: dict[str, object]) -> None:
        """BAG has precision 0: half a bag is not a thing on a challan."""
        result = await _c(fixtures).convert(
            Decimal("0.126"),
            _u(fixtures, "TON"),
            _u(fixtures, "BAG"),
            material_id=_m(fixtures, "CEM-OPC-50"),
        )
        assert result.converted_quantity == result.converted_quantity.to_integral_value()
