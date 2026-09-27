"""Mobile sync: pushing what a phone captured offline and pulling what it
caches (docs/06 §4).

Seeded people: staff.gvh1 (site staff, GVH-S1 — the phone user), sm.gvh1 (site
manager, reviews), admin, auditor (cannot capture).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_deliveries import (
    ADMIN,
    AUDITOR,
    _approved_order,
    _keys,
    _rate,
    payload,
)

pytestmark = pytest.mark.integration

STAFF = "staff.gvh1@krb.example"
MANAGER = "sm.gvh1@krb.example"
DEVICE = "test-phone-0001"


def op(body: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """One push operation. The delivery's own id is what makes it idempotent."""
    body = {"id": str(uuid4()), **body}
    return {
        "op_id": str(uuid4()),
        "entity": "delivery",
        "op": "create",
        "payload": body,
        **extra,
    }


def batch(*ops: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "device_id": DEVICE,
        "app_version": "1.0.0",
        "platform": "ANDROID",
        "ops": list(ops),
        **extra,
    }


async def _push(api: AsyncClient, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    response = await api.post("/sync/push", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def _pull_all(
    api: AsyncClient, headers: dict[str, str], since: int = 0, **params: Any
) -> tuple[list[dict[str, Any]], int]:
    """Follow the cursor to the end, as the app does."""
    seen: list[dict[str, Any]] = []
    cursor = since
    for _ in range(200):
        response = await api.get(
            "/sync/pull", headers=headers, params={"since": cursor, "limit": 50, **params}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["server_seq"] >= cursor
        seen.extend(page["changes"])
        cursor = page["server_seq"]
        if not page["has_more"]:
            return seen, cursor
    raise AssertionError("pull never finished")


class TestPush:
    async def test_an_entry_is_recorded_and_answered_with_the_servers_view_of_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        entry = op(payload(keys, "12.5"))
        result = (await _push(api, staff, batch(entry)))["results"][0]

        assert result["op_id"] == entry["op_id"] and result["outcome"] == "applied"
        record = result["record"]
        assert record["id"] == entry["payload"]["id"]
        assert record["delivery_number"] and record["status"] == "UNDER_REVIEW"
        # No order was named, so the server raised a flag the phone can show.
        assert record["flags"]
        # The delivery exists on the server, marked as having waited offline.
        stored = (await api.get(f"/deliveries/{record['id']}", headers=staff)).json()
        assert stored["was_offline"] is True and stored["device_id"] == DEVICE

    async def test_pushing_the_same_entry_again_records_it_once(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        entry = op(payload(keys))
        first = (await _push(api, staff, batch(entry)))["results"][0]
        second = (await _push(api, staff, batch(entry)))["results"][0]

        assert first["outcome"] == "applied" and second["outcome"] == "duplicate"
        assert second["record"]["id"] == first["record"]["id"]
        assert second["record"]["delivery_number"] == first["record"]["delivery_number"]
        listing = (await api.get("/deliveries", headers=staff, params={"limit": 100})).json()[
            "items"
        ]
        assert [d["id"] for d in listing].count(first["record"]["id"]) == 1

    async def test_one_bad_entry_does_not_stop_the_good_ones_around_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        good_a = op(payload(keys, truck_number="AAA-111"))
        broken = op({"site_id": keys["s1"]})  # no vendor, no lines
        unknown_vendor = op(payload(keys, vendor_id=str(uuid4()), truck_number="BBB-222"))
        good_b = op(payload(keys, truck_number="CCC-333"))
        response = await _push(api, staff, batch(good_a, broken, unknown_vendor, good_b))

        outcomes = [r["outcome"] for r in response["results"]]
        assert outcomes == ["applied", "rejected", "rejected", "applied"]
        assert [r["op_id"] for r in response["results"]] == [
            o["op_id"] for o in (good_a, broken, unknown_vendor, good_b)
        ]
        invalid = response["results"][1]["error"]
        assert invalid["code"] == "invalid_payload" and invalid["retryable"] is False
        assert {f["field"] for f in invalid["fields"]} >= {"vendor_id", "items"}
        # The good ones behind the bad ones really are stored.
        for good in (good_a, good_b):
            found = await api.get(f"/deliveries/{good['payload']['id']}", headers=staff)
            assert found.status_code == 200

    async def test_a_site_the_person_is_not_assigned_to_is_refused_for_good(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)  # assigned to GVH-S1 only
        entry = op(payload(keys, site_id=keys["s2"]))
        result = (await _push(api, staff, batch(entry)))["results"][0]
        assert result["outcome"] == "rejected"
        assert result["error"]["code"] == "site_not_permitted"
        assert result["error"]["retryable"] is False

    async def test_the_phone_cannot_supply_its_own_price(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        entry = op(payload(keys, rate="1"))
        result = (await _push(api, staff, batch(entry)))["results"][0]
        assert result["outcome"] == "rejected"
        assert result["error"]["code"] == "invalid_payload"

    async def test_a_person_who_may_not_see_prices_is_not_shown_one(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login("procurement@krb.example"), keys, "100")
        po = await _approved_order(api, login, keys, "100")
        result = (
            await _push(
                api, await login(STAFF), batch(op(payload(keys, purchase_order_id=po["id"])))
            )
        )["results"][0]
        assert result["outcome"] == "applied"
        assert result["record"]["rate"] is None and result["record"]["amount"] is None

    async def test_a_delivery_head_office_already_decided_wins_over_a_late_correction(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        created = op(payload(keys, truck_number="LATE-001"))
        record = (await _push(api, staff, batch(created)))["results"][0]["record"]
        approved = await api.post(
            f"/deliveries/{record['id']}/approve",
            headers=await login(MANAGER),
            json={"comments": "Checked at the gate"},
        )
        assert approved.status_code == 200, approved.text

        correction = {
            "op_id": str(uuid4()),
            "entity": "delivery_correction",
            "op": "update",
            "payload": {**created["payload"], "truck_number": "LATE-002"},
        }
        result = (await _push(api, staff, batch(correction)))["results"][0]
        assert result["outcome"] == "conflict"
        assert result["error"]["code"] == "already_reviewed"
        assert result["record"]["id"] == record["id"]
        assert result["record"]["status"] == "APPROVED"

    async def test_a_delivery_sent_back_can_be_corrected_from_the_phone(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        created = op(payload(keys, truck_number="FIX-001"))
        record = (await _push(api, staff, batch(created)))["results"][0]["record"]
        sent_back = await api.post(
            f"/deliveries/{record['id']}/request-correction",
            headers=await login(MANAGER),
            json={"comments": "Wrong truck number"},
        )
        assert sent_back.status_code == 200, sent_back.text

        correction = {
            "op_id": str(uuid4()),
            "entity": "delivery_correction",
            "op": "update",
            "payload": {**created["payload"], "truck_number": "FIX-002"},
        }
        result = (await _push(api, staff, batch(correction)))["results"][0]
        assert result["outcome"] == "applied", result
        stored = (await api.get(f"/deliveries/{record['id']}", headers=staff)).json()
        assert stored["truck_number"] == "FIX-002"

    async def test_a_person_who_cannot_capture_is_refused_outright(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        response = await api.post(
            "/sync/push", headers=await login(AUDITOR), json=batch(op(payload(keys)))
        )
        assert response.status_code == 403

    async def test_the_batch_size_is_bounded(self, api: AsyncClient, login: Any) -> None:
        keys = await _keys(api, await login(ADMIN))
        ops = [op(payload(keys)) for _ in range(51)]
        response = await api.post("/sync/push", headers=await login(STAFF), json=batch(*ops))
        assert response.status_code == 422

    async def test_the_device_is_registered_and_a_revoked_one_is_refused(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        await _push(api, staff, batch(op(payload(keys)), push_token="tok-1"))
        row = (
            await db.execute(
                text(
                    "SELECT platform, app_version, push_token, last_sync_at IS NOT NULL "
                    "FROM user_devices WHERE device_uid = :d"
                ),
                {"d": DEVICE},
            )
        ).one()
        assert tuple(row) == ("ANDROID", "1.0.0", "tok-1", True)

        await db.execute(
            text("UPDATE user_devices SET revoked_at = now() WHERE device_uid = :d"), {"d": DEVICE}
        )
        pushed = await api.post("/sync/push", headers=staff, json=batch(op(payload(keys))))
        pulled = await api.get("/sync/pull", headers=staff, params={"device_id": DEVICE})
        assert pushed.status_code == 403 and pulled.status_code == 403


class TestPull:
    async def test_a_full_pull_carries_what_a_phone_needs_to_capture(
        self, api: AsyncClient, login: Any
    ) -> None:
        staff = await login(STAFF)
        changes, cursor = await _pull_all(api, staff)
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for change in changes:
            by_entity.setdefault(change["entity"], []).append(change)

        assert {"materials", "units", "truck_types", "vendors", "sites", "rules"} <= set(by_entity)
        assert cursor == max(c["server_seq"] for c in changes)
        # In order, so a page boundary never skips a row.
        seqs = [c["server_seq"] for c in changes]
        assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))

        material = next(m for m in by_entity["materials"] if m["data"]["sku"] == "AGG-CRUSH-12")
        assert material["data"]["base_unit_id"] in material["data"]["unit_ids"]
        # Only the sites this person may capture at, with what the geofence needs.
        assert {s["data"]["code"] for s in by_entity["sites"]} == {"GVH-S1"}
        site = by_entity["sites"][0]["data"]
        assert site["latitude"] == pytest.approx(31.411) and site["longitude"] == pytest.approx(
            74.2461
        )
        assert float(site["geofence_radius_m"]) > 0
        # Rules the phone can pre-check with; the server-only ones stay on the server.
        rule_types = {r["data"]["rule_type"] for r in by_entity["rules"]}
        assert "GEOFENCE_RADIUS" in rule_types
        assert not rule_types & {"QTY_TOLERANCE", "PRICE_TOLERANCE", "APPROVAL_LIMIT"}

    async def test_pull_carries_no_prices(self, api: AsyncClient, login: Any) -> None:
        changes, _ = await _pull_all(api, await login(STAFF))
        flat = str([c["data"] for c in changes])
        for word in ("rate", "standard_rate", "amount", "price"):
            assert f"'{word}'" not in flat

    async def test_a_second_pull_from_the_cursor_is_empty(
        self, api: AsyncClient, login: Any
    ) -> None:
        staff = await login(STAFF)
        _, cursor = await _pull_all(api, staff)
        page = (await api.get("/sync/pull", headers=staff, params={"since": cursor})).json()
        assert page["changes"] == [] and page["has_more"] is False
        assert page["server_seq"] == cursor

    async def test_paging_visits_every_row_exactly_once(self, api: AsyncClient, login: Any) -> None:
        staff = await login(STAFF)
        whole = (await api.get("/sync/pull", headers=staff, params={"limit": 500})).json()
        assert whole["has_more"] is False
        paged, _ = await _pull_all(api, staff)  # 50 a page
        assert [(c["entity"], c["id"]) for c in paged] == [
            (c["entity"], c["id"]) for c in whole["changes"]
        ]

    async def test_a_change_after_the_cursor_arrives_and_only_that(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        staff = await login(STAFF)
        _, cursor = await _pull_all(api, staff)
        await db.execute(
            text("UPDATE materials SET name = 'Crush (renamed)' WHERE sku = 'AGG-CRUSH-12'")
        )
        changes, new_cursor = await _pull_all(api, staff, since=cursor)
        assert [c["entity"] for c in changes] == ["materials"]
        assert changes[0]["data"]["name"] == "Crush (renamed)"
        assert new_cursor > cursor

    async def test_a_changed_alternate_unit_changes_the_material_it_belongs_to(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        staff = await login(STAFF)
        _, cursor = await _pull_all(api, staff)
        await db.execute(
            text(
                "UPDATE material_units SET is_issue_default = is_issue_default "
                "WHERE id = (SELECT id FROM material_units LIMIT 1)"
            )
        )
        changes, _ = await _pull_all(api, staff, since=cursor)
        assert [c["entity"] for c in changes] == ["materials"]

    async def test_a_deleted_row_arrives_as_a_tombstone(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        staff = await login(STAFF)
        _, cursor = await _pull_all(api, staff)
        await db.execute(
            text("UPDATE truck_types SET deleted_at = now() WHERE code = '10-WHEELER'")
        )
        changes, _ = await _pull_all(api, staff, since=cursor, entities=["truck_types"])
        assert len(changes) == 1 and changes[0]["deleted"] is True

    async def test_an_order_is_offered_while_it_can_be_received_and_withdrawn_after(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        _, cursor = await _pull_all(api, staff)
        po = await _approved_order(api, login, keys, "40")

        offered, cursor = await _pull_all(api, staff, since=cursor, entities=["open_pos"])
        assert [c["id"] for c in offered] == [po["id"]]
        data = offered[0]["data"]
        assert data["vendor_id"] == keys["vendor"] and data["status"] == "APPROVED"
        assert [(i["material_id"], i["quantity"]) for i in data["items"]] == [
            (keys["crush"], "40.0000")
        ]

        await db.execute(
            text("UPDATE purchase_orders SET status = 'CANCELLED' WHERE id = :i"), {"i": po["id"]}
        )
        withdrawn, _ = await _pull_all(api, staff, since=cursor, entities=["open_pos"])
        assert [(c["id"], c["deleted"]) for c in withdrawn] == [(po["id"], True)]

    async def test_orders_elsewhere_are_not_offered(self, api: AsyncClient, login: Any) -> None:
        keys = await _keys(api, await login(ADMIN))
        admin = await login(ADMIN)
        # An order for the other site, which this person is not assigned to.
        created = await api.post(
            "/purchase-orders",
            headers=admin,
            json={
                "vendor_id": keys["vendor"],
                "project_id": keys["project"],
                "site_id": keys["s2"],
                "items": [
                    {
                        "material_id": keys["crush"],
                        "unit_id": keys["ton"],
                        "quantity": "10",
                        "rate": "100",
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        offered, _ = await _pull_all(api, await login(STAFF), entities=["open_pos"])
        assert created.json()["id"] not in {c["id"] for c in offered}

    async def test_what_a_person_may_not_sync_is_reported_rather_than_left_empty(
        self, api: AsyncClient, login: Any
    ) -> None:
        page = (await api.get("/sync/pull", headers=await login(AUDITOR))).json()
        assert "sites" in page["not_permitted"] and "open_pos" in page["not_permitted"]

    async def test_an_unknown_entity_is_a_validation_error(
        self, api: AsyncClient, login: Any
    ) -> None:
        response = await api.get(
            "/sync/pull", headers=await login(STAFF), params={"entities": ["bananas"]}
        )
        assert response.status_code == 422

    async def test_pulling_needs_a_signed_in_person(self, api: AsyncClient) -> None:
        assert (await api.get("/sync/pull")).status_code == 401


def _field_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {n for v in value.values() for n in _field_names(v)}
    if isinstance(value, list):
        return {n for v in value for n in _field_names(v)}
    return set()


class TestMyDeliveries:
    """The fate of what a phone sent up comes back down the same cursor."""

    async def _mine(
        self, api: AsyncClient, login: Any, who: str = STAFF, since: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        changes, cursor = await _pull_all(
            api, await login(who), since=since, entities=["my_deliveries"]
        )
        return changes, cursor

    async def test_a_persons_own_entries_come_back_with_their_status_and_flags(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        entry = op(payload(keys, "12.5", truck_number="MINE-1"))
        record = (await _push(api, staff, batch(entry)))["results"][0]["record"]

        mine, _ = await self._mine(api, login)
        [row] = [c for c in mine if c["id"] == record["id"]]
        data = row["data"]
        assert data["delivery_number"] == record["delivery_number"]
        assert data["status"] == "UNDER_REVIEW" and data["can_correct"] is False
        assert data["open_flags"] and {"flag_type", "severity", "message"} <= set(
            data["open_flags"][0]
        )
        assert data["review"] is None
        # Enough to correct it from: the entry as recorded.
        entry_back = data["entry"]
        assert entry_back["truck_number"] == "MINE-1" and entry_back["site_id"] == keys["s1"]
        assert [(i["material_id"], i["quantity"]) for i in entry_back["items"]] == [
            (keys["crush"], "12.5000")
        ]
        # A phone holds no prices, whoever it belongs to: no field for one, anywhere in the entry.
        assert not {"rate", "amount", "unit_price", "vendor_rate_id"} & _field_names(data)

    async def test_only_the_persons_own_entries_and_nobody_elses(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        theirs = await _push(
            api, await login(MANAGER), batch(op(payload(keys, truck_number="THEIRS-1")))
        )
        mine, _ = await self._mine(api, login, STAFF)
        assert theirs["results"][0]["record"]["id"] not in {c["id"] for c in mine}
        theirs_seen, _ = await self._mine(api, login, MANAGER)
        assert theirs["results"][0]["record"]["id"] in {c["id"] for c in theirs_seen}

    async def test_a_reviewers_decision_and_note_reach_the_phone(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        record = (await _push(api, staff, batch(op(payload(keys, truck_number="SB-1")))))[
            "results"
        ][0]["record"]
        _, cursor = await self._mine(api, login)

        sent_back = await api.post(
            f"/deliveries/{record['id']}/request-correction",
            headers=await login(MANAGER),
            json={"comments": "Wrong truck number"},
        )
        assert sent_back.status_code == 200, sent_back.text
        changes, cursor = await self._mine(api, login, since=cursor)
        [row] = [c for c in changes if c["id"] == record["id"]]
        assert (
            row["data"]["status"] == "CORRECTION_REQUESTED" and row["data"]["can_correct"] is True
        )
        assert row["data"]["review"]["action"] == "REQUEST_CORRECTION"
        assert row["data"]["review"]["comments"] == "Wrong truck number"
        assert row["data"]["review"]["reviewer_name"]

        # Nothing new since: an unchanged entry is not sent again.
        assert (await self._mine(api, login, since=cursor))[0] == []

        approved = await api.post(
            f"/deliveries/{record['id']}/approve",
            headers=await login(MANAGER),
            json={"comments": "Checked at the gate"},
        )
        # (a corrected entry would normally come first; approving directly is also allowed)
        assert approved.status_code in (200, 422)
        if approved.status_code == 200:
            changes, _ = await self._mine(api, login, since=cursor)
            assert [c["data"]["status"] for c in changes if c["id"] == record["id"]] == ["APPROVED"]
