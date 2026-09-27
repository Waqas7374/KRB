"""Telling people about deliveries (docs/02 §26): reviewers when one needs a
look, the person who captured it when head office decides.

Seeded people: staff.gvh1 (captures at GVH-S1), sm.gvh1 (site manager, may
review at GVH-S1), pm.gvh (project manager of GVH, may review), admin (super
administrator: a global grant that covers everything and must NOT be told of
every delivery), auditor.

The outbox drain is pointed at the test's own transaction, as in
test_outbox_worker.py, so the events a request queues are delivered here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.workers.tasks import outbox
from tests.integration.test_deliveries import ADMIN, _approved_order, _keys, _rate, _record

pytestmark = pytest.mark.integration

STAFF = "staff.gvh1@krb.example"
MANAGER = "sm.gvh1@krb.example"
PM = "pm.gvh@krb.example"
OTHER_PM = "pm.rsd@krb.example"
PROCUREMENT = "procurement@krb.example"


@pytest.fixture(autouse=True)
def _outbox_uses_the_test_session(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def _test_factory() -> Any:
        yield db

    async def _noop_dispose() -> None:
        return None

    monkeypatch.setattr(outbox, "SessionFactory", _test_factory)
    monkeypatch.setattr(outbox, "dispose_engine", _noop_dispose)


async def _inbox(
    api: AsyncClient, login: Any, who: str, kind: str | None = None
) -> list[dict[str, Any]]:
    body = (await api.get("/notifications", headers=await login(who), params={"limit": 100})).json()
    items: list[dict[str, Any]] = body["items"]
    return [i for i in items if kind is None or i["notification_type"] == kind]


class TestReviewersAreToldWhenADeliveryNeedsALook:
    async def test_the_people_who_may_review_it_hear_of_it_and_no_one_else(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        delivery = await _record(api, await login(STAFF), keys)  # no order: under review
        assert delivery["status"] == "UNDER_REVIEW"
        await outbox._drain_once()

        for reviewer in (MANAGER, PM):
            [note] = [
                n
                for n in await _inbox(api, login, reviewer, "DELIVERY_REQUIRES_REVIEW")
                if n["link_path"] == f"/deliveries/{delivery['id']}"
            ]
            assert delivery["delivery_number"] in note["title"]
            assert "GVH-S1" in note["title"]
            assert note["body"]  # says what the flag is, not just that there is one
        # Not the person who captured it, not another project, not the super administrator.
        for outsider in (STAFF, OTHER_PM, ADMIN):
            heard = [
                n
                for n in await _inbox(api, login, outsider, "DELIVERY_REQUIRES_REVIEW")
                if n["link_path"] == f"/deliveries/{delivery['id']}"
            ]
            assert heard == [], outsider

    async def test_a_critical_flag_is_urgent_news(self, api: AsyncClient, login: Any) -> None:
        keys = await _keys(api, await login(ADMIN))
        # About a kilometre off the site: a critical geofence flag.
        delivery = await _record(api, await login(STAFF), keys, latitude=31.411 + 0.009)
        await outbox._drain_once()
        [note] = [
            n
            for n in await _inbox(api, login, MANAGER, "DELIVERY_REQUIRES_REVIEW")
            if n["link_path"] == f"/deliveries/{delivery['id']}"
        ]
        assert note["priority"] == "HIGH"

    async def test_a_delivery_with_nothing_wrong_tells_nobody(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "100")
        clean = await _record(api, await login(STAFF), keys, purchase_order_id=po["id"])
        assert clean["status"] == "SUBMITTED"
        await outbox._drain_once()
        for who in (MANAGER, PM):
            assert [
                n
                for n in await _inbox(api, login, who, "DELIVERY_REQUIRES_REVIEW")
                if n["link_path"] == f"/deliveries/{clean['id']}"
            ] == []

    async def test_a_corrected_entry_that_still_needs_a_look_is_announced_again(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        delivery = await _record(api, staff, keys)
        await api.post(
            f"/deliveries/{delivery['id']}/request-correction",
            headers=await login(MANAGER),
            json={"comments": "Please confirm the truck number"},
        )
        corrected = await api.put(
            f"/deliveries/{delivery['id']}",
            headers=staff,
            json={
                "site_id": keys["s1"],
                "vendor_id": keys["vendor"],
                "truck_number": "FIX-1",
                "captured_at": delivery["captured_at"],
                "latitude": 31.411,
                "longitude": 74.2461,
                "items": [
                    {"material_id": keys["crush"], "unit_id": keys["ton"], "quantity": "12.5"}
                ],
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["status"] == "UNDER_REVIEW"  # still no order
        await outbox._drain_once()
        titles = [
            n["title"]
            for n in await _inbox(api, login, MANAGER, "DELIVERY_REQUIRES_REVIEW")
            if n["link_path"] == f"/deliveries/{delivery['id']}"
        ]
        assert any("corrected" in t for t in titles)


class TestTheCapturerIsToldWhatWasDecided:
    async def _decide(
        self, api: AsyncClient, login: Any, delivery: dict[str, Any], verb: str, comments: str
    ) -> None:
        response = await api.post(
            f"/deliveries/{delivery['id']}/{verb}",
            headers=await login(MANAGER),
            json={"comments": comments},
        )
        assert response.status_code == 200, response.text
        await outbox._drain_once()

    async def test_approval_rejection_and_a_request_for_correction_each_reach_them(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)

        approved = await _record(api, staff, keys, truck_number="AAA-1")
        await self._decide(api, login, approved, "approve", "Checked at the gate, fine")
        rejected = await _record(api, staff, keys, truck_number="BBB-2")
        await self._decide(api, login, rejected, "reject", "Not our material")
        sent_back = await _record(api, staff, keys, truck_number="CCC-3")
        await self._decide(api, login, sent_back, "request-correction", "Wrong truck number")

        by_link = {
            n["link_path"]: n
            for n in await _inbox(api, login, STAFF)
            if n["notification_type"].startswith("DELIVERY_")
            and n["notification_type"] != "DELIVERY_REQUIRES_REVIEW"
        }
        a, r, c = (by_link[f"/deliveries/{d['id']}"] for d in (approved, rejected, sent_back))
        assert a["notification_type"] == "DELIVERY_APPROVED"
        assert "Checked at the gate" in a["body"]
        assert r["notification_type"] == "DELIVERY_REJECTED" and r["priority"] == "HIGH"
        assert "Not our material" in r["body"]
        assert c["notification_type"] == "DELIVERY_CORRECTION_REQUESTED"
        assert "Wrong truck number" in c["body"]

    async def test_deciding_your_own_entry_does_not_notify_you(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        mine = await _record(api, await login(MANAGER), keys, truck_number="MINE-1")
        approved = await api.post(
            f"/deliveries/{mine['id']}/approve",
            headers=await login(MANAGER),
            json={"comments": "Recorded and checked by me"},
        )
        assert approved.status_code == 200, approved.text
        await outbox._drain_once()
        assert [
            n
            for n in await _inbox(api, login, MANAGER, "DELIVERY_APPROVED")
            if n["link_path"] == f"/deliveries/{mine['id']}"
        ] == []
