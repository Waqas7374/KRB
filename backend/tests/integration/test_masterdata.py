"""Master data end to end: units, conversions, weighbridge calibration,
materials, truck types, warehouses.

The calibration tests are the important ones here. §20 forbids a hard-coded
conversion factor; it says nothing about where a *correct* one comes from.
This is that answer — an admin logs real weighbridge readings and confirms a
factor, rather than typing a number from memory. These tests exist because a
real bug was found while exercising this exact flow by hand: `FOR UPDATE`
cannot be applied across an outer join, and several models eager-load a
relationship that way by default.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.masterdata.models import Material, Unit, UnitConversion

pytestmark = pytest.mark.integration

PROCUREMENT = "procurement@krb.example"
ADMIN = "admin@krb.example"
SITE_STAFF = "staff.gvh1@krb.example"
AUDITOR = "auditor@krb.example"


@pytest.fixture
async def crush_and_units(db: AsyncSession) -> dict[str, Any]:
    material = (
        await db.execute(select(Material).where(Material.sku == "AGG-CRUSH-20"))
    ).scalar_one()
    ton = (await db.execute(select(Unit).where(Unit.code == "TON"))).scalar_one()
    cft = (await db.execute(select(Unit).where(Unit.code == "CFT"))).scalar_one()
    return {"material_id": str(material.id), "ton_id": str(ton.id), "cft_id": str(cft.id)}


class TestUnits:
    async def test_lists_the_seeded_units(self, api: AsyncClient, login: Any) -> None:
        headers = await login(PROCUREMENT)
        body = (await api.get("/units", headers=headers)).json()
        assert body["page"]["total"] >= 12
        codes = {item["code"] for item in body["items"]}
        assert {"TON", "KG", "BAG", "CFT"} <= codes

    async def test_creating_a_unit_requires_units_manage(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(SITE_STAFF)
        response = await api.post(
            "/units",
            headers=headers,
            json={"code": "GALLON", "name": "Gallon", "dimension": "VOLUME"},
        )
        assert response.status_code == 403

    async def test_procurement_manager_can_create_a_unit(
        self, api: AsyncClient, login: Any
    ) -> None:
        """Procurement now owns units.manage — see docs/access/roles.py."""
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/units",
            headers=headers,
            json={"code": "GALLON", "name": "Gallon", "symbol": "gal", "dimension": "VOLUME"},
        )
        assert response.status_code == 201
        assert response.json()["code"] == "GALLON"


class TestConversionResolution:
    async def test_resolves_the_seeded_factor(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/unit-conversions/resolve",
            headers=headers,
            json={
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "material_id": crush_and_units["material_id"],
                "quantity": "12.5",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["factor"] == "22.500000000000"
        assert body["converted_quantity"] == "281.25"

    async def test_an_unconfigured_pair_is_refused_not_guessed(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        rft = (await api.get("/units", headers=headers, params={"q": "Running"})).json()["items"][
            0
        ]["id"]

        response = await api.post(
            "/unit-conversions/resolve",
            headers=headers,
            json={
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": rft,
                "material_id": crush_and_units["material_id"],
            },
        )
        assert response.status_code == 422
        assert "administrator must configure" in response.json()["detail"]

    async def test_direct_entry_supersedes_rather_than_edits(
        self, api: AsyncClient, login: Any, db: AsyncSession, crush_and_units: dict[str, Any]
    ) -> None:
        """The write path a non-calibration admin uses — still append-only."""
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/unit-conversions",
            headers=headers,
            json={
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "factor": "23.000000000000",
                "scope_type": "MATERIAL",
                "material_id": crush_and_units["material_id"],
                "effective_from": "2027-01-01",
                "basis_note": "Manual correction for test",
            },
        )
        assert response.status_code == 201
        new_row = response.json()
        assert new_row["supersedes_id"] is not None

        old_row = (
            await db.execute(
                select(UnitConversion).where(UnitConversion.id == new_row["supersedes_id"])
            )
        ).scalar_one()
        assert old_row.effective_to is not None
        assert old_row.factor == Decimal("22.500000000000")  # untouched


class TestCalibrationWorkflow:
    """The full weighbridge-calibration flow, end to end."""

    async def _log_readings(
        self, api: AsyncClient, headers: dict[str, str], units: dict[str, Any]
    ) -> list[dict[str, Any]]:
        readings = []
        for weight, volume in (("12.500", "281.00"), ("10.000", "224.80"), ("15.200", "341.50")):
            response = await api.post(
                "/unit-conversions/calibrations",
                headers=headers,
                json={
                    "material_id": units["material_id"],
                    "from_unit_id": units["ton_id"],
                    "to_unit_id": units["cft_id"],
                    "source_quantity": weight,
                    "target_quantity": volume,
                    "recorded_at": "2026-09-20",
                    "truck_number": "RJ14 GB 4001",
                },
            )
            assert response.status_code == 201, response.text
            readings.append(response.json())
        return readings

    async def test_recording_a_reading_does_not_change_the_live_factor(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        await self._log_readings(api, headers, crush_and_units)

        resolved = (
            await api.post(
                "/unit-conversions/resolve",
                headers=headers,
                json={
                    "from_unit_id": crush_and_units["ton_id"],
                    "to_unit_id": crush_and_units["cft_id"],
                    "material_id": crush_and_units["material_id"],
                },
            )
        ).json()
        assert resolved["factor"] == "22.500000000000"

    async def test_the_implied_factor_is_computed_correctly(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/unit-conversions/calibrations",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "source_quantity": "10.000",
                "target_quantity": "225.000",
                "recorded_at": "2026-09-20",
            },
        )
        assert response.json()["implied_factor"] == "22.500000000000"

    async def test_stats_report_average_median_and_spread(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        await self._log_readings(api, headers, crush_and_units)

        stats = (
            await api.get(
                "/unit-conversions/calibrations/stats",
                headers=headers,
                params={
                    "material_id": crush_and_units["material_id"],
                    "from_unit_id": crush_and_units["ton_id"],
                    "to_unit_id": crush_and_units["cft_id"],
                },
            )
        ).json()

        assert stats["count"] == 3
        assert stats["average_factor"] is not None
        assert stats["min_factor"] <= stats["median_factor"] <= stats["max_factor"]
        assert len(stats["readings"]) == 3

    async def test_a_discarded_reading_is_excluded_from_stats_but_not_deleted(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        readings = await self._log_readings(api, headers, crush_and_units)

        discard = await api.post(
            f"/unit-conversions/calibrations/{readings[0]['id']}/discard",
            headers=headers,
            json={"reason": "Weighbridge was mid-calibration that morning"},
        )
        assert discard.status_code == 200
        assert discard.json()["status"] == "DISCARDED"

        stats = (
            await api.get(
                "/unit-conversions/calibrations/stats",
                headers=headers,
                params={
                    "material_id": crush_and_units["material_id"],
                    "from_unit_id": crush_and_units["ton_id"],
                    "to_unit_id": crush_and_units["cft_id"],
                },
            )
        ).json()
        assert stats["count"] == 2

        # Still readable, never gone — the discarded one is itself evidence.
        all_readings = (
            await api.get(
                "/unit-conversions/calibrations",
                headers=headers,
                params={"material_id": crush_and_units["material_id"]},
            )
        ).json()
        assert len(all_readings) == 3

    async def test_discarding_without_a_reason_is_refused(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        readings = await self._log_readings(api, headers, crush_and_units)

        response = await api.post(
            f"/unit-conversions/calibrations/{readings[0]['id']}/discard",
            headers=headers,
            json={"reason": ""},
        )
        assert response.status_code == 422

    async def test_confirming_creates_an_effective_dated_conversion(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        """The core property: confirming never overwrites the current factor
        in place. It closes it and inserts a new, future-dated one."""
        headers = await login(PROCUREMENT)
        readings = await self._log_readings(api, headers, crush_and_units)

        confirmed = await api.post(
            "/unit-conversions/calibrations/confirm",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "factor": "22.480000000000",
                "reading_ids": [r["id"] for r in readings],
                "effective_from": "2026-10-01",
                "basis_note": "Test calibration",
            },
        )
        assert confirmed.status_code == 200
        body = confirmed.json()
        assert body["factor"] == "22.480000000000"
        assert body["effective_from"] == "2026-10-01"
        assert body["supersedes_id"] is not None

    async def test_the_old_factor_still_applies_before_the_new_effective_date(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        """A document priced in September must not be silently restated by an
        October recalibration."""
        headers = await login(PROCUREMENT)
        readings = await self._log_readings(api, headers, crush_and_units)
        await api.post(
            "/unit-conversions/calibrations/confirm",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "factor": "22.480000000000",
                "reading_ids": [r["id"] for r in readings],
                "effective_from": "2026-10-01",
            },
        )

        before = (
            await api.post(
                "/unit-conversions/resolve",
                headers=headers,
                json={
                    "from_unit_id": crush_and_units["ton_id"],
                    "to_unit_id": crush_and_units["cft_id"],
                    "material_id": crush_and_units["material_id"],
                    "at": "2026-09-25",
                },
            )
        ).json()
        after = (
            await api.post(
                "/unit-conversions/resolve",
                headers=headers,
                json={
                    "from_unit_id": crush_and_units["ton_id"],
                    "to_unit_id": crush_and_units["cft_id"],
                    "material_id": crush_and_units["material_id"],
                    "at": "2026-10-05",
                },
            )
        ).json()

        assert before["factor"] == "22.500000000000"
        assert after["factor"] == "22.480000000000"

    async def test_confirmed_readings_are_marked_applied_and_linked(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        readings = await self._log_readings(api, headers, crush_and_units)

        conversion = (
            await api.post(
                "/unit-conversions/calibrations/confirm",
                headers=headers,
                json={
                    "material_id": crush_and_units["material_id"],
                    "from_unit_id": crush_and_units["ton_id"],
                    "to_unit_id": crush_and_units["cft_id"],
                    "factor": "22.480000000000",
                    "reading_ids": [r["id"] for r in readings],
                    "effective_from": "2026-10-01",
                },
            )
        ).json()

        applied = (
            await api.get(
                "/unit-conversions/calibrations",
                headers=headers,
                params={"material_id": crush_and_units["material_id"], "status": "APPLIED"},
            )
        ).json()
        assert len(applied) == 3
        assert all(r["applied_conversion_id"] == conversion["id"] for r in applied)

    async def test_confirming_with_an_already_applied_reading_is_refused(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        readings = await self._log_readings(api, headers, crush_and_units)
        await api.post(
            "/unit-conversions/calibrations/confirm",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "factor": "22.480000000000",
                "reading_ids": [readings[0]["id"]],
                "effective_from": "2026-10-01",
            },
        )

        reuse = await api.post(
            "/unit-conversions/calibrations/confirm",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "factor": "22.500000000000",
                "reading_ids": [readings[0]["id"]],
                "effective_from": "2026-11-01",
            },
        )
        assert reuse.status_code == 422
        assert reuse.json()["rule"] == "reading_not_pending"

    async def test_confirming_with_no_readings_is_refused(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        """Never auto-averaged and applied — a person must name real evidence."""
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/unit-conversions/calibrations/confirm",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "factor": "22.480000000000",
                "reading_ids": [],
                "effective_from": "2026-10-01",
            },
        )
        assert response.status_code == 422

    async def test_calibration_produces_both_a_mechanical_and_a_narrative_audit_row(
        self, api: AsyncClient, login: Any, db: AsyncSession, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        readings = await self._log_readings(api, headers, crush_and_units)
        conversion = (
            await api.post(
                "/unit-conversions/calibrations/confirm",
                headers=headers,
                json={
                    "material_id": crush_and_units["material_id"],
                    "from_unit_id": crush_and_units["ton_id"],
                    "to_unit_id": crush_and_units["cft_id"],
                    "factor": "22.480000000000",
                    "reading_ids": [r["id"] for r in readings],
                    "effective_from": "2026-10-01",
                },
            )
        ).json()

        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.entity_id == conversion["id"])))
            .scalars()
            .all()
        )
        summaries = " | ".join(r.summary or "" for r in rows)
        assert "22.500000000000" in summaries
        assert "22.480000000000" in summaries
        assert "weighbridge reading" in summaries.lower()

    async def test_site_staff_cannot_calibrate(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(SITE_STAFF)
        response = await api.post(
            "/unit-conversions/calibrations",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "source_quantity": "10.0",
                "target_quantity": "225.0",
                "recorded_at": "2026-09-20",
            },
        )
        assert response.status_code == 403

    async def test_a_zero_weight_reading_is_refused(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(PROCUREMENT)
        response = await api.post(
            "/unit-conversions/calibrations",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "source_quantity": "0",
                "target_quantity": "225.0",
                "recorded_at": "2026-09-20",
            },
        )
        assert response.status_code == 422


class TestMaterialsCRUD:
    async def test_creating_a_material_links_its_base_unit(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """Regression test for the FOR UPDATE / outer-join bug: Material has
        two lazy="joined" relationships (category, base_unit)."""
        headers = await login(PROCUREMENT)
        category = (
            await api.get("/material-categories", headers=headers, params={"q": "Cement"})
        ).json()["items"][0]
        ton = (await api.get("/units", headers=headers, params={"q": "Metric"})).json()["items"][0]

        created = await api.post(
            "/materials",
            headers=headers,
            json={
                "sku": "TEST-MAT-01",
                "name": "Test Material",
                "category_id": category["id"],
                "base_unit_id": ton["id"],
                "standard_rate": "100.000000",
            },
        )
        assert created.status_code == 201
        material_id = created.json()["id"]

        # This is exactly the query path that broke: get_for_update() on a
        # model with joined eager-loaded relationships.
        updated = await api.patch(
            f"/materials/{material_id}", headers=headers, json={"standard_rate": "105.000000"}
        )
        assert updated.status_code == 200
        assert updated.json()["standard_rate"] == "105.000000"

    async def test_deactivating_a_material_soft_deletes_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PROCUREMENT)
        materials = (await api.get("/materials", headers=headers, params={"limit": 1})).json()
        material_id = materials["items"][0]["id"]

        response = await api.post(f"/materials/{material_id}/deactivate", headers=headers)
        assert response.status_code == 200

        listing = (await api.get("/materials", headers=headers, params={"q": "zzz-none"})).json()
        assert material_id not in [i["id"] for i in listing["items"]]


class TestAuthorisation:
    async def test_auditor_can_read_but_not_calibrate(
        self, api: AsyncClient, login: Any, crush_and_units: dict[str, Any]
    ) -> None:
        headers = await login(AUDITOR)
        assert (await api.get("/units", headers=headers)).status_code == 200
        assert (await api.get("/materials", headers=headers)).status_code == 200

        response = await api.post(
            "/unit-conversions/calibrations",
            headers=headers,
            json={
                "material_id": crush_and_units["material_id"],
                "from_unit_id": crush_and_units["ton_id"],
                "to_unit_id": crush_and_units["cft_id"],
                "source_quantity": "10.0",
                "target_quantity": "225.0",
                "recorded_at": "2026-09-20",
            },
        )
        assert response.status_code == 403
