"""Stock issues, transfers and adjustments (docs/02 §6-7).

Seeded people: sm.gvh1 (site manager at GVH-S1: issues, transfers, raises
adjustments), pm.gvh (project manager of GVH: sees valuation, signs
adjustments), finance (signs the large ones), store.cs (store manager at the
central store: receives transfers), staff.gvh1 (site staff: cannot move stock),
pm.rsd (another project's manager), admin, auditor.

Crush is stocked in TON and can be counted in CFT (a seeded, material-specific factor). Stock is put
on the shelf directly through the ledger, the way a GRN would.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import system_access_context
from app.modules.inventory.domain.enums import TxnType
from app.modules.inventory.services import ledger, reconcile
from tests.integration.test_deliveries import ADMIN, _keys
from tests.integration.test_finance import _codes

pytestmark = pytest.mark.integration

MANAGER = "sm.gvh1@krb.example"
PM = "pm.gvh@krb.example"
FINANCE = "finance@krb.example"
STORE = "store.cs@krb.example"
STAFF = "staff.gvh1@krb.example"
OTHER_PM = "pm.rsd@krb.example"
AUDITOR = "auditor@krb.example"


class Setup:
    """Ids the tests need, and a way to put stock on a shelf."""

    def __init__(self, keys: dict[str, str], warehouses: dict[str, str], company: UUID) -> None:
        self.keys = keys
        self.wh = warehouses
        self.company = company
        self.crush = UUID(keys["crush"])

    async def stock(
        self, db: AsyncSession, code: str, quantity: str, cost: str, material: UUID | None = None
    ) -> None:
        await ledger.post(
            db,
            system_access_context(self.company, UUID(int=0)),
            ledger.MovementRequest(
                txn_type=TxnType.GRN_IN,
                warehouse_id=UUID(self.wh[code]),
                material_id=material or self.crush,
                quantity=Decimal(quantity),
                unit_cost=Decimal(cost),
                source_type="TEST",
                source_id=uuid4(),
            ),
        )


async def _setup(api: AsyncClient, login: Any, db: AsyncSession) -> Setup:
    admin = await login(ADMIN)
    keys = await _keys(api, admin)
    rows = (await api.get("/warehouses", headers=admin, params={"limit": 50})).json()["items"]
    company = (await db.execute(text("SELECT company_id FROM warehouses LIMIT 1"))).scalar_one()
    units = (await api.get("/units", headers=admin, params={"limit": 100})).json()["items"]
    keys["cft"] = next(u["id"] for u in units if u["code"] == "CFT")
    return Setup(keys, {w["code"]: w["id"] for w in rows}, company)


async def _balance(
    api: AsyncClient, login: Any, code: str, keys: dict[str, str]
) -> dict[str, Any] | None:
    rows = (
        await api.get(
            "/inventory/balances",
            headers=await login(ADMIN),
            params={"material_id": keys["crush"], "limit": 100},
        )
    ).json()["items"]
    return next((r for r in rows if r["warehouse_code"] == code), None)


async def _on_hand(api: AsyncClient, login: Any, code: str, keys: dict[str, str]) -> Decimal:
    row = await _balance(api, login, code, keys)
    return Decimal(row["quantity_on_hand"]) if row else Decimal(0)


def _issue_body(s: Setup, quantity: str = "4", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "warehouse_id": s.wh["GVH-S1-YARD"],
        "issued_to_type": "CONTRACTOR",
        "issued_to_name": "Al-Noor Contractors",
        "purpose": "Road base, block C",
        "lines": [{"material_id": s.keys["crush"], "quantity": quantity, "unit_id": s.keys["ton"]}],
    }
    body.update(extra)
    return body


async def _issue(
    api: AsyncClient, login: Any, s: Setup, quantity: str = "4", **extra: Any
) -> dict[str, Any]:
    made = await api.post(
        "/inventory/issues", headers=await login(MANAGER), json=_issue_body(s, quantity, **extra)
    )
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


async def _issue_post(api: AsyncClient, login: Any, issue: dict[str, Any]) -> dict[str, Any]:
    posted = await api.post(f"/inventory/issues/{issue['id']}/post", headers=await login(MANAGER))
    assert posted.status_code == 200, posted.text
    return posted.json()  # type: ignore[no-any-return]


class TestIssues:
    async def test_a_draft_moves_no_stock_and_posting_takes_it_at_the_average_cost(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        await s.stock(db, "GVH-S1-YARD", "10", "120")  # average 110

        draft = await _issue(api, login, s, "4")
        assert draft["status"] == "DRAFT" and draft["issue_number"].startswith("ISS-")
        assert draft["can_post"] is True
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 20

        posted = await _issue_post(api, login, draft)
        assert posted["status"] == "ISSUED" and posted["can_post"] is False
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 16
        # Charged at the average, which an issue never changes.
        balance = await _balance(api, login, "GVH-S1-YARD", s.keys)
        assert balance is not None and Decimal(balance["average_cost"]) == Decimal(110)

        as_pm = (await api.get(f"/inventory/issues/{draft['id']}", headers=await login(PM))).json()
        assert Decimal(as_pm["items"][0]["unit_cost"]) == Decimal(110)
        assert Decimal(as_pm["total_value"]) == Decimal(440)

    async def test_posting_charges_materials_consumed_and_cancelling_reverses_it(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        finance = await login(FINANCE)
        codes = await _codes(api, finance)

        before = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_6100 = next(
            (Decimal(r["debit"]) for r in before["rows"] if r["account_id"] == codes["6100"]),
            Decimal(0),
        )

        posted = await _issue_post(api, login, await _issue(api, login, s, "4"))
        assert posted["journal_entry_id"] is not None
        entry = (
            await api.get(f"/finance/journal-entries/{posted['journal_entry_id']}", headers=finance)
        ).json()
        assert entry["status"] == "POSTED"
        assert Decimal(entry["total_debit"]) == Decimal(400)  # 4 tons at the average of 100

        after = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_6100 = next(r for r in after["rows"] if r["account_id"] == codes["6100"])
        assert Decimal(after_6100["debit"]) == before_6100 + Decimal(400)

        cancelled = await api.post(
            f"/inventory/issues/{posted['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Wrong job charged"},
        )
        assert cancelled.status_code == 200, cancelled.text
        reversed_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        assert not any(r["account_id"] == codes["6100"] for r in reversed_tb["rows"])

    async def test_the_site_manager_is_not_shown_what_the_stock_cost(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        posted = await _issue_post(api, login, await _issue(api, login, s))
        assert posted["prices_hidden"] is True and posted["total_value"] is None
        assert posted["items"][0]["unit_cost"] is None and posted["items"][0]["value"] is None

    async def test_a_quantity_counted_in_another_unit_is_converted_for_the_ledger(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        body = _issue_body(s)
        body["lines"] = [
            {"material_id": s.keys["crush"], "quantity": "45", "unit_id": s.keys["cft"]}
        ]
        made = await api.post("/inventory/issues", headers=await login(MANAGER), json=body)
        assert made.status_code == 201, made.text
        posted = await _issue_post(api, login, made.json())
        line = posted["items"][0]
        assert Decimal(line["quantity"]) == 45 and line["unit_code"] == "CFT"
        base = Decimal(line["base_quantity"])
        assert line["base_unit_code"] == "TON" and Decimal("1.5") < base < Decimal("2.5")
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 10 - base

    async def test_you_cannot_issue_what_is_not_there(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "3", "100")
        draft = await _issue(api, login, s, "4")
        refused = await api.post(
            f"/inventory/issues/{draft['id']}/post", headers=await login(MANAGER)
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "insufficient_stock"
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 3

    async def test_an_issue_is_posted_once(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        posted = await _issue_post(api, login, await _issue(api, login, s))
        again = await api.post(
            f"/inventory/issues/{posted['id']}/post", headers=await login(MANAGER)
        )
        assert again.status_code == 422 and again.json()["rule"] == "issue_not_draft"
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 6

    async def test_cancelling_a_posted_issue_puts_the_stock_back_at_the_cost_it_left_at(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        posted = await _issue_post(api, login, await _issue(api, login, s, "4"))

        no_reason = await api.post(
            f"/inventory/issues/{posted['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "no"},
        )
        assert no_reason.status_code == 422
        cancelled = await api.post(
            f"/inventory/issues/{posted['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Issued to the wrong contractor"},
        )
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"

        balance = await _balance(api, login, "GVH-S1-YARD", s.keys)
        assert balance is not None
        assert Decimal(balance["quantity_on_hand"]) == 10
        assert Decimal(balance["average_cost"]) == Decimal(100)
        ledger_rows = (
            await api.get(
                "/inventory/ledger",
                headers=await login(PM),
                params={"material_id": s.keys["crush"], "limit": 50, "sort": "posted_at"},
            )
        ).json()["items"]
        kinds = [r["txn_type"] for r in ledger_rows]
        assert kinds[-2:] == ["ISSUE_OUT", "REVERSAL_IN"]
        assert ledger_rows[-1]["reversal_of_id"] == ledger_rows[-2]["id"]

    async def test_a_draft_can_be_cancelled_without_touching_stock(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        draft = await _issue(api, login, s)
        done = await api.post(
            f"/inventory/issues/{draft['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Not needed after all"},
        )
        assert done.status_code == 200
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 10

    async def test_only_stocked_materials_and_reachable_warehouses_are_accepted(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        unknown = await api.post(
            "/inventory/issues",
            headers=await login(MANAGER),
            json=_issue_body(s, warehouse_id=str(uuid4())),
        )
        assert unknown.status_code == 422
        # Another site's store is out of this manager's reach.
        elsewhere = await api.post(
            "/inventory/issues",
            headers=await login(MANAGER),
            json=_issue_body(s, warehouse_id=s.wh["CS-LHR-MAIN"]),
        )
        assert elsewhere.status_code == 404

    async def test_who_may_issue_and_who_may_see(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        draft = await _issue(api, login, s)
        staff = await api.post("/inventory/issues", headers=await login(STAFF), json=_issue_body(s))
        assert staff.status_code == 403
        # Another project's manager cannot see it, let alone post it.
        assert (
            await api.get(f"/inventory/issues/{draft['id']}", headers=await login(OTHER_PM))
        ).status_code == 404
        assert (
            await api.post(f"/inventory/issues/{draft['id']}/post", headers=await login(OTHER_PM))
        ).status_code in (403, 404)
        # An auditor reads.
        seen = await api.get("/inventory/issues", headers=await login(AUDITOR))
        assert seen.status_code == 200 and draft["id"] in {i["id"] for i in seen.json()["items"]}
        assert draft["can_post"] is True


def _transfer_body(s: Setup, source: str, target: str, quantity: str = "4") -> dict[str, Any]:
    return {
        "from_warehouse_id": s.wh[source],
        "to_warehouse_id": s.wh[target],
        "vehicle_number": "LEA-4411",
        "lines": [{"material_id": s.keys["crush"], "quantity": quantity, "unit_id": s.keys["ton"]}],
    }


async def _transfer(
    api: AsyncClient,
    login: Any,
    s: Setup,
    source: str = "GVH-S1-YARD",
    target: str = "CS-LHR-MAIN",
    quantity: str = "4",
) -> dict[str, Any]:
    made = await api.post(
        "/inventory/transfers",
        headers=await login(MANAGER),
        json=_transfer_body(s, source, target, quantity),
    )
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


async def _value(api: AsyncClient, login: Any, code: str, keys: dict[str, str]) -> Decimal:
    row = await _balance(api, login, code, keys)
    return Decimal(row["total_value"]) if row else Decimal(0)


class TestTransfers:
    async def test_dispatch_takes_stock_out_and_receive_counts_it_in_at_the_same_cost(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        await s.stock(db, "GVH-S1-YARD", "10", "120")  # average 110
        await s.stock(db, "CS-LHR-MAIN", "5", "200")  # the destination has its own average
        before = await _value(api, login, "GVH-S1-YARD", s.keys) + await _value(
            api, login, "CS-LHR-MAIN", s.keys
        )

        draft = await _transfer(api, login, s, quantity="4")
        assert draft["status"] == "DRAFT" and draft["transfer_number"].startswith("TRF-")
        assert draft["can_dispatch"] is True and draft["can_receive"] is False
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 20  # a draft moves nothing

        sent = await api.post(
            f"/inventory/transfers/{draft['id']}/dispatch", headers=await login(MANAGER)
        )
        assert sent.status_code == 200 and sent.json()["status"] == "IN_TRANSIT"
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 16
        # On the truck: not yet on hand at the destination, but not lost either.
        centre = await _balance(api, login, "CS-LHR-MAIN", s.keys)
        assert centre is not None
        assert Decimal(centre["quantity_on_hand"]) == 5
        assert Decimal(centre["quantity_in_transit"]) == 4

        received = await api.post(
            f"/inventory/transfers/{draft['id']}/receive", headers=await login(STORE)
        )
        assert received.status_code == 200, received.text
        assert received.json()["status"] == "RECEIVED"
        centre = await _balance(api, login, "CS-LHR-MAIN", s.keys)
        assert centre is not None
        assert Decimal(centre["quantity_on_hand"]) == 9
        assert Decimal(centre["quantity_in_transit"]) == 0
        # 5 @ 200 + 4 @ 110 = 1,440 over 9: moving stock blended, it did not revalue.
        assert Decimal(centre["total_value"]) == Decimal(1440)
        after = await _value(api, login, "GVH-S1-YARD", s.keys) + await _value(
            api, login, "CS-LHR-MAIN", s.keys
        )
        assert after == before

    async def test_only_the_destination_may_receive_and_only_the_source_may_dispatch(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        draft = await _transfer(api, login, s)

        # The receiving store cannot send what is not theirs.
        assert (
            await api.post(
                f"/inventory/transfers/{draft['id']}/dispatch", headers=await login(STORE)
            )
        ).status_code in (403, 404)
        await api.post(f"/inventory/transfers/{draft['id']}/dispatch", headers=await login(MANAGER))
        # The sender cannot count it in at the other end.
        refused = await api.post(
            f"/inventory/transfers/{draft['id']}/receive", headers=await login(MANAGER)
        )
        assert refused.status_code in (403, 404)
        # The destination sees it coming, and is told it can receive.
        seen = (
            await api.get(f"/inventory/transfers/{draft['id']}", headers=await login(STORE))
        ).json()
        assert seen["can_receive"] is True and seen["can_dispatch"] is False
        listed = (await api.get("/inventory/transfers", headers=await login(STORE))).json()
        assert draft["id"] in {t["id"] for t in listed["items"]}
        # A project that has nothing to do with it does not.
        assert (
            await api.get(f"/inventory/transfers/{draft['id']}", headers=await login(OTHER_PM))
        ).status_code == 404

    async def test_you_cannot_send_what_is_not_there(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "3", "100")
        draft = await _transfer(api, login, s, quantity="4")
        refused = await api.post(
            f"/inventory/transfers/{draft['id']}/dispatch", headers=await login(MANAGER)
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "insufficient_stock"

    async def test_a_transfer_in_transit_can_be_brought_back_and_a_received_one_cannot(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        back = await _transfer(api, login, s)
        await api.post(f"/inventory/transfers/{back['id']}/dispatch", headers=await login(MANAGER))
        cancelled = await api.post(
            f"/inventory/transfers/{back['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Truck broke down"},
        )
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 10
        centre = await _balance(api, login, "CS-LHR-MAIN", s.keys)
        assert centre is None or Decimal(centre["quantity_in_transit"]) == 0
        assert centre is None or Decimal(centre["quantity_on_hand"]) == 0

        done = await _transfer(api, login, s)
        await api.post(f"/inventory/transfers/{done['id']}/dispatch", headers=await login(MANAGER))
        await api.post(f"/inventory/transfers/{done['id']}/receive", headers=await login(STORE))
        refused = await api.post(
            f"/inventory/transfers/{done['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Changed my mind"},
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "transfer_received"

    async def test_a_transfer_goes_through_its_states_in_order(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        draft = await _transfer(api, login, s)
        early = await api.post(
            f"/inventory/transfers/{draft['id']}/receive", headers=await login(STORE)
        )
        assert early.status_code == 422 and early.json()["rule"] == "transfer_not_in_transit"
        await api.post(f"/inventory/transfers/{draft['id']}/dispatch", headers=await login(MANAGER))
        twice = await api.post(
            f"/inventory/transfers/{draft['id']}/dispatch", headers=await login(MANAGER)
        )
        assert twice.status_code == 422 and twice.json()["rule"] == "transfer_not_draft"

    async def test_a_transfer_needs_two_different_stores(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        same = await api.post(
            "/inventory/transfers",
            headers=await login(MANAGER),
            json=_transfer_body(s, "GVH-S1-YARD", "GVH-S1-YARD"),
        )
        assert same.status_code == 422

    async def test_stock_can_move_between_two_stores_on_the_same_site(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        draft = await _transfer(api, login, s, target="GVH-S1-GODOWN", quantity="6")
        await api.post(f"/inventory/transfers/{draft['id']}/dispatch", headers=await login(MANAGER))
        done = await api.post(
            f"/inventory/transfers/{draft['id']}/receive", headers=await login(MANAGER)
        )
        assert done.status_code == 200, done.text
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 4
        assert await _on_hand(api, login, "GVH-S1-GODOWN", s.keys) == 6


class TestTheLedgerStillAddsUp:
    async def test_after_issues_transfers_and_adjustments_every_balance_matches_its_ledger(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "50", "100")
        await s.stock(db, "GVH-S1-YARD", "50", "130")
        await _issue_post(api, login, await _issue(api, login, s, "7"))
        cancelled = await _issue_post(api, login, await _issue(api, login, s, "3"))
        await api.post(
            f"/inventory/issues/{cancelled['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Issued in error"},
        )
        moved = await _transfer(api, login, s, quantity="12")
        await api.post(f"/inventory/transfers/{moved['id']}/dispatch", headers=await login(MANAGER))
        await api.post(f"/inventory/transfers/{moved['id']}/receive", headers=await login(STORE))
        back = await _transfer(api, login, s, quantity="5")
        await api.post(f"/inventory/transfers/{back['id']}/dispatch", headers=await login(MANAGER))
        await api.post(
            f"/inventory/transfers/{back['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Truck broke down"},
        )
        submitted = await _submit(api, login, await _adjustment(api, login, s, "-4"))
        assert (await _decide(api, login, submitted, PM)).status_code == 200

        assert await reconcile.find_drift(db) == []


class TestPickers:
    async def _codes(self, api: AsyncClient, login: Any, who: str, action: str) -> set[str]:
        response = await api.get(
            "/inventory/warehouse-options", headers=await login(who), params={"action": action}
        )
        assert response.status_code == 200, response.text
        return {w["code"] for w in response.json()}

    async def test_a_person_is_offered_only_the_stores_they_may_act_on(
        self, api: AsyncClient, login: Any
    ) -> None:
        # The site manager holds no warehouse-management right, yet can pick their stores.
        assert await self._codes(api, login, MANAGER, "issue") == {"GVH-S1-YARD", "GVH-S1-GODOWN"}
        assert await self._codes(api, login, MANAGER, "adjust") == {"GVH-S1-YARD", "GVH-S1-GODOWN"}
        assert await self._codes(api, login, MANAGER, "transfer_from") == {
            "GVH-S1-YARD",
            "GVH-S1-GODOWN",
        }
        # A project manager reaches every site of their project.
        assert {"GVH-S1-YARD", "GVH-S2-YARD"} <= await self._codes(api, login, PM, "issue")
        assert "RSD-S1-YARD" not in await self._codes(api, login, PM, "issue")
        assert await self._codes(api, login, STORE, "issue") == {"CS-LHR-MAIN", "CS-LHR-QUAR"}

    async def test_a_transfer_may_be_sent_to_any_store(self, api: AsyncClient, login: Any) -> None:
        everywhere = await self._codes(api, login, MANAGER, "transfer_to")
        assert {"GVH-S1-YARD", "GVH-S2-YARD", "RSD-S1-YARD", "CS-LHR-MAIN"} <= everywhere

    async def test_someone_who_cannot_do_the_thing_is_offered_nothing(
        self, api: AsyncClient, login: Any
    ) -> None:
        # Site staff cannot see stock at all, so are turned away rather than shown nothing.
        assert (
            await api.get(
                "/inventory/warehouse-options",
                headers=await login(STAFF),
                params={"action": "issue"},
            )
        ).status_code == 403
        # An auditor may read stock but not act on it.
        assert await self._codes(api, login, AUDITOR, "issue") == set()
        assert await self._codes(api, login, AUDITOR, "adjust") == set()
        assert await self._codes(api, login, AUDITOR, "transfer_to") == set()


def _adjust_body(s: Setup, delta: str = "-2", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "warehouse_id": s.wh["GVH-S1-YARD"],
        "reason_code": "COUNT_CORRECTION",
        "reason_note": "Physical count came up short",
        "lines": [{"material_id": s.keys["crush"], "quantity_delta": delta}],
    }
    body.update(extra)
    return body


async def _adjustment(
    api: AsyncClient, login: Any, s: Setup, delta: str = "-2", **extra: Any
) -> dict[str, Any]:
    made = await api.post(
        "/inventory/adjustments",
        headers=await login(MANAGER),
        json=_adjust_body(s, delta, **extra),
    )
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


async def _submit(
    api: AsyncClient, login: Any, adjustment: dict[str, Any], who: str = MANAGER
) -> dict[str, Any]:
    submitted = await api.post(
        f"/inventory/adjustments/{adjustment['id']}/submit", headers=await login(who)
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()  # type: ignore[no-any-return]


async def _decide(
    api: AsyncClient, login: Any, adjustment: dict[str, Any], who: str, action: str = "approve"
) -> Any:
    return await api.post(
        f"/approvals/requests/{adjustment['approval_request_id']}/{action}",
        headers=await login(who),
        json={"comments": "Recounted with the storekeeper"} if action != "approve" else {},
    )


class TestAdjustments:
    async def test_raising_one_moves_nothing_and_records_what_the_books_said(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        draft = await _adjustment(api, login, s, "-2")
        assert draft["status"] == "DRAFT" and draft["adjustment_number"].startswith("ADJ-")
        assert draft["reason_code"] == "COUNT_CORRECTION"
        [line] = draft["items"]
        assert Decimal(line["system_quantity"]) == 10 and Decimal(line["quantity_delta"]) == -2
        # The site manager is not shown what the stock is worth.
        assert draft["prices_hidden"] is True and line["value_delta"] is None
        assert draft["can_submit"] is True and draft["can_edit"] is True
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 10

    async def test_a_small_adjustment_is_signed_by_the_project_manager_and_then_posts(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        submitted = await _submit(api, login, await _adjustment(api, login, s, "-2"))
        assert submitted["status"] == "PENDING_APPROVAL" and submitted["approval_request_id"]
        assert submitted["can_withdraw"] is True and submitted["can_edit"] is False
        # Awaiting a signature is not stock movement.
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 10

        # The person who raised it cannot sign it.
        assert (await _decide(api, login, submitted, MANAGER)).status_code in (403, 404, 422)
        assert (await _decide(api, login, submitted, PM)).status_code == 200

        posted = (
            await api.get(f"/inventory/adjustments/{submitted['id']}", headers=await login(PM))
        ).json()
        assert posted["status"] == "POSTED" and posted["posted_at"]
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 8
        assert Decimal(posted["net_value"]) == Decimal(-200)
        assert Decimal(posted["items"][0]["unit_cost"]) == Decimal(100)
        ledger_rows = (
            await api.get(
                "/inventory/ledger",
                headers=await login(PM),
                params={"material_id": s.keys["crush"], "limit": 50, "sort": "posted_at"},
            )
        ).json()["items"]
        assert ledger_rows[-1]["txn_type"] == "ADJUST_OUT"
        assert ledger_rows[-1]["source_id"] == posted["id"]
        assert posted["can_edit"] is False and posted["can_cancel"] is False

        # A shortfall is charged to site overheads, not to inventory's own
        # value — inventory only ever holds what a receipt or an issue moved.
        assert posted["journal_entry_id"] is not None
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        entry = (
            await api.get(f"/finance/journal-entries/{posted['journal_entry_id']}", headers=finance)
        ).json()
        assert entry["status"] == "POSTED"
        debit_account = next(
            line["account_id"] for line in entry["lines"] if Decimal(line["debit"]) > 0
        )
        assert debit_account == codes["6300"]
        assert Decimal(entry["total_debit"]) == Decimal(200)

    async def test_an_increase_is_valued_at_the_average_or_at_the_cost_given(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        adjustment = await _adjustment(api, login, s, "5")  # at the average, 100
        submitted = await _submit(api, login, adjustment)
        assert (await _decide(api, login, submitted, PM)).status_code == 200
        balance = await _balance(api, login, "GVH-S1-YARD", s.keys)
        assert balance is not None
        assert Decimal(balance["quantity_on_hand"]) == 15
        assert Decimal(balance["total_value"]) == Decimal(1500)

        priced = _adjust_body(s, "5")
        priced["lines"][0]["unit_cost"] = "160"
        made = await api.post("/inventory/adjustments", headers=await login(MANAGER), json=priced)
        assert made.status_code == 201, made.text
        submitted = await _submit(api, login, made.json())
        assert (await _decide(api, login, submitted, PM)).status_code == 200
        balance = await _balance(api, login, "GVH-S1-YARD", s.keys)
        assert balance is not None and Decimal(balance["total_value"]) == Decimal(2300)

    async def test_an_increase_with_nothing_in_stock_needs_a_cost(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        refused = await api.post(
            "/inventory/adjustments", headers=await login(MANAGER), json=_adjust_body(s, "5")
        )
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "lines.0.unit_cost"

    async def test_you_cannot_take_out_more_than_is_there_even_over_two_lines(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "3", "100")
        body = _adjust_body(s, "-2")
        body["lines"].append({"material_id": s.keys["crush"], "quantity_delta": "-2"})
        refused = await api.post("/inventory/adjustments", headers=await login(MANAGER), json=body)
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "lines.1.quantity_delta"

    async def test_a_reason_is_not_optional(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        body = _adjust_body(s)
        body["reason_note"] = "ok"
        assert (
            await api.post("/inventory/adjustments", headers=await login(MANAGER), json=body)
        ).status_code == 422
        body = _adjust_body(s)
        del body["reason_code"]
        assert (
            await api.post("/inventory/adjustments", headers=await login(MANAGER), json=body)
        ).status_code == 422
        body = _adjust_body(s, reason_code="BECAUSE")
        assert (
            await api.post("/inventory/adjustments", headers=await login(MANAGER), json=body)
        ).status_code == 422

    async def test_a_large_adjustment_needs_finance_as_well(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "500", "100")
        submitted = await _submit(api, login, await _adjustment(api, login, s, "-300"))  # 30,000
        assert (await _decide(api, login, submitted, PM)).status_code == 200
        # One signature in, still not posted.
        mid = (
            await api.get(f"/inventory/adjustments/{submitted['id']}", headers=await login(PM))
        ).json()
        assert mid["status"] == "PENDING_APPROVAL"
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 500
        assert (await _decide(api, login, submitted, FINANCE)).status_code == 200
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 200

    async def test_a_rejected_adjustment_moves_nothing_and_can_be_edited_and_resubmitted(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        submitted = await _submit(api, login, await _adjustment(api, login, s, "-9"))
        rejected = await _decide(api, login, submitted, PM, "reject")
        assert rejected.status_code == 200, rejected.text
        seen = (
            await api.get(f"/inventory/adjustments/{submitted['id']}", headers=await login(MANAGER))
        ).json()
        assert seen["status"] == "REJECTED" and seen["decision_reason"]
        assert seen["can_edit"] is True
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 10

        edited = await api.put(
            f"/inventory/adjustments/{submitted['id']}",
            headers=await login(MANAGER),
            json=_adjust_body(s, "-1", reason_note="Recount: only one bag is missing"),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "DRAFT"
        assert Decimal(edited.json()["items"][0]["quantity_delta"]) == -1
        again = await _submit(api, login, edited.json())
        assert (await _decide(api, login, again, PM)).status_code == 200
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 9

    async def test_a_pending_adjustment_can_be_withdrawn_and_then_edited(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        submitted = await _submit(api, login, await _adjustment(api, login, s))
        pulled = await api.post(
            f"/inventory/adjustments/{submitted['id']}/withdraw", headers=await login(MANAGER)
        )
        assert pulled.status_code == 200 and pulled.json()["status"] == "DRAFT"
        assert pulled.json()["can_edit"] is True
        again = await api.post(
            f"/inventory/adjustments/{submitted['id']}/withdraw", headers=await login(MANAGER)
        )
        assert again.status_code == 422 and again.json()["rule"] == "adjustment_not_pending"
        # Once withdrawn the old request cannot be decided.
        assert (await _decide(api, login, submitted, PM)).status_code in (409, 422)
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 10

    async def test_a_draft_can_be_cancelled_but_not_a_posted_one(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        draft = await _adjustment(api, login, s)
        done = await api.post(
            f"/inventory/adjustments/{draft['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Raised against the wrong store"},
        )
        assert done.status_code == 200 and done.json()["status"] == "CANCELLED"

        submitted = await _submit(api, login, await _adjustment(api, login, s))
        assert (await _decide(api, login, submitted, PM)).status_code == 200
        refused = await api.post(
            f"/inventory/adjustments/{submitted['id']}/cancel",
            headers=await login(MANAGER),
            json={"reason": "Changed my mind"},
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "adjustment_not_cancellable"
        edit = await api.put(
            f"/inventory/adjustments/{submitted['id']}",
            headers=await login(MANAGER),
            json=_adjust_body(s, "-1"),
        )
        assert edit.status_code == 422 and edit.json()["rule"] == "adjustment_not_editable"

    async def test_stock_used_since_submission_stops_the_submission(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        draft = await _adjustment(api, login, s, "-8")
        # Meanwhile the yard issues most of it.
        await _issue_post(api, login, await _issue(api, login, s, "5"))
        refused = await api.post(
            f"/inventory/adjustments/{draft['id']}/submit", headers=await login(MANAGER)
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "insufficient_stock"

    async def test_a_rule_that_approves_by_itself_still_names_its_approval_and_posts(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        published = await api.post(
            "/approval-workflows",
            headers=await login(ADMIN),
            json={
                "doc_type": "stock_adjustment",
                "name": "Auto for the test",
                "definition": {
                    "rules": [{"sequence": 10, "name": "Always", "condition": True, "steps": []}]
                },
            },
        )
        assert published.status_code in (200, 201), published.text
        submitted = await _submit(api, login, await _adjustment(api, login, s, "-1"))
        assert submitted["status"] == "POSTED"
        assert submitted["approval_request_id"] is not None
        assert await _on_hand(api, login, "GVH-S1-YARD", s.keys) == 9

    async def test_who_may_raise_and_who_may_sign(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        s = await _setup(api, login, db)
        await s.stock(db, "GVH-S1-YARD", "10", "100")
        assert (
            await api.post(
                "/inventory/adjustments", headers=await login(STAFF), json=_adjust_body(s)
            )
        ).status_code == 403
        assert (
            await api.post(
                "/inventory/adjustments", headers=await login(AUDITOR), json=_adjust_body(s)
            )
        ).status_code == 403
        draft = await _adjustment(api, login, s)
        assert (
            await api.get(f"/inventory/adjustments/{draft['id']}", headers=await login(OTHER_PM))
        ).status_code == 404
        listed = (await api.get("/inventory/adjustments", headers=await login(PM))).json()
        assert draft["id"] in {a["id"] for a in listed["items"]}
        as_pm = (
            await api.get(f"/inventory/adjustments/{draft['id']}", headers=await login(PM))
        ).json()
        assert as_pm["prices_hidden"] is False
        assert Decimal(as_pm["net_value"]) == Decimal(-200)
