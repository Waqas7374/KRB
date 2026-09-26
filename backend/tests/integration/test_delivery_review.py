"""Head-office review: the queue, decisions, corrections, flag waivers and
attaching an order afterwards (docs/02 §6, docs/12 Q3).

Seeded people: staff.gvh1 (site staff, GVH-S1 — captures), sm.gvh1 (site
manager — reviews and approves), pm.gvh (project manager — reviews), admin
(the only one who may reopen and waive), auditor.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.test_deliveries import (
    ADMIN,
    NORTH_1KM,
    PROCUREMENT,
    _approved_order,
    _keys,
    _rate,
    _record,
    flags,
    payload,
    stamp,
)

pytestmark = pytest.mark.integration

STAFF = "staff.gvh1@krb.example"
MANAGER = "sm.gvh1@krb.example"
PM = "pm.gvh@krb.example"
AUDITOR = "auditor@krb.example"


async def _flagged(
    api: AsyncClient, login: Any, **extra: Any
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """A delivery captured by site staff that has landed in review (no order)."""
    keys = await _keys(api, await login(ADMIN))
    delivery = await _record(api, await login(STAFF), keys, **extra)
    assert delivery["status"] == "UNDER_REVIEW"
    return delivery, keys, await login(MANAGER)


class TestQueue:
    async def test_the_most_severe_comes_first_then_the_oldest(
        self, api: AsyncClient, login: Any
    ) -> None:
        staff = await login(STAFF)
        keys = await _keys(api, await login(ADMIN))
        older_warning = await _record(
            api, staff, keys, truck_number="A", captured_at=stamp(hours=5)
        )
        newer_warning = await _record(
            api, staff, keys, truck_number="B", captured_at=stamp(hours=1)
        )
        critical = await _record(
            api, staff, keys, truck_number="C", latitude=NORTH_1KM, captured_at=stamp(minutes=30)
        )
        assert flags(critical)["GEOFENCE_MISMATCH"]["severity"] == "CRITICAL"

        manager = await login(MANAGER)
        queue = (await api.get("/deliveries/review-queue", headers=manager)).json()["items"]
        ids = [d["id"] for d in queue]
        ours = [i for i in ids if i in {older_warning["id"], newer_warning["id"], critical["id"]}]
        assert ours == [critical["id"], older_warning["id"], newer_warning["id"]]
        assert queue[ids.index(critical["id"])]["worst_severity"] == "CRITICAL"

    async def test_an_unflagged_delivery_is_not_in_the_queue(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "100")
        clean = await _record(api, await login(STAFF), keys, purchase_order_id=po["id"])
        assert clean["status"] == "SUBMITTED"
        queue = (await api.get("/deliveries/review-queue", headers=await login(MANAGER))).json()
        assert clean["id"] not in {d["id"] for d in queue["items"]}

    async def test_site_staff_cannot_open_the_queue(self, api: AsyncClient, login: Any) -> None:
        assert (
            await api.get("/deliveries/review-queue", headers=await login(STAFF))
        ).status_code == 403


class TestApprove:
    async def test_approving_resolves_warnings_and_records_who_and_when(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, manager = await _flagged(api, login)
        approved = await api.post(
            f"/deliveries/{delivery['id']}/approve",
            headers=manager,
            json={"comments": "Known vendor; order to follow"},
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "APPROVED" and body["approved_at"] and body["approved_by_id"]
        assert body["has_open_flags"] is False
        assert {f["status"] for f in body["flags"] if f["severity"] != "INFO"} == {"ACCEPTED"}
        assert body["reviews"][0]["action"] == "ACCEPT"
        assert body["reviews"][0]["reviewer_name"] == "Imran Shah"
        assert body["reviews"][0]["previous_status"] == "UNDER_REVIEW"
        # Decided: no more decision buttons.
        assert body["can_approve"] is False and body["can_reject"] is False

    async def test_a_critical_flag_cannot_be_accepted_without_a_reason(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, manager = await _flagged(api, login, latitude=NORTH_1KM)
        refused = await api.post(f"/deliveries/{delivery['id']}/approve", headers=manager, json={})
        assert refused.status_code == 422
        assert "critical flag" in refused.json()["detail"]
        ok = await api.post(
            f"/deliveries/{delivery['id']}/approve",
            headers=manager,
            json={
                "comments": "Truck stopped at the vendor's yard first; GPS is right, load is fine"
            },
        )
        assert ok.status_code == 200
        accepted = flags(ok.json())["GEOFENCE_MISMATCH"]
        assert accepted["status"] == "ACCEPTED" and "vendor's yard" in accepted["resolution_note"]

    async def test_a_decided_delivery_cannot_be_decided_again(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, manager = await _flagged(api, login)
        await api.post(f"/deliveries/{delivery['id']}/approve", headers=manager, json={})
        again = await api.post(f"/deliveries/{delivery['id']}/approve", headers=manager, json={})
        assert again.status_code == 422 and again.json()["rule"] == "delivery_not_reviewable"

    async def test_the_reviewer_of_one_site_cannot_touch_anothers(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        keys = await _keys(api, admin)
        elsewhere = await _record(api, admin, keys, site_id=keys["s2"], truck_number="S2")
        refused = await api.post(
            f"/deliveries/{elsewhere['id']}/approve", headers=await login(MANAGER), json={}
        )
        assert refused.status_code == 404

    async def test_site_staff_and_the_auditor_cannot_approve(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, _manager = await _flagged(api, login)
        for who in (STAFF, AUDITOR):
            refused = await api.post(
                f"/deliveries/{delivery['id']}/approve", headers=await login(who), json={}
            )
            assert refused.status_code == 403, who


class TestReject:
    async def test_rejecting_needs_a_reason_and_keeps_the_record(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, manager = await _flagged(api, login)
        blank = await api.post(f"/deliveries/{delivery['id']}/reject", headers=manager, json={})
        assert blank.status_code == 422
        rejected = await api.post(
            f"/deliveries/{delivery['id']}/reject",
            headers=manager,
            json={"comments": "Not our material: wrong grade on the challan"},
        )
        assert rejected.status_code == 200
        body = rejected.json()
        assert body["status"] == "REJECTED" and body["rejection_reason"].startswith("Not our")
        assert body["rejected_at"] and body["has_open_flags"] is False
        assert {f["status"] for f in body["flags"] if f["severity"] != "INFO"} == {"REJECTED"}
        # Still there, readable, with its evidence.
        stored = (await api.get(f"/deliveries/{delivery['id']}", headers=manager)).json()
        assert stored["items"] and stored["captured_at"]


class TestCorrection:
    async def test_the_full_loop_send_back_correct_and_review_again(
        self, api: AsyncClient, login: Any
    ) -> None:
        staff = await login(STAFF)
        keys = await _keys(api, await login(ADMIN))
        delivery = await _record(api, staff, keys, "22")  # overloaded, no order
        assert "TONNAGE_ANOMALY" in flags(delivery)
        manager = await login(MANAGER)

        sent = await api.post(
            f"/deliveries/{delivery['id']}/request-correction",
            headers=manager,
            json={"comments": "22 t on a 16 t truck: please recheck the weighbridge slip"},
        )
        assert sent.status_code == 200 and sent.json()["status"] == "CORRECTION_REQUESTED"
        assert sent.json()["reviews"][0]["comments"].startswith("22 t on")

        # The capturer, and only they, may now correct it.
        mine = (await api.get(f"/deliveries/{delivery['id']}", headers=staff)).json()
        assert mine["can_correct"] is True
        fixed = await api.put(
            f"/deliveries/{delivery['id']}",
            headers=staff,
            json=payload(keys, "15", truck_number="LEB-1234", captured_at=delivery["captured_at"]),
        )
        assert fixed.status_code == 200, fixed.text
        body = fixed.json()
        assert Decimal(body["items"][0]["quantity"]) == Decimal(15)
        assert (
            body["delivery_number"] == delivery["delivery_number"]
        )  # same delivery, not a new one
        # The old flag is kept as history; the corrected entry raised none for tonnage.
        tonnage = [f for f in body["flags"] if f["flag_type"] == "TONNAGE_ANOMALY"]
        assert [f["status"] for f in tonnage] == ["CORRECTED"]
        assert body["status"] == "UNDER_REVIEW"  # still no order: back in the queue
        assert [r["action"] for r in body["reviews"]][:2] == [
            "CORRECTION_SUBMITTED",
            "REQUEST_CORRECTION",
        ]

    async def test_a_correction_is_checked_like_a_new_delivery(
        self, api: AsyncClient, login: Any
    ) -> None:
        staff = await login(STAFF)
        keys = await _keys(api, await login(ADMIN))
        delivery = await _record(api, staff, keys, "10")
        await api.post(
            f"/deliveries/{delivery['id']}/request-correction",
            headers=await login(MANAGER),
            json={"comments": "Please recheck the quantity"},
        )
        worse = await api.put(
            f"/deliveries/{delivery['id']}",
            headers=staff,
            json=payload(keys, "30", captured_at=delivery["captured_at"]),
        )
        assert worse.status_code == 200
        assert flags(worse.json())["TONNAGE_ANOMALY"]["status"] == "OPEN"

    async def test_only_a_delivery_sent_back_can_be_corrected(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, keys, _manager = await _flagged(api, login)
        refused = await api.put(
            f"/deliveries/{delivery['id']}",
            headers=await login(STAFF),
            json=payload(keys, "5", captured_at=delivery["captured_at"]),
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "delivery_not_editable"

    async def test_sending_back_needs_a_note_the_capturer_can_act_on(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, manager = await _flagged(api, login)
        refused = await api.post(
            f"/deliveries/{delivery['id']}/request-correction",
            headers=manager,
            json={"comments": "x"},
        )
        assert refused.status_code == 422


class TestReopenAndWaive:
    async def test_only_an_administrator_can_reopen_and_needs_a_reason(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, manager = await _flagged(api, login)
        await api.post(
            f"/deliveries/{delivery['id']}/reject",
            headers=manager,
            json={"comments": "Wrong grade"},
        )
        assert (
            await api.post(
                f"/deliveries/{delivery['id']}/reopen", headers=manager, json={"comments": "Sorry"}
            )
        ).status_code == 403
        admin = await login(ADMIN)
        assert (
            await api.post(f"/deliveries/{delivery['id']}/reopen", headers=admin, json={})
        ).status_code == 422
        reopened = await api.post(
            f"/deliveries/{delivery['id']}/reopen",
            headers=admin,
            json={"comments": "Vendor produced the correct challan"},
        )
        assert reopened.status_code == 200
        body = reopened.json()
        assert body["status"] == "UNDER_REVIEW" and body["rejection_reason"] is None
        assert [r["action"] for r in body["reviews"]][:2] == ["REOPEN", "REJECT"]

    async def test_waiving_the_last_open_flag_releases_the_delivery_from_review(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "100")
        # Only flag: the load is a kilometre off (order and rate are fine).
        delivery = await _record(
            api, await login(STAFF), keys, purchase_order_id=po["id"], latitude=NORTH_1KM
        )
        open_flags = [f for f in delivery["flags"] if f["status"] == "OPEN"]
        assert [f["flag_type"] for f in open_flags] == ["GEOFENCE_MISMATCH"]
        admin = await login(ADMIN)

        short = await api.post(
            f"/delivery-flags/{open_flags[0]['id']}/waive", headers=admin, json={"note": "ok"}
        )
        assert short.status_code == 422
        waived = await api.post(
            f"/delivery-flags/{open_flags[0]['id']}/waive",
            headers=admin,
            json={"note": "Weighbridge is outside the site fence; known"},
        )
        assert waived.status_code == 200
        body = waived.json()
        assert flags(body)["GEOFENCE_MISMATCH"]["status"] == "WAIVED"
        assert body["status"] == "SUBMITTED" and body["has_open_flags"] is False
        again = await api.post(
            f"/delivery-flags/{open_flags[0]['id']}/waive", headers=admin, json={"note": "twice"}
        )
        assert again.status_code == 422 and again.json()["rule"] == "flag_not_open"

    async def test_a_site_manager_cannot_waive(self, api: AsyncClient, login: Any) -> None:
        delivery, _k, manager = await _flagged(api, login)
        flag = next(f for f in delivery["flags"] if f["status"] == "OPEN")
        refused = await api.post(
            f"/delivery-flags/{flag['id']}/waive", headers=manager, json={"note": "Not a problem"}
        )
        assert refused.status_code == 403


class TestAttachOrder:
    async def test_attaching_an_order_clears_no_po_and_checks_the_balance(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        po = await _approved_order(api, login, keys, "100")
        first = await _record(api, staff, keys, "60", truck_number="A")  # no order
        assert "NO_PO" in flags(first)
        manager = await login(MANAGER)

        attached = await api.post(
            f"/deliveries/{first['id']}/attach-purchase-order",
            headers=manager,
            json={
                "purchase_order_id": po["id"],
                "comments": "Order raised after the truck arrived",
            },
        )
        assert attached.status_code == 200, attached.text
        body = attached.json()
        assert body["purchase_order_number"] == po["po_number"]
        assert flags(body)["NO_PO"]["status"] == "CORRECTED"
        assert "PO_QTY_EXCEEDED" not in flags(body)
        assert body["reviews"][0]["action"] == "ATTACH_PO"

        # A second load, attached, now counts the first: 60 + 45 > 100 + 2 %.
        second = await _record(api, staff, keys, "45", truck_number="B")
        over = await api.post(
            f"/deliveries/{second['id']}/attach-purchase-order",
            headers=manager,
            json={"purchase_order_id": po["id"]},
        )
        assert over.status_code == 200
        assert Decimal(flags(over.json())["PO_QTY_EXCEEDED"]["actual_value"]) == Decimal(105)
        assert over.json()["status"] == "UNDER_REVIEW"  # attaching cannot hide an over-delivery

    async def test_the_order_must_be_the_vendors_and_only_one_can_be_attached(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        po = await _approved_order(api, login, keys, "100")
        manager = await login(MANAGER)
        other_vendor = await _record(
            api, await login(STAFF), keys, vendor_id=keys["vendor2"], truck_number="V2"
        )
        wrong = await api.post(
            f"/deliveries/{other_vendor['id']}/attach-purchase-order",
            headers=manager,
            json={"purchase_order_id": po["id"]},
        )
        assert wrong.status_code == 422 and wrong.json()["rule"] == "purchase_order_vendor"

        mine = await _record(api, await login(STAFF), keys, truck_number="M")
        ok = await api.post(
            f"/deliveries/{mine['id']}/attach-purchase-order",
            headers=manager,
            json={"purchase_order_id": po["id"]},
        )
        assert ok.status_code == 200
        twice = await api.post(
            f"/deliveries/{mine['id']}/attach-purchase-order",
            headers=manager,
            json={"purchase_order_id": po["id"]},
        )
        assert twice.status_code == 422 and twice.json()["rule"] == "delivery_has_order"


class TestCapabilities:
    async def test_the_screen_is_told_what_each_person_may_do(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, manager = await _flagged(api, login)
        as_manager = (await api.get(f"/deliveries/{delivery['id']}", headers=manager)).json()
        assert as_manager["can_approve"] and as_manager["can_reject"]
        assert as_manager["can_request_correction"] and as_manager["can_attach_order"]
        assert as_manager["can_waive"] is False  # not theirs to waive
        as_staff = (
            await api.get(f"/deliveries/{delivery['id']}", headers=await login(STAFF))
        ).json()
        assert not (as_staff["can_approve"] or as_staff["can_reject"] or as_staff["can_correct"])
        as_admin = (
            await api.get(f"/deliveries/{delivery['id']}", headers=await login(ADMIN))
        ).json()
        assert as_admin["can_waive"] is True

    async def test_review_history_is_readable_by_anyone_who_can_see_the_delivery(
        self, api: AsyncClient, login: Any
    ) -> None:
        delivery, _k, manager = await _flagged(api, login)
        await api.post(f"/deliveries/{delivery['id']}/approve", headers=manager, json={})
        history = (
            await api.get(f"/deliveries/{delivery['id']}/reviews", headers=await login(AUDITOR))
        ).json()
        assert [h["action"] for h in history] == ["ACCEPT"]
