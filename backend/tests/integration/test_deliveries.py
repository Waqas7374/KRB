"""Recording deliveries: the checks, the server-side pricing, idempotency and
scope (docs/05, docs/06 §4, docs/02 §6).

Seeded people: sm.gvh1 (site manager, GVH-S1 — records deliveries), staff.gvh1
(site staff, GVH-S1), procurement (sets rates), admin, auditor.

GVH-S1's centre is (31.411, 74.2461) with a 500 m radius; 0.009 degrees of
latitude is about 1 000 m.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow

pytestmark = pytest.mark.integration

SITE_MANAGER = "sm.gvh1@krb.example"
SITE_STAFF = "staff.gvh1@krb.example"
PROCUREMENT = "procurement@krb.example"
ADMIN = "admin@krb.example"
AUDITOR = "auditor@krb.example"

LAT, LNG = 31.411, 74.2461
NORTH_1KM = LAT + 0.009


def stamp(**delta: float) -> str:
    return (utcnow() - timedelta(**delta)).isoformat()


async def _keys(api: AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    materials = (await api.get("/materials", headers=headers, params={"limit": 100})).json()[
        "items"
    ]
    crush = next(m for m in materials if m["sku"] == "AGG-CRUSH-12")
    brick = next(m for m in materials if m["sku"] == "BRICK-A1")
    vendors = (
        await api.get("/vendors", headers=headers, params={"limit": 50, "status": "ACTIVE"})
    ).json()["items"]
    trucks = (await api.get("/truck-types", headers=headers, params={"limit": 50})).json()["items"]
    truck = next(t for t in trucks if t["code"] == "10-WHEELER")
    projects = (await api.get("/projects", headers=headers, params={"q": "GVH"})).json()["items"]
    project = next(p for p in projects if p["code"] == "GVH")
    sites = (await api.get("/sites", headers=headers, params={"project_id": project["id"]})).json()
    s1 = next(s for s in sites["items"] if s["code"] == "GVH-S1")
    s2 = next(s for s in sites["items"] if s["code"] == "GVH-S2")
    return {
        "crush": crush["id"],
        "ton": crush["base_unit_id"],
        "brick": brick["id"],
        "nos": brick["base_unit_id"],
        "vendor": vendors[0]["id"],
        "vendor2": vendors[1]["id"],
        "truck": truck["id"],
        "project": project["id"],
        "s1": s1["id"],
        "s2": s2["id"],
    }


def payload(keys: dict[str, str], quantity: str = "12.5", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "site_id": keys["s1"],
        "vendor_id": keys["vendor"],
        "truck_number": "LEB-1234",
        "truck_type_id": keys["truck"],
        "challan_number": "CH-001",
        "captured_at": stamp(minutes=5),
        "latitude": LAT,
        "longitude": LNG,
        "gps_accuracy_m": 8,
        "items": [{"material_id": keys["crush"], "unit_id": keys["ton"], "quantity": quantity}],
    }
    body.update(extra)
    return body


async def _record(
    api: AsyncClient,
    headers: dict[str, str],
    keys: dict[str, str],
    quantity: str = "12.5",
    **extra: Any,
) -> dict[str, Any]:
    response = await api.post("/deliveries", headers=headers, json=payload(keys, quantity, **extra))
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def flags(delivery: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["flag_type"]: f for f in delivery["flags"]}


async def _rate(
    api: AsyncClient,
    buyer: dict[str, str],
    keys: dict[str, str],
    rate: str,
    days_ago: int = 60,
    **extra: Any,
) -> dict[str, Any]:
    created = await api.post(
        "/vendor-rates",
        headers=buyer,
        json={
            "vendor_id": extra.pop("vendor_id", keys["vendor"]),
            "material_id": keys["crush"],
            "unit_id": extra.pop("unit_id", keys["ton"]),
            "rate": rate,
            "effective_from": (utcnow() - timedelta(days=days_ago)).date().isoformat(),
            **extra,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()  # type: ignore[no-any-return]


async def _approved_order(
    api: AsyncClient, login: Any, keys: dict[str, str], quantity: str, rate: str = "100"
) -> dict[str, Any]:
    admin = await login(ADMIN)
    created = await api.post(
        "/purchase-orders",
        headers=admin,
        json={
            "vendor_id": keys["vendor"],
            "project_id": keys["project"],
            "site_id": keys["s1"],
            "items": [
                {
                    "material_id": keys["crush"],
                    "unit_id": keys["ton"],
                    "quantity": quantity,
                    "rate": rate,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    po = created.json()
    submitted = await api.post(f"/purchase-orders/{po['id']}/submit", headers=admin)
    assert submitted.status_code == 200, submitted.text
    approved = await api.post(
        f"/approvals/requests/{submitted.json()['approval_request_id']}/approve",
        headers=await login(PROCUREMENT),
        json={},
    )
    assert approved.status_code == 200, approved.text
    return po  # type: ignore[no-any-return]


# -----------------------------------------------------------------------------


class TestCapture:
    async def test_a_delivery_with_a_purchase_order_and_no_problems_goes_straight_to_submitted(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "100")
        site = await login(SITE_MANAGER)
        delivery = await _record(api, site, keys, purchase_order_id=po["id"])
        assert delivery["status"] == "SUBMITTED"
        assert delivery["flags"] == [] and delivery["has_open_flags"] is False
        assert delivery["delivery_number"].startswith("DLV-")
        assert delivery["is_inside_geofence"] is True
        assert Decimal(delivery["distance_from_site_m"]) == 0
        assert delivery["purchase_order_number"] == po["po_number"]
        assert delivery["submitted_by_name"] == "Imran Shah"

    async def test_no_purchase_order_is_flagged_and_sent_for_review_never_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        delivery = await _record(api, site, keys)
        assert delivery["status"] == "UNDER_REVIEW"
        no_po = flags(delivery)["NO_PO"]
        assert no_po["severity"] == "WARNING" and no_po["status"] == "OPEN"
        assert delivery["has_open_flags"] is True and delivery["flag_count"] == len(
            delivery["flags"]
        )

    async def test_a_project_can_make_a_purchase_order_mandatory(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        await db.execute(
            text(
                "UPDATE projects SET settings = settings "
                "|| '{\"require_po_for_delivery\": true}'::jsonb WHERE id = :id"
            ),
            {"id": keys["project"]},
        )
        refused = await api.post("/deliveries", headers=site, json=payload(keys))
        assert refused.status_code == 422 and refused.json()["rule"] == "purchase_order_required"

    async def test_the_list_and_detail_are_scoped_to_the_callers_sites(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        mine = await _record(api, site, keys)
        listed = (await api.get("/deliveries", headers=site, params={"limit": 50})).json()["items"]
        assert mine["id"] in {d["id"] for d in listed}
        row = next(d for d in listed if d["id"] == mine["id"])
        assert row["site_code"] == "GVH-S1" and row["worst_severity"] == "WARNING"
        assert "Crush 12mm" in row["material_summary"]
        assert (await api.get(f"/deliveries/{mine['id']}", headers=site)).status_code == 200

    async def test_a_site_manager_cannot_record_at_another_site(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        refused = await api.post(
            "/deliveries", headers=site, json=payload(keys, site_id=keys["s2"])
        )
        # Out of scope reads as "no such site", not "forbidden": no confirming it exists.
        assert refused.status_code == 404

    async def test_an_auditor_can_read_but_not_record(self, api: AsyncClient, login: Any) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        mine = await _record(api, site, keys)
        auditor = await login(AUDITOR)
        assert (await api.get(f"/deliveries/{mine['id']}", headers=auditor)).status_code == 200
        assert (
            await api.post("/deliveries", headers=auditor, json=payload(keys))
        ).status_code == 403

    async def test_bad_references_are_refused_with_field_errors(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        wrong_unit = payload(keys)
        wrong_unit["items"][0]["unit_id"] = keys["nos"]  # bricks are counted, crush is weighed
        refused = await api.post("/deliveries", headers=site, json=wrong_unit)
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "items.0.unit_id"

        unknown_vendor = await api.post(
            "/deliveries", headers=site, json=payload(keys, vendor_id=str(uuid4()))
        )
        assert unknown_vendor.status_code == 422
        naive = await api.post(
            "/deliveries", headers=site, json=payload(keys, captured_at="2026-09-25T10:00:00")
        )
        assert naive.status_code == 422


class TestIdempotency:
    async def test_the_same_id_twice_stores_one_delivery_and_replays_it(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        client_id = str(uuid4())
        first = await api.post("/deliveries", headers=site, json=payload(keys, id=client_id))
        second = await api.post("/deliveries", headers=site, json=payload(keys, id=client_id))
        assert first.status_code == 201 and second.status_code == 200
        assert first.json()["id"] == second.json()["id"] == client_id
        assert first.json()["delivery_number"] == second.json()["delivery_number"]
        count = (
            await db.execute(
                text("SELECT count(*) FROM deliveries WHERE id = :id"), {"id": client_id}
            )
        ).scalar_one()
        assert count == 1

    async def test_a_replay_even_with_different_content_changes_nothing(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        client_id = str(uuid4())
        await api.post("/deliveries", headers=site, json=payload(keys, "12.5", id=client_id))
        again = await api.post("/deliveries", headers=site, json=payload(keys, "99", id=client_id))
        assert again.status_code == 200
        assert Decimal(again.json()["items"][0]["quantity"]) == Decimal("12.5")

    async def test_someone_elses_id_is_not_a_replay(self, api: AsyncClient, login: Any) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        client_id = str(uuid4())
        await api.post("/deliveries", headers=site, json=payload(keys, id=client_id))
        staff = await login(SITE_STAFF)
        clash = await api.post("/deliveries", headers=staff, json=payload(keys, id=client_id))
        assert clash.status_code == 409


class TestTonnage:
    async def test_the_trucks_own_limit_applies_when_no_rule_says_otherwise(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        heavy = flags(await _record(api, site, keys, "22"))["TONNAGE_ANOMALY"]
        assert heavy["severity"] == "CRITICAL" and Decimal(heavy["deviation_pct"]) == Decimal(
            "37.50"
        )
        assert heavy["message"].startswith("Tonnage 22.0t exceeds 16.0t maximum for 10-Wheeler")
        assert heavy["rule_id"] is None
        assert "TONNAGE_ANOMALY" not in flags(
            await _record(api, site, keys, "15.9", truck_number="B")
        )

    async def test_a_configured_rule_overrides_the_truck_default_and_is_snapshotted(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        rule = await api.post(
            "/business-rules",
            headers=admin,
            json={
                "rule_type": "TONNAGE_MAX",
                "name": "10-wheeler carrying crush",
                "scope": {"truck_type_id": keys["truck"], "material_id": keys["crush"]},
                "value": {"max": "18"},
                "effective_from": "2026-01-01",
            },
        )
        assert rule.status_code == 201, rule.text
        assert "TONNAGE_ANOMALY" not in flags(await _record(api, site, keys, "17"))
        delivery = await _record(api, site, keys, "19", truck_number="B")
        over = flags(delivery)["TONNAGE_ANOMALY"]
        assert over["rule_id"] == rule.json()["id"]
        assert over["rule_snapshot"]["value"] == {"max": "18", "unit": "TON"}

        # Tightening the rule next month never rewrites why this was flagged.
        await api.patch(
            f"/business-rules/{rule.json()['id']}", headers=admin, json={"value": {"max": "10"}}
        )
        kept = flags((await api.get(f"/deliveries/{delivery['id']}", headers=site)).json())
        assert kept["TONNAGE_ANOMALY"]["rule_snapshot"]["value"]["max"] == "18"

    async def test_a_load_that_is_not_counted_by_weight_is_not_checked_for_tonnage(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        bricks = payload(keys)
        bricks["items"] = [
            {"material_id": keys["brick"], "unit_id": keys["nos"], "quantity": "5000"}
        ]
        recorded = await api.post("/deliveries", headers=site, json=bricks)
        assert recorded.status_code == 201, recorded.text
        assert "TONNAGE_ANOMALY" not in flags(recorded.json())


class TestGeofence:
    async def _flag(
        self, api: AsyncClient, login: Any, **extra: Any
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        delivery = await _record(api, site, keys, **extra)
        return delivery, flags(delivery).get("GEOFENCE_MISMATCH")

    async def test_inside_the_fence_raises_nothing(self, api: AsyncClient, login: Any) -> None:
        delivery, flag = await self._flag(api, login)
        assert flag is None and delivery["is_inside_geofence"] is True

    async def test_a_kilometre_away_is_critical_but_the_delivery_is_still_saved(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, flag = await self._flag(api, login, latitude=NORTH_1KM)
        assert flag is not None and flag["severity"] == "CRITICAL"
        assert (
            450 < float(flag["actual_value"]) < 550
        )  # ~1 000 m from centre, less the 500 m radius
        assert delivery["status"] == "UNDER_REVIEW" and delivery["is_inside_geofence"] is False
        assert float(delivery["distance_from_site_m"]) == float(flag["actual_value"])
        # The evidence is kept whatever the outcome.
        assert float(delivery["captured_lat"]) == pytest.approx(NORTH_1KM)

    async def test_no_gps_fix_is_a_warning(self, api: AsyncClient, login: Any) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        body = payload(keys)
        for key in ("latitude", "longitude", "gps_accuracy_m"):
            body.pop(key)
        delivery = (await api.post("/deliveries", headers=site, json=body)).json()
        flag = flags(delivery)["GEOFENCE_MISMATCH"]
        assert flag["severity"] == "WARNING" and flag["actual_value"] is None
        assert delivery["location_source"] == "MANUAL"

    async def test_a_site_scoped_rule_changes_the_radius(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        keys = await _keys(api, admin)
        # 600 m north is outside the site's own 500 m radius...
        north_600 = LAT + 0.0054
        _, outside = await self._flag(api, login, latitude=north_600)
        assert outside is not None
        # ...but inside a 2 km rule for that site.
        await api.post(
            "/business-rules",
            headers=admin,
            json={
                "rule_type": "GEOFENCE_RADIUS",
                "scope": {"site_id": keys["s1"]},
                "value": {"radius_m": 2000},
                "effective_from": "2026-01-01",
            },
        )
        _, inside = await self._flag(api, login, latitude=north_600, truck_number="B")
        assert inside is None


class TestPricing:
    async def test_the_rate_and_amount_are_resolved_on_the_server(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, buyer, keys, "100")
        delivery = await _record(api, site, keys, "12.5")
        item = delivery["items"][0]
        assert Decimal(item["rate"]) == Decimal(100) and item["rate_source"] == "MANUAL"
        assert Decimal(item["amount"]) == Decimal("1250")
        assert Decimal(delivery["total_amount"]) == Decimal("1250")
        assert "RATE_MISSING" not in flags(delivery)

    async def test_a_delivery_with_no_rate_is_saved_unpriced_and_flagged(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        delivery = await _record(api, site, keys)
        assert delivery["items"][0]["amount"] is None
        assert flags(delivery)["RATE_MISSING"]["severity"] == "WARNING"

    async def test_a_vendor_priced_per_cft_is_converted_from_tons_and_snapshotted(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        units = (await api.get("/units", headers=buyer, params={"limit": 100})).json()["items"]
        cft = next(u["id"] for u in units if u["code"] == "CFT")
        await _rate(api, buyer, keys, "50", unit_id=cft)
        delivery = await _record(api, site, keys, "10")
        item = delivery["items"][0]
        # 10 TON x 23.2 CFT/TON (Crush 12mm) = 232 CFT at 50 = 11 600.
        assert Decimal(item["converted_quantity"]) == Decimal(232)
        assert item["converted_unit_code"] == "CFT" and Decimal(
            item["conversion_factor"]
        ) == Decimal("23.2")
        assert Decimal(item["amount"]) == Decimal(11600)

    async def test_a_later_rate_change_never_restates_a_delivery_already_counted(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, buyer, keys, "100", days_ago=60)
        delivery = await _record(api, site, keys, "10")
        await _rate(api, buyer, keys, "105", days_ago=0, reason="Fuel surcharge")
        after = (await api.get(f"/deliveries/{delivery['id']}", headers=site)).json()
        assert Decimal(after["items"][0]["rate"]) == Decimal(100)
        assert Decimal(after["items"][0]["amount"]) == Decimal(1000)

    async def test_the_rate_is_resolved_as_of_the_capture_time(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, buyer, keys, "100", days_ago=60)
        await _rate(api, buyer, keys, "105", days_ago=5)
        old = await _record(api, site, keys, "10", captured_at=stamp(days=20))
        new = await _record(api, site, keys, "10", truck_number="B", captured_at=stamp(hours=1))
        assert Decimal(old["items"][0]["rate"]) == Decimal(100)
        assert Decimal(new["items"][0]["rate"]) == Decimal(105)


class TestOtherChecks:
    async def test_the_same_truck_and_material_within_twenty_minutes_is_suspect(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        first = await _record(
            api, site, keys, truck_number="LEB 1234", captured_at=stamp(minutes=30)
        )
        second = await _record(
            api, site, keys, truck_number="leb-1234", captured_at=stamp(minutes=20)
        )
        assert "DUPLICATE_SUSPECT" not in flags(first)
        dup = flags(second)["DUPLICATE_SUSPECT"]
        assert first["delivery_number"] in dup["message"]
        # A different truck, or the same truck an hour later, is fine.
        other = await _record(api, site, keys, truck_number="XYZ-9", captured_at=stamp(minutes=19))
        later = await _record(
            api, site, keys, truck_number="LEB 1234", captured_at=stamp(minutes=1)
        )
        assert "DUPLICATE_SUSPECT" not in flags(other)
        assert "DUPLICATE_SUSPECT" in flags(later)  # 19 min after the second

    async def test_a_capture_long_before_arrival_is_late(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        late = flags(await _record(api, site, keys, captured_at=stamp(days=5)))["LATE_SUBMISSION"]
        assert late["severity"] == "WARNING" and Decimal(late["actual_value"]) >= Decimal(5)

    async def test_a_wrong_device_clock_is_flagged_but_offline_delay_is_not_skew(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        wrong = await _record(
            api, site, keys, device_time=(utcnow() + timedelta(hours=2)).isoformat()
        )
        assert "CLOCK_SKEW" in flags(wrong) and abs(wrong["clock_skew_seconds"]) > 7000
        # Captured a day ago but the device's clock is right: late, not skewed.
        offline = await _record(
            api,
            site,
            keys,
            truck_number="B",
            captured_at=stamp(hours=20),
            device_time=utcnow().isoformat(),
            was_offline=True,
        )
        assert "CLOCK_SKEW" not in flags(offline) and offline["was_offline"] is True

    async def test_a_capture_time_in_the_future_is_flagged(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        future = await _record(
            api, site, keys, captured_at=(utcnow() + timedelta(hours=3)).isoformat()
        )
        assert "CLOCK_SKEW" in flags(future)

    async def test_a_suspended_vendors_delivery_is_saved_and_flagged(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        await db.execute(
            text(
                "UPDATE vendors SET status = 'SUSPENDED', suspension_reason = 'Under review' "
                "WHERE id = :id"
            ),
            {"id": keys["vendor"]},
        )
        assert "VENDOR_INACTIVE" in flags(await _record(api, site, keys))

    async def test_a_daily_cap_rule_flags_the_delivery_that_exceeds_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        await api.post(
            "/business-rules",
            headers=admin,
            json={
                "rule_type": "DAILY_DELIVERY_CAP",
                "scope": {"vendor_id": keys["vendor"], "site_id": keys["s1"]},
                "value": {"max_deliveries": 2},
                "effective_from": "2026-01-01",
            },
        )
        results = [
            flags(
                await _record(
                    api, site, keys, truck_number=f"T{i}", captured_at=stamp(minutes=60 - i * 10)
                )
            )
            for i in range(3)
        ]
        assert "DAILY_CAP_EXCEEDED" not in results[0] and "DAILY_CAP_EXCEEDED" not in results[1]
        assert "DAILY_CAP_EXCEEDED" in results[2]


class TestPurchaseOrderBalance:
    async def test_a_delivery_that_takes_the_order_past_its_tolerance_is_flagged(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        po = await _approved_order(api, login, keys, "100")
        first = await _record(api, site, keys, "60", purchase_order_id=po["id"], truck_number="A")
        second = await _record(api, site, keys, "41", purchase_order_id=po["id"], truck_number="B")
        third = await _record(api, site, keys, "5", purchase_order_id=po["id"], truck_number="C")
        assert "PO_QTY_EXCEEDED" not in flags(first)
        assert "PO_QTY_EXCEEDED" not in flags(second)  # 101 <= 100 + 2 %
        over = flags(third)["PO_QTY_EXCEEDED"]  # 106 > 102
        assert (
            Decimal(over["actual_value"]) == Decimal(106)
            and over["rule_snapshot"]["value"]["pct"] == "2"
        )

    async def test_a_rejected_or_cancelled_delivery_no_longer_counts(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        po = await _approved_order(api, login, keys, "100")
        first = await _record(api, site, keys, "90", purchase_order_id=po["id"], truck_number="A")
        await db.execute(
            text(
                "UPDATE deliveries SET status = 'REJECTED', rejection_reason = 'Wrong material' "
                "WHERE id = :id"
            ),
            {"id": first["id"]},
        )
        again = await _record(api, site, keys, "90", purchase_order_id=po["id"], truck_number="B")
        assert "PO_QTY_EXCEEDED" not in flags(again)

    async def test_the_order_must_belong_to_the_same_vendor_and_be_receivable(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        po = await _approved_order(api, login, keys, "100")
        wrong_vendor = await api.post(
            "/deliveries",
            headers=site,
            json=payload(keys, vendor_id=keys["vendor2"], purchase_order_id=po["id"]),
        )
        assert wrong_vendor.status_code == 422
        assert wrong_vendor.json()["errors"][0]["field"] == "purchase_order_id"

        admin = await login(ADMIN)
        await api.post(
            f"/purchase-orders/{po['id']}/cancel", headers=admin, json={"reason": "Vendor withdrew"}
        )
        cancelled = await api.post(
            "/deliveries", headers=site, json=payload(keys, purchase_order_id=po["id"])
        )
        assert cancelled.status_code == 422 and "cancelled" in cancelled.text


class TestDatabaseGuarantees:
    async def test_a_rejected_delivery_must_carry_a_reason(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        delivery = await _record(api, site, keys)
        with pytest.raises(IntegrityError, match="rejection_needs_reason"):
            async with db.begin_nested():
                await db.execute(
                    text("UPDATE deliveries SET status = 'REJECTED' WHERE id = :id"),
                    {"id": delivery["id"]},
                )

    async def test_review_decisions_are_append_only(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        from sqlalchemy.exc import DBAPIError

        site = await login(SITE_MANAGER)
        keys = await _keys(api, await login(ADMIN))
        delivery = await _record(api, site, keys)
        await db.execute(
            text(
                """
                INSERT INTO delivery_reviews (id, delivery_id, company_id, action, reviewed_at,
                                              previous_status, new_status)
                SELECT gen_random_uuid(), id, company_id, 'ACCEPT', now(), 'UNDER_REVIEW',
                       'APPROVED'
                FROM deliveries WHERE id = :id
                """
            ),
            {"id": delivery["id"]},
        )
        for statement in (
            "UPDATE delivery_reviews SET comments = 'edited'",
            "DELETE FROM delivery_reviews",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                async with db.begin_nested():
                    await db.execute(text(statement))
