"""Vendor rates end to end: proposal, approval, supersession, history and
resolution (docs/05 §4).

Seeded people: procurement (procurement manager, holds rates.*), finance
(finance manager, approves larger changes), auditor (reads, cannot write).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow

pytestmark = pytest.mark.integration

PROCUREMENT = "procurement@krb.example"
FINANCE = "finance@krb.example"
AUDITOR = "auditor@krb.example"
REQUESTER = "sm.gvh1@krb.example"

TODAY = utcnow().date()


def day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


async def _keys(api: AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    vendors = (
        await api.get("/vendors", headers=headers, params={"limit": 50, "status": "ACTIVE"})
    ).json()["items"]
    materials = (await api.get("/materials", headers=headers, params={"limit": 50})).json()["items"]
    material = next(m for m in materials if m["is_purchasable"])
    projects = (await api.get("/projects", headers=headers, params={"q": "GVH"})).json()["items"]
    project = next(p for p in projects if p["code"] == "GVH")
    sites = (await api.get("/sites", headers=headers, params={"project_id": project["id"]})).json()
    site = next(s for s in sites["items"] if s["code"] == "GVH-S1")
    return {
        "vendor_id": vendors[0]["id"],
        "other_vendor_id": vendors[1]["id"],
        "material_id": material["id"],
        "unit_id": material["base_unit_id"],
        "project_id": project["id"],
        "site_id": site["id"],
    }


def body(keys: dict[str, str], rate: str, start: int, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "vendor_id": keys["vendor_id"],
        "material_id": keys["material_id"],
        "unit_id": keys["unit_id"],
        "rate": rate,
        "effective_from": day(start),
        "reason": "Quarterly renegotiation",
    }
    payload.update(extra)
    return payload


async def _propose(
    api: AsyncClient,
    headers: dict[str, str],
    keys: dict[str, str],
    rate: str,
    start: int,
    **extra: Any,
) -> dict[str, Any]:
    response = await api.post(
        "/vendor-rates", headers=headers, json=body(keys, rate, start, **extra)
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def _resolve(
    api: AsyncClient, headers: dict[str, str], keys: dict[str, str], at: int = 0, **extra: str
) -> dict[str, Any] | None:
    response = await api.get(
        "/vendor-rates/resolve",
        headers=headers,
        params={
            "vendor_id": keys["vendor_id"],
            "material_id": keys["material_id"],
            "at": day(at),
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


class TestProposalAndApproval:
    async def test_a_first_rate_approves_itself_and_comes_into_force(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        rate = await _propose(api, buyer, keys, "48", -30)
        assert rate["status"] == "ACTIVE" and rate["is_current"] is True
        assert rate["previous_rate"] is None and rate["scope"] == "Company-wide"
        assert Decimal((await _resolve(api, buyer, keys))["rate"]) == Decimal(48)  # type: ignore[index]

    async def test_a_small_change_approves_itself_and_supersedes_the_old_period(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        first = await _propose(api, buyer, keys, "50", -60)
        second = await _propose(api, buyer, keys, "52", -10, reason="Fuel surcharge")
        assert second["status"] == "ACTIVE"
        assert Decimal(second["change_pct"]) == Decimal("4.00")
        assert Decimal(second["previous_rate"]) == Decimal(50)

        # The old period was closed the day before the new one starts.
        old = (await api.get(f"/vendor-rates/{first['id']}", headers=buyer)).json()
        assert old["effective_to"] == day(-11) and old["is_current"] is False
        # History resolves each date to the rate of the day.
        assert Decimal((await _resolve(api, buyer, keys, at=-30))["rate"]) == Decimal(50)  # type: ignore[index]
        assert Decimal((await _resolve(api, buyer, keys, at=0))["rate"]) == Decimal(52)  # type: ignore[index]
        assert await _resolve(api, buyer, keys, at=-90) is None

    async def test_a_large_change_waits_for_finance_and_takes_effect_only_then(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -60)
        big = await _propose(api, buyer, keys, "130", -5, reason="Cement price spike")
        assert big["status"] == "PENDING_APPROVAL"
        assert Decimal(big["change_pct"]) == Decimal("30.00")

        # Not in force yet: today still resolves to the old rate.
        assert Decimal((await _resolve(api, buyer, keys))["rate"]) == Decimal(100)  # type: ignore[index]

        finance = await login(FINANCE)
        done = await api.post(
            f"/approvals/requests/{big['approval_request_id']}/approve",
            headers=finance,
            json={"comments": "Confirmed against the supplier's notice"},
        )
        assert done.status_code == 200, done.text
        assert Decimal((await _resolve(api, buyer, keys))["rate"]) == Decimal(130)  # type: ignore[index]
        approved = (await api.get(f"/vendor-rates/{big['id']}", headers=buyer)).json()
        assert approved["status"] == "ACTIVE" and approved["approved_at"]

    async def test_a_rejected_change_leaves_the_old_rate_untouched(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -60)
        big = await _propose(api, buyer, keys, "150", -5)
        finance = await login(FINANCE)
        rejected = await api.post(
            f"/approvals/requests/{big['approval_request_id']}/reject",
            headers=finance,
            json={"comments": "Not supported by the quotation"},
        )
        assert rejected.status_code == 200
        assert (await api.get(f"/vendor-rates/{big['id']}", headers=buyer)).json()[
            "status"
        ] == "REJECTED"
        assert Decimal((await _resolve(api, buyer, keys))["rate"]) == Decimal(100)  # type: ignore[index]

    async def test_a_pending_proposal_can_be_withdrawn_and_replaced(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -60)
        big = await _propose(api, buyer, keys, "150", -5)

        # Only one change may be pending per vendor, material and scope.
        again = await api.post("/vendor-rates", headers=buyer, json=body(keys, "160", -4))
        assert again.status_code == 422 and again.json()["rule"] == "rate_change_pending"

        withdrawn = await api.post(f"/vendor-rates/{big['id']}/withdraw", headers=buyer)
        assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "WITHDRAWN"
        assert (
            await api.post("/vendor-rates", headers=buyer, json=body(keys, "160", -4))
        ).status_code == 201


class TestRules:
    async def test_periods_only_move_forward(self, api: AsyncClient, login: Any) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -10)
        for start in (-10, -20):
            refused = await api.post("/vendor-rates", headers=buyer, json=body(keys, "101", start))
            assert refused.status_code == 422, start
            assert refused.json()["rule"] == "rate_period_not_forward"

    async def test_restating_the_same_rate_is_refused(self, api: AsyncClient, login: Any) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -30)
        same = await api.post("/vendor-rates", headers=buyer, json=body(keys, "100", -5))
        assert same.status_code == 422 and same.json()["rule"] == "rate_unchanged"

    async def test_the_unit_must_be_one_the_material_has(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        units = (await api.get("/units", headers=buyer, params={"limit": 100})).json()["items"]
        material = (await api.get(f"/materials/{keys['material_id']}", headers=buyer)).json()
        allowed = {material["base_unit_id"], *(u["unit_id"] for u in material.get("units", []))}
        stranger = next(u for u in units if u["id"] not in allowed)
        refused = await api.post(
            "/vendor-rates", headers=buyer, json=body(keys, "10", -1, unit_id=stranger["id"])
        )
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "unit_id"

    async def test_the_value_of_a_rate_can_never_be_edited(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        rate = await _propose(api, buyer, keys, "100", -30)
        for field in ("rate", "effective_from", "effective_to", "status"):
            refused = await api.patch(
                f"/vendor-rates/{rate['id']}", headers=buyer, json={field: "1"}
            )
            assert refused.status_code == 422, field
        # A note, the one thing that may change, does not disturb the value.
        noted = await api.patch(
            f"/vendor-rates/{rate['id']}", headers=buyer, json={"notes": "Ex-works"}
        )
        assert noted.status_code == 200
        assert noted.json()["notes"] == "Ex-works" and Decimal(noted.json()["rate"]) == Decimal(100)
        assert (
            await api.put(f"/vendor-rates/{rate['id']}", headers=buyer, json={})
        ).status_code == 405

    async def test_an_auditor_can_read_but_not_propose(self, api: AsyncClient, login: Any) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -30)
        auditor = await login(AUDITOR)
        listed = await api.get("/vendor-rates", headers=auditor, params={"current": True})
        assert listed.status_code == 200 and listed.json()["items"]
        assert (
            await api.post("/vendor-rates", headers=auditor, json=body(keys, "1", -1))
        ).status_code == 403

    async def test_a_site_manager_cannot_propose_rates(self, api: AsyncClient, login: Any) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        site = await login(REQUESTER)
        refused = await api.post("/vendor-rates", headers=site, json=body(keys, "1", -1))
        assert refused.status_code == 403


class TestResolutionByScope:
    async def test_site_beats_project_beats_company_and_others_fall_back(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -30)
        await _propose(api, buyer, keys, "110", -30, project_id=keys["project_id"])
        site_rate = await _propose(api, buyer, keys, "120", -30, site_id=keys["site_id"])
        # The project is filled in from the site.
        assert site_rate["project_id"] == keys["project_id"] and site_rate["scope"].startswith(
            "Site"
        )

        at_site = await _resolve(
            api, buyer, keys, project_id=keys["project_id"], site_id=keys["site_id"]
        )
        assert at_site["rate"].startswith("120") and at_site["scope"] == "site"  # type: ignore[index]
        at_project = await _resolve(api, buyer, keys, project_id=keys["project_id"])
        assert at_project["rate"].startswith("110") and at_project["scope"] == "project"  # type: ignore[index]
        anywhere = await _resolve(api, buyer, keys)
        assert anywhere["rate"].startswith("100") and anywhere["scope"] == "company"  # type: ignore[index]

    async def test_each_vendor_has_its_own_rates(self, api: AsyncClient, login: Any) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -30)
        other = {**keys, "vendor_id": keys["other_vendor_id"]}
        assert await _resolve(api, buyer, other) is None


class TestHistory:
    async def test_every_change_is_recorded_with_old_new_who_and_why(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "48", -60, reason="Opening price")
        await _propose(api, buyer, keys, "52", -20, reason="Monsoon freight")
        rows = (
            await api.get(
                "/vendor-rates/history",
                headers=buyer,
                params={"vendor_id": keys["vendor_id"], "material_id": keys["material_id"]},
            )
        ).json()["items"]
        assert [(r["old_rate"], r["new_rate"], r["reason"]) for r in rows] == [
            ("48.000000", "52.000000", "Monsoon freight"),
            (None, "48.000000", "Opening price"),
        ]
        assert rows[0]["changed_by_name"] == "Ahmed Raza"
        assert Decimal(rows[0]["change_pct"]) == Decimal("8.33")

    async def test_history_cannot_be_edited_or_deleted_even_directly(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "48", -60)
        for statement in (
            "UPDATE vendor_rate_history SET new_rate = 1",
            "DELETE FROM vendor_rate_history",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                async with db.begin_nested():
                    await db.execute(text(statement))


class TestDatabaseGuarantees:
    async def test_two_approved_periods_for_one_scope_cannot_overlap(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        rate = await _propose(api, buyer, keys, "48", -60)
        with pytest.raises(IntegrityError, match="ex_vendor_rates_no_overlap"):
            async with db.begin_nested():
                await db.execute(
                    text(
                        """
                        INSERT INTO vendor_rates (id, company_id, vendor_id, material_id, unit_id,
                          rate, currency_code, effective_from, status, source, version)
                        SELECT gen_random_uuid(), company_id, vendor_id, material_id, unit_id, 99,
                          currency_code, effective_from + 5, 'ACTIVE', 'MANUAL', 1
                        FROM vendor_rates WHERE id = :id
                        """
                    ),
                    {"id": rate["id"]},
                )

    async def test_a_pending_or_rejected_period_does_not_block_others(
        self, api: AsyncClient, login: Any
    ) -> None:
        # Only approved periods are constrained: a proposal is not yet a claim on dates.
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -60)
        big = await _propose(api, buyer, keys, "150", -5)
        assert big["status"] == "PENDING_APPROVAL"


class TestTheGrid:
    """The head-office rate screen (§19): what is in force, and the road it took."""

    async def _row(
        self, api: AsyncClient, headers: dict[str, str], keys: dict[str, str]
    ) -> dict[str, Any]:
        page = (
            await api.get(
                "/vendor-rates/grid",
                headers=headers,
                params={"vendor_id": keys["vendor_id"], "material_id": keys["material_id"]},
            )
        ).json()
        [row] = page["items"]
        return row  # type: ignore[no-any-return]

    async def test_a_row_shows_the_current_rate_and_the_periods_that_led_to_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "50", -60)
        await _propose(api, buyer, keys, "52", -10, reason="Fuel surcharge")

        row = await self._row(api, buyer, keys)
        assert Decimal(row["rate"]) == Decimal(52) and row["effective_from"] == day(-10)
        assert Decimal(row["previous_rate"]) == Decimal(50)
        assert Decimal(row["change_pct"]) == Decimal("4.00")
        # Oldest first, for a sparkline.
        assert [(p["effective_from"], Decimal(p["rate"])) for p in row["points"]] == [
            (day(-60), Decimal(50)),
            (day(-10), Decimal(52)),
        ]
        assert row["scope"] == "Company-wide" and row["has_pending"] is False
        assert row["vendor_name"] and row["material_sku"] and row["unit_code"]

    async def test_a_change_waiting_for_approval_is_marked_and_not_yet_the_rate(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "100", -60)
        big = await _propose(api, buyer, keys, "130", -5, reason="Cement price spike")
        assert big["status"] == "PENDING_APPROVAL"

        row = await self._row(api, buyer, keys)
        assert Decimal(row["rate"]) == Decimal(100) and row["has_pending"] is True
        assert [Decimal(p["rate"]) for p in row["points"]] == [Decimal(100)]

    async def test_only_the_current_rate_of_each_scope_is_a_row_and_readers_see_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        buyer = await login(PROCUREMENT)
        keys = await _keys(api, buyer)
        await _propose(api, buyer, keys, "50", -60)
        await _propose(api, buyer, keys, "52", -10)
        mine = (
            await api.get(
                "/vendor-rates/grid", headers=buyer, params={"vendor_id": keys["vendor_id"]}
            )
        ).json()
        assert mine["page"]["total"] == 1  # two periods, one row: what is in force
        auditor = await api.get("/vendor-rates/grid", headers=await login(AUDITOR))
        assert auditor.status_code == 200
        # Nobody without the right to see rates gets the grid.
        staff = await api.get("/vendor-rates/grid", headers=await login("staff.gvh1@krb.example"))
        assert staff.status_code == 403
