"""The material-delivery dashboard (`GET /deliveries/summary`, docs/08 §5).

Seeded people: staff.gvh1 (GVH-S1, no right to see prices), sm.gvh1, pm.gvh
(project manager of GVH, sees prices), pm.rsd (another project), admin.

Every test starts from the seeded database, so the totals are exact.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.types import utcnow
from tests.integration.test_deliveries import (
    ADMIN,
    PROCUREMENT,
    _approved_order,
    _keys,
    _rate,
    _record,
)

pytestmark = pytest.mark.integration

STAFF = "staff.gvh1@krb.example"
MANAGER = "sm.gvh1@krb.example"
PM = "pm.gvh@krb.example"
OTHER_PM = "pm.rsd@krb.example"


async def _summary(api: AsyncClient, login: Any, who: str, **params: Any) -> dict[str, Any]:
    response = await api.get("/deliveries/summary", headers=await login(who), params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def _priced_load(
    api: AsyncClient, login: Any, keys: dict[str, str], quantity: str = "12.5", **extra: Any
) -> dict[str, Any]:
    """A load with an order and a rate of 100 a tonne: it goes straight through, priced."""
    po = await _approved_order(api, login, keys, "200")
    return await _record(
        api, await login(STAFF), keys, quantity, purchase_order_id=po["id"], **extra
    )


class TestTheDay:
    async def test_an_empty_day_is_zeros_not_an_error(self, api: AsyncClient, login: Any) -> None:
        s = await _summary(api, login, PM)
        assert s["deliveries"] == 0 and s["by_status"] == {} and s["open_flags"] == 0
        assert Decimal(s["tonnage"]) == 0 and s["quantities"] == []
        assert s["by_day"] == [] and s["top_vendors"] == [] and s["waiting"] == []
        assert s["from_date"] == s["to_date"]  # defaults to today

    async def test_totals_quantities_and_value_of_todays_loads(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        await _priced_load(api, login, keys, "12.5", truck_number="AAA-1")
        await _priced_load(api, login, keys, "7.5", truck_number="BBB-2")

        s = await _summary(api, login, PM)
        assert s["deliveries"] == 2 and s["by_status"] == {"SUBMITTED": 2}
        assert Decimal(s["tonnage"]) == Decimal(20)
        assert [(q["unit_code"], Decimal(q["quantity"])) for q in s["quantities"]] == [
            ("TON", Decimal(20))
        ]
        assert Decimal(s["value"]) == Decimal(2000) and s["values_hidden"] is False

        [point] = s["by_day"]
        assert point["deliveries"] == 2 and Decimal(point["tonnage"]) == Decimal(20)
        assert Decimal(point["value"]) == Decimal(2000)
        [material] = s["top_materials"]
        assert material["sublabel"] == "AGG-CRUSH-12" and material["deliveries"] == 2
        assert Decimal(material["quantities"][0]["quantity"]) == Decimal(20)
        [vendor] = s["top_vendors"]
        assert vendor["deliveries"] == 2 and Decimal(vendor["value"]) == Decimal(2000)
        [site] = s["by_site"]
        assert site["label"] == "GVH-S1" and site["deliveries"] == 2

    async def test_a_load_with_no_price_yet_still_counts_toward_the_quantities(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        # No rate exists, so nothing is converted or priced: it is still six tonnes on site.
        await _record(api, await login(STAFF), keys, "6", truck_number="NORATE-1")
        s = await _summary(api, login, PM)
        assert s["deliveries"] == 1 and Decimal(s["tonnage"]) == Decimal(6)
        assert Decimal(s["quantities"][0]["quantity"]) == Decimal(6)
        assert s["value"] is None or Decimal(s["value"]) == 0
        assert Decimal(s["by_site"][0]["quantities"][0]["quantity"]) == Decimal(6)

    async def test_someone_who_may_not_see_prices_gets_the_loads_but_no_values(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        await _priced_load(api, login, keys)
        s = await _summary(api, login, STAFF)  # site staff hold no rates.view
        assert s["deliveries"] == 1 and Decimal(s["tonnage"]) == Decimal("12.5")
        assert s["value"] is None and s["values_hidden"] is True
        assert s["by_day"][0]["value"] is None
        assert all(v["value"] is None for v in s["top_vendors"] + s["top_materials"])

    async def test_a_rejected_load_is_counted_but_adds_no_quantity_or_value(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        await _priced_load(api, login, keys, "10", truck_number="OK-1")
        bad = await _priced_load(api, login, keys, "5", truck_number="BAD-2")
        rejected = await api.post(
            f"/deliveries/{bad['id']}/reject",
            headers=await login(MANAGER),
            json={"comments": "Not our material"},
        )
        assert rejected.status_code == 200, rejected.text

        s = await _summary(api, login, PM)
        assert s["deliveries"] == 2
        assert s["by_status"] == {"SUBMITTED": 1, "REJECTED": 1}
        assert Decimal(s["tonnage"]) == Decimal(10) and Decimal(s["value"]) == Decimal(1000)

    async def test_flagged_loads_wait_in_the_list_oldest_first_and_are_counted(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        # Minutes, not hours: "today" is the company's day, so a load captured hours ago can
        # belong to yesterday when this runs just after midnight in Karachi.
        older = await _record(api, staff, keys, truck_number="OLD-1", captured_at=_ago(minutes=9))
        newer = await _record(api, staff, keys, truck_number="NEW-2", captured_at=_ago(minutes=4))
        s = await _summary(api, login, PM)
        assert s["open_flags"] == 2 and s["waiting_total"] == 2
        assert [w["id"] for w in s["waiting"]] == [older["id"], newer["id"]]
        first = s["waiting"][0]
        assert first["delivery_number"] == older["delivery_number"]
        assert first["site_code"] == "GVH-S1" and first["flag_count"] >= 1

    async def test_other_projects_and_nobody_else_see_none_of_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        await _priced_load(api, login, keys)
        elsewhere = await _summary(api, login, OTHER_PM)
        assert elsewhere["deliveries"] == 0 and elsewhere["by_site"] == []
        everything = await _summary(api, login, ADMIN)
        assert everything["deliveries"] == 1


class TestThePeriod:
    async def test_a_period_spans_days_and_excludes_the_days_outside_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        staff = await login(STAFF)
        await _record(api, staff, keys, truck_number="D0", captured_at=_ago(minutes=4))
        await _record(api, staff, keys, truck_number="D3", captured_at=_ago(days=3))
        today = utcnow().date()
        week = await _summary(
            api,
            login,
            PM,
            from_date=(today - timedelta(days=7)).isoformat(),
            to_date=(today + timedelta(days=1)).isoformat(),
        )
        assert week["deliveries"] == 2 and len(week["by_day"]) == 2
        assert [p["day"] for p in week["by_day"]] == sorted(p["day"] for p in week["by_day"])
        quiet = await _summary(
            api,
            login,
            PM,
            from_date=(today - timedelta(days=30)).isoformat(),
            to_date=(today - timedelta(days=20)).isoformat(),
        )
        assert quiet["deliveries"] == 0

    async def test_a_period_must_run_forwards_and_stay_within_reason(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(PM)
        backwards = await api.get(
            "/deliveries/summary",
            headers=headers,
            params={"from_date": "2026-03-10", "to_date": "2026-03-01"},
        )
        assert backwards.status_code == 422
        too_long = await api.get(
            "/deliveries/summary",
            headers=headers,
            params={"from_date": "2026-01-01", "to_date": "2026-06-30"},
        )
        assert too_long.status_code == 422

    async def test_it_needs_the_right_to_see_deliveries(self, api: AsyncClient, login: Any) -> None:
        assert (await api.get("/deliveries/summary")).status_code == 401
        finance = await api.get("/deliveries/summary", headers=await login("hr@krb.example"))
        assert finance.status_code == 403


def _ago(**delta: float) -> str:
    return (utcnow() - timedelta(**delta)).isoformat()
