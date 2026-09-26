"""Goods received notes and the stock ledger (docs/02 §6-7).

Seeded people: staff.gvh1 (captures deliveries), sm.gvh1 (approves deliveries,
raises GRNs), pm.gvh (posts GRNs, sees valuation), admin (cancels GRNs),
procurement (sets rates), auditor.

Crush is counted and stocked in TON; GVH-S1's default receiving warehouse is
GVH-S1-YARD.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import system_access_context
from app.core.types import utcnow
from app.modules.inventory.domain.enums import TxnType
from app.modules.inventory.services import ledger, reconcile
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
AUDITOR = "auditor@krb.example"


async def _approved_delivery(
    api: AsyncClient,
    login: Any,
    keys: dict[str, str],
    quantity: str = "12.5",
    *,
    po: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A delivery captured by site staff and approved by the site manager."""
    if po is not None:
        extra["purchase_order_id"] = po["id"]
    delivery = await _record(api, await login(STAFF), keys, quantity, **extra)
    approved = await api.post(
        f"/deliveries/{delivery['id']}/approve",
        headers=await login(MANAGER),
        json={"comments": "Checked on site; fine to receive"},
    )
    assert approved.status_code == 200, approved.text
    return approved.json()  # type: ignore[no-any-return]


async def _grn(
    api: AsyncClient, login: Any, delivery: dict[str, Any], **body: Any
) -> dict[str, Any]:
    made = await api.post(
        f"/deliveries/{delivery['id']}/convert-to-grn", headers=await login(MANAGER), json=body
    )
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


async def _as_pm(api: AsyncClient, login: Any, grn: dict[str, Any]) -> dict[str, Any]:
    """The GRN as someone who may see valuation (the site manager may not)."""
    return (await api.get(f"/grns/{grn['id']}", headers=await login(PM))).json()  # type: ignore[no-any-return]


async def _post(api: AsyncClient, login: Any, grn: dict[str, Any]) -> dict[str, Any]:
    posted = await api.post(f"/grns/{grn['id']}/approve", headers=await login(PM), json={})
    assert posted.status_code == 200, posted.text
    return posted.json()  # type: ignore[no-any-return]


async def _balance(
    api: AsyncClient, login: Any, keys: dict[str, str], who: str = PM
) -> dict[str, Any] | None:
    rows = (
        await api.get(
            "/inventory/balances",
            headers=await login(who),
            params={"material_id": keys["crush"], "limit": 50},
        )
    ).json()["items"]
    return next((r for r in rows if r["warehouse_code"] == "GVH-S1-YARD"), None)


async def _ledger(api: AsyncClient, login: Any, keys: dict[str, str]) -> list[dict[str, Any]]:
    return (  # type: ignore[no-any-return]
        await api.get(
            "/inventory/ledger",
            headers=await login(PM),
            params={"material_id": keys["crush"], "limit": 50, "sort": "posted_at"},
        )
    ).json()["items"]


class TestRaise:
    async def test_a_grn_is_drafted_from_an_approved_delivery_and_links_back(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        delivery = await _approved_delivery(api, login, keys)
        grn = await _grn(api, login, delivery)
        assert grn["items"][0]["amount"] is None  # the site manager may not see valuation
        assert grn["can_post"] is False  # site managers raise GRNs; they do not post them
        grn = await _as_pm(api, login, grn)

        assert grn["status"] == "DRAFT" and grn["grn_number"].startswith("GRN-")
        assert grn["delivery_number"] == delivery["delivery_number"]
        assert grn["warehouse_code"] == "GVH-S1-YARD"  # the site's default receiving warehouse
        [line] = grn["items"]
        assert Decimal(line["delivered_quantity"]) == Decimal("12.5")
        assert Decimal(line["accepted_quantity"]) == Decimal("12.5")  # all taken until inspected
        assert Decimal(line["rate"]) == Decimal(100) and Decimal(line["amount"]) == Decimal(1250)
        linked = (
            await api.get(f"/deliveries/{delivery['id']}", headers=await login(MANAGER))
        ).json()
        assert linked["grn_id"] == grn["id"]
        # Drafting moves no stock.
        assert await _balance(api, login, keys) is None

    async def test_only_an_approved_and_unreceived_delivery_can_be_received(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        unreviewed = await _record(api, await login(STAFF), keys)  # no order: under review
        refused = await api.post(
            f"/deliveries/{unreviewed['id']}/convert-to-grn", headers=await login(MANAGER), json={}
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "delivery_not_approved"

        delivery = await _approved_delivery(api, login, keys, truck_number="B")
        await _grn(api, login, delivery)
        again = await api.post(
            f"/deliveries/{delivery['id']}/convert-to-grn", headers=await login(MANAGER), json={}
        )
        assert again.status_code == 422 and again.json()["rule"] == "delivery_already_received"

    async def test_the_warehouse_must_belong_to_the_deliverys_site(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        delivery = await _approved_delivery(api, login, keys)
        warehouses = (
            await api.get("/warehouses", headers=await login(ADMIN), params={"limit": 50})
        ).json()["items"]
        elsewhere = next(w for w in warehouses if w["code"] == "GVH-S2-YARD")
        refused = await api.post(
            f"/deliveries/{delivery['id']}/convert-to-grn",
            headers=await login(MANAGER),
            json={"warehouse_id": elsewhere["id"]},
        )
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "warehouse_id"

    async def test_site_staff_cannot_raise_a_grn_and_a_site_manager_cannot_post_one(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        delivery = await _approved_delivery(api, login, keys)
        assert (
            await api.post(
                f"/deliveries/{delivery['id']}/convert-to-grn", headers=await login(STAFF), json={}
            )
        ).status_code == 403
        grn = await _grn(api, login, delivery)
        assert (
            await api.post(f"/grns/{grn['id']}/approve", headers=await login(MANAGER), json={})
        ).status_code == 403


class TestPosting:
    async def test_posting_moves_stock_at_the_priced_cost_and_receives_the_order(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "100")
        delivery = await _approved_delivery(api, login, keys, "12.5", po=po)
        posted = await _post(api, login, await _grn(api, login, delivery))

        assert posted["status"] == "POSTED" and posted["inspection_result"] == "PASSED"
        assert posted["posted_at"] and Decimal(posted["net_amount"]) == Decimal(1250)
        [line] = posted["items"]
        assert Decimal(line["base_quantity"]) == Decimal("12.5") and line["base_unit_code"] == "TON"
        assert Decimal(line["unit_cost"]) == Decimal(100) and line["inventory_txn_id"]

        balance = await _balance(api, login, keys)
        assert balance is not None
        assert Decimal(balance["quantity_on_hand"]) == Decimal("12.5")
        assert Decimal(balance["average_cost"]) == Decimal(100)
        assert Decimal(balance["total_value"]) == Decimal(1250)
        [row] = await _ledger(api, login, keys)
        assert row["txn_type"] == "GRN_IN" and Decimal(row["quantity_in"]) == Decimal("12.5")
        assert Decimal(row["balance_quantity_after"]) == Decimal("12.5")

        # The delivery is received, and the order line knows what arrived.
        after = (
            await api.get(f"/deliveries/{delivery['id']}", headers=await login(MANAGER))
        ).json()
        assert after["status"] == "RECEIVED"
        order = (await api.get(f"/purchase-orders/{po['id']}", headers=await login(ADMIN))).json()
        item = order["items"][0]
        assert Decimal(item["received_quantity"]) == Decimal("12.5")
        assert Decimal(item["accepted_quantity"]) == Decimal("12.5")
        assert order["status"] == "PARTIALLY_RECEIVED"

    async def test_a_second_receipt_blends_into_the_weighted_average(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        buyer = await login(PROCUREMENT)
        await _rate(api, buyer, keys, "100")
        await _rate(api, buyer, keys, "200", vendor_id=keys["vendor2"])
        first = await _approved_delivery(api, login, keys, "10", truck_number="A")
        second = await _approved_delivery(
            api, login, keys, "10", vendor_id=keys["vendor2"], truck_number="B"
        )
        await _post(api, login, await _grn(api, login, first))
        await _post(api, login, await _grn(api, login, second))
        balance = await _balance(api, login, keys)
        assert balance is not None
        assert Decimal(balance["quantity_on_hand"]) == Decimal(20)
        assert Decimal(balance["average_cost"]) == Decimal(150)
        assert Decimal(balance["total_value"]) == Decimal(3000)
        rows = await _ledger(api, login, keys)
        assert [Decimal(r["balance_quantity_after"]) for r in rows] == [Decimal(10), Decimal(20)]

    async def test_a_receipt_that_completes_the_order_marks_it_received(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "10")
        delivery = await _approved_delivery(api, login, keys, "10", po=po)
        await _post(api, login, await _grn(api, login, delivery))
        order = (await api.get(f"/purchase-orders/{po['id']}", headers=await login(ADMIN))).json()
        assert order["status"] == "RECEIVED"

    async def test_an_unpriced_line_cannot_be_posted_until_it_is_priced(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        delivery = await _approved_delivery(api, login, keys, "10")  # no rate exists yet
        grn = await _grn(api, login, delivery)
        assert grn["has_unpriced_lines"] is True
        assert (await _as_pm(api, login, grn))["items"][0]["amount"] is None

        refused = await api.post(f"/grns/{grn['id']}/approve", headers=await login(PM), json={})
        assert refused.status_code == 422 and refused.json()["rule"] == "grn_unpriced"
        assert await _balance(api, login, keys) is None  # nothing went on the shelf at nothing

        # A rate is set, and the load — captured earlier — is priced as of its capture date.
        await _rate(api, await login(PROCUREMENT), keys, "80")
        priced = await api.post(f"/grns/{grn['id']}/reprice", headers=await login(MANAGER))
        assert priced.status_code == 200, priced.text
        assert Decimal((await _as_pm(api, login, grn))["items"][0]["amount"]) == Decimal(800)
        # The delivery line itself was back-filled: the two never disagree.
        stored = (await api.get(f"/deliveries/{delivery['id']}", headers=await login(PM))).json()
        assert Decimal(stored["items"][0]["amount"]) == Decimal(800)

        await _post(api, login, grn)
        balance = await _balance(api, login, keys)
        assert balance is not None and Decimal(balance["average_cost"]) == Decimal(80)

    async def test_a_posted_grn_cannot_be_posted_or_inspected_again(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        grn = await _grn(api, login, await _approved_delivery(api, login, keys))
        posted = await _post(api, login, grn)
        again = await api.post(f"/grns/{grn['id']}/approve", headers=await login(PM), json={})
        assert again.status_code == 422 and again.json()["rule"] == "grn_not_draft"
        inspect = await api.patch(
            f"/grns/{grn['id']}/inspection",
            headers=await login(MANAGER),
            json={"lines": [{"grn_item_id": posted["items"][0]["id"], "accepted_quantity": "1"}]},
        )
        assert inspect.status_code == 422 and inspect.json()["rule"] == "grn_not_draft"


class TestInspection:
    async def test_only_the_accepted_part_goes_into_stock_and_the_rest_needs_a_reason(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "100")
        delivery = await _approved_delivery(api, login, keys, "12.5", po=po)
        grn = await _grn(api, login, delivery)
        line_id = grn["items"][0]["id"]
        manager = await login(MANAGER)

        no_reason = await api.patch(
            f"/grns/{grn['id']}/inspection",
            headers=manager,
            json={"lines": [{"grn_item_id": line_id, "accepted_quantity": "10"}]},
        )
        assert no_reason.status_code == 422
        assert no_reason.json()["errors"][0]["field"] == "lines.0.rejection_reason"
        too_much = await api.patch(
            f"/grns/{grn['id']}/inspection",
            headers=manager,
            json={"lines": [{"grn_item_id": line_id, "accepted_quantity": "13"}]},
        )
        assert too_much.status_code == 422

        inspected = await api.patch(
            f"/grns/{grn['id']}/inspection",
            headers=manager,
            json={
                "lines": [
                    {
                        "grn_item_id": line_id,
                        "accepted_quantity": "10",
                        "rejection_reason": "Contaminated with clay",
                        "batch_no": "B-77",
                    }
                ],
                "remarks": "Rejected 2.5 t at the gate",
            },
        )
        assert inspected.status_code == 200, inspected.text
        body = await _as_pm(api, login, inspected.json())
        assert body["inspection_result"] == "PARTIAL"
        assert Decimal(body["items"][0]["rejected_quantity"]) == Decimal("2.5")
        # Priced by what is accepted: 10 of the 12.5 the load was counted at.
        assert Decimal(body["items"][0]["amount"]) == Decimal(1000)

        await _post(api, login, body)
        balance = await _balance(api, login, keys)
        assert balance is not None and Decimal(balance["quantity_on_hand"]) == Decimal(10)
        assert Decimal(balance["total_value"]) == Decimal(1000)
        after = (await api.get(f"/deliveries/{delivery['id']}", headers=manager)).json()
        assert after["status"] == "PARTIALLY_RECEIVED"
        order = (await api.get(f"/purchase-orders/{po['id']}", headers=await login(ADMIN))).json()
        item = order["items"][0]
        assert Decimal(item["received_quantity"]) == Decimal("12.5")  # what arrived
        assert Decimal(item["accepted_quantity"]) == Decimal(10)  # what was taken

    async def test_rejecting_everything_puts_nothing_in_stock(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        grn = await _grn(api, login, await _approved_delivery(api, login, keys, "5"))
        inspected = await api.patch(
            f"/grns/{grn['id']}/inspection",
            headers=await login(MANAGER),
            json={
                "lines": [
                    {
                        "grn_item_id": grn["items"][0]["id"],
                        "accepted_quantity": "0",
                        "rejection_reason": "Wrong grade",
                    }
                ]
            },
        )
        assert inspected.json()["inspection_result"] == "FAILED"
        await _post(api, login, inspected.json())
        assert await _balance(api, login, keys) is None
        assert await _ledger(api, login, keys) == []


class TestCancel:
    async def test_cancelling_a_posted_grn_reverses_stock_and_returns_the_delivery(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "100")
        delivery = await _approved_delivery(api, login, keys, "12.5", po=po)
        grn = await _grn(api, login, delivery)
        await _post(api, login, grn)

        admin = await login(ADMIN)
        assert (
            await api.post(f"/grns/{grn['id']}/cancel", headers=admin, json={"reason": "no"})
        ).status_code == 422
        cancelled = await api.post(
            f"/grns/{grn['id']}/cancel",
            headers=admin,
            json={"reason": "Posted against the wrong load"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"

        # Nothing was deleted: the ledger shows the receipt and its reversal.
        rows = await _ledger(api, login, keys)
        assert [r["txn_type"] for r in rows] == ["GRN_IN", "REVERSAL_OUT"]
        assert rows[1]["reversal_of_id"] == rows[0]["id"]
        assert Decimal(rows[1]["balance_quantity_after"]) == 0
        balance = await _balance(api, login, keys)
        assert balance is not None and Decimal(balance["quantity_on_hand"]) == 0

        # The order line and the delivery are as they were.
        order = (await api.get(f"/purchase-orders/{po['id']}", headers=admin)).json()
        assert Decimal(order["items"][0]["received_quantity"]) == 0
        assert order["status"] in ("APPROVED", "SENT", "ACKNOWLEDGED")
        back = (await api.get(f"/deliveries/{delivery['id']}", headers=admin)).json()
        assert back["status"] == "APPROVED" and back["grn_id"] is None
        # ...so it can be received again.
        assert (await _grn(api, login, delivery))["status"] == "DRAFT"

    async def test_a_grn_cannot_be_cancelled_once_its_stock_has_been_used(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        grn = await _grn(api, login, await _approved_delivery(api, login, keys, "10"))
        posted = await _post(api, login, grn)

        balance = await _balance(api, login, keys)
        assert balance is not None
        ctx = system_access_context(UUID(int=0), UUID(int=0))
        company = (
            await db.execute(text("SELECT company_id FROM grns WHERE id = :id"), {"id": grn["id"]})
        ).scalar_one()
        ctx = system_access_context(company, UUID(int=0))
        await ledger.post(
            db,
            ctx,
            ledger.MovementRequest(
                txn_type=TxnType.ISSUE_OUT,
                warehouse_id=UUID(balance["warehouse_id"]),
                material_id=UUID(keys["crush"]),
                quantity=Decimal(7),
                source_type="TEST",
                source_id=UUID(int=1),
            ),
        )
        refused = await api.post(
            f"/grns/{grn['id']}/cancel", headers=await login(ADMIN), json={"reason": "Wrong load"}
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "insufficient_stock"
        # Nothing changed: still posted, stock still what the issue left.
        assert (await api.get(f"/grns/{grn['id']}", headers=await login(PM))).json()[
            "status"
        ] == "POSTED"
        now = await _balance(api, login, keys)
        assert now is not None and Decimal(now["quantity_on_hand"]) == Decimal(3)
        assert posted["status"] == "POSTED"

    async def test_a_draft_can_be_cancelled_and_releases_its_delivery(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        delivery = await _approved_delivery(api, login, keys)
        grn = await _grn(api, login, delivery)
        cancelled = await api.post(
            f"/grns/{grn['id']}/cancel",
            headers=await login(ADMIN),
            json={"reason": "Raised by mistake"},
        )
        assert cancelled.status_code == 200
        assert (await api.get(f"/deliveries/{delivery['id']}", headers=await login(ADMIN))).json()[
            "grn_id"
        ] is None


class TestStockViews:
    async def test_valuation_is_hidden_from_those_who_may_not_see_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        await _post(api, login, await _grn(api, login, await _approved_delivery(api, login, keys)))
        seen = await _balance(api, login, keys, who=MANAGER)  # holds inventory.view only
        assert seen is not None and Decimal(seen["quantity_on_hand"]) == Decimal("12.5")
        assert seen["average_cost"] is None and seen["total_value"] is None
        ledger_rows = (
            await api.get(
                "/inventory/ledger",
                headers=await login(MANAGER),
                params={"material_id": keys["crush"]},
            )
        ).json()["items"]
        assert ledger_rows and ledger_rows[0]["unit_cost"] is None
        assert ledger_rows[0]["value_in"] is None

    async def test_low_stock_uses_a_reorder_rule_scoped_to_the_material(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        await _post(api, login, await _grn(api, login, await _approved_delivery(api, login, keys)))
        admin = await login(ADMIN)
        made = await api.post(
            "/business-rules",
            headers=admin,
            json={
                "rule_type": "REORDER_LEVEL",
                "scope": {"material_id": keys["crush"]},
                "value": {"min_quantity": "50"},
                "effective_from": "2026-01-01",
            },
        )
        assert made.status_code == 201, made.text
        low = (await api.get("/inventory/low-stock", headers=await login(PM))).json()
        mine = next(r for r in low if r["material_id"] == keys["crush"])
        # The rule scoped to the material overrides the level on the material itself.
        assert mine["is_low"] is True and Decimal(mine["reorder_level"]) == Decimal(50)

    async def test_the_auditor_reads_stock_but_cannot_raise_or_post(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        grn = await _grn(api, login, await _approved_delivery(api, login, keys))
        auditor = await login(AUDITOR)
        assert (await api.get(f"/grns/{grn['id']}", headers=auditor)).status_code == 200
        assert (
            await api.post(f"/grns/{grn['id']}/approve", headers=auditor, json={})
        ).status_code == 403


class TestLedgerGuarantees:
    async def _stock(self, api: AsyncClient, login: Any) -> dict[str, str]:
        keys = await _keys(api, await login(ADMIN))
        await _rate(api, await login(PROCUREMENT), keys, "100")
        await _post(api, login, await _grn(api, login, await _approved_delivery(api, login, keys)))
        return keys

    async def test_the_ledger_cannot_be_edited_or_deleted_even_directly(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        await self._stock(api, login)
        for statement in (
            "UPDATE inventory_transactions SET quantity_in = 999",
            "DELETE FROM inventory_transactions",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                async with db.begin_nested():
                    await db.execute(text(statement))

    async def test_a_movement_goes_one_way_and_stock_cannot_go_negative(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        await self._stock(api, login)
        with pytest.raises(IntegrityError, match="quantity_on_hand_non_negative"):
            async with db.begin_nested():
                await db.execute(text("UPDATE inventory_balances SET quantity_on_hand = -1"))

    async def test_the_ledger_refuses_to_issue_more_than_is_there(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        keys = await self._stock(api, login)
        balance = await _balance(api, login, keys)
        assert balance is not None
        company = (await db.execute(text("SELECT company_id FROM warehouses LIMIT 1"))).scalar_one()
        from app.core.errors import BusinessRuleError

        with pytest.raises(BusinessRuleError, match=r"Only 12\.5"):
            await ledger.post(
                db,
                system_access_context(company, UUID(int=0)),
                ledger.MovementRequest(
                    txn_type=TxnType.ISSUE_OUT,
                    warehouse_id=UUID(balance["warehouse_id"]),
                    material_id=UUID(keys["crush"]),
                    quantity=Decimal(13),
                    source_type="TEST",
                    source_id=UUID(int=1),
                ),
            )

    async def test_balances_agree_with_the_ledger_and_drift_is_found(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        await self._stock(api, login)
        assert await reconcile.find_drift(db) == []

        # Someone "fixes" a balance by hand.
        await db.execute(
            text("UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + 5")
        )
        drift = await reconcile.find_drift(db)
        assert len(drift) == 1
        assert drift[0].balance_quantity - drift[0].ledger_quantity == Decimal(5)
        assert "ledger adds up to" in drift[0].detail

        # A ledger with no balance row at all is drift too.
        await db.execute(text("DELETE FROM inventory_balances"))
        orphan = await reconcile.find_drift(db)
        assert len(orphan) == 1 and orphan[0].balance_quantity == 0

    async def test_the_nightly_task_raises_an_alarm_for_the_administrators(
        self, api: AsyncClient, login: Any, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextlib import asynccontextmanager

        from app.workers.tasks import inventory as inventory_tasks
        from app.workers.tasks import outbox as outbox_tasks

        @asynccontextmanager
        async def _factory() -> Any:
            yield db

        async def _noop() -> None:
            return None

        for module in (inventory_tasks, outbox_tasks):
            monkeypatch.setattr(module, "SessionFactory", _factory)
            monkeypatch.setattr(module, "dispose_engine", _noop)
        monkeypatch.setattr(outbox_tasks, "send_email", lambda **kwargs: None)

        await self._stock(api, login)
        await db.execute(text("UPDATE inventory_balances SET total_value = total_value + 100"))
        found = await inventory_tasks._reconcile_once()
        assert len(found) == 1
        await outbox_tasks._drain_once()
        inbox = (await api.get("/notifications", headers=await login(ADMIN))).json()["items"]
        alarms = [n for n in inbox if n["notification_type"] == "INVENTORY_INTEGRITY_ALARM"]
        assert len(alarms) == 1 and alarms[0]["priority"] == "URGENT"
        assert utcnow() is not None
