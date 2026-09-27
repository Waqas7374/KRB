"""Posting rules and budgets (docs/02 §8, docs/07 §3): the sub-ledger → GL
mapping as configuration, and the plan a purchase order commits against and a
goods receipt releases from.

Seeded people: finance@krb.example (FINANCE_MANAGER, owns posting rules and
budgets), pm.gvh@krb.example (PROJECT_MANAGER, sees budgets but cannot create
or approve one), admin, staff.gvh1 (no finance permission at all).

The commitment-wiring tests reuse the delivery/PO/GRN helpers from
test_deliveries and test_grn_inventory rather than re-building that setup:
they are the one path in the app that already raises, approves and receives
against a purchase order end to end.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.types import utcnow
from tests.integration.test_deliveries import (
    ADMIN,
    PROCUREMENT,
    SITE_MANAGER,
    _approved_order,
    _keys,
    _rate,
)
from tests.integration.test_finance import _codes
from tests.integration.test_grn_inventory import _approved_delivery, _grn, _post

pytestmark = pytest.mark.integration

FINANCE = "finance@krb.example"
PM = "pm.gvh@krb.example"
STAFF = "staff.gvh1@krb.example"


def _current_fy() -> int:
    today = utcnow().date()
    # Matches seeds/finance.py: the company's fiscal year starts in July.
    return today.year if today.month >= 7 else today.year - 1


def _budget_body(project_id: str, *lines: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "fiscal_year": _current_fy(),
        "name": "Test budget",
        "lines": list(lines),
        **extra,
    }


def _line(account_id: str, amount: str = "1000000", **extra: Any) -> dict[str, Any]:
    return {"account_id": account_id, "budgeted_amount": amount, **extra}


class TestPostingRules:
    async def test_the_seeded_receipt_rules_are_listed(self, api: AsyncClient, login: Any) -> None:
        rows = (
            await api.get(
                "/finance/posting-rules",
                headers=await login(FINANCE),
                params={"source_type": "GRN", "event": "RECEIPT", "limit": 50},
            )
        ).json()["items"]
        assert len(rows) == 4
        assert all(r["source_type"] == "GRN" and r["event"] == "RECEIPT" for r in rows)
        assert all(r["debit_account_code"] and r["credit_account_code"] for r in rows)

    async def test_only_finance_coa_manage_may_create_or_edit_a_rule(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        refused = await api.post(
            "/finance/posting-rules",
            headers=await login(STAFF),
            json={
                "source_type": "GRN",
                "event": "RECEIPT",
                "debit_account_id": codes["1400"],
                "credit_account_id": codes["2100"],
            },
        )
        assert refused.status_code == 403

    async def test_a_condition_must_use_known_variables(self, api: AsyncClient, login: Any) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        made = await api.post(
            "/finance/posting-rules",
            headers=finance,
            json={
                "source_type": "GRN",
                "event": "RECEIPT",
                "name": "Bad condition",
                "condition": {"==": [{"var": "not_a_real_variable"}, True]},
                "debit_account_id": codes["1400"],
                "credit_account_id": codes["2100"],
            },
        )
        assert made.status_code == 422

    async def test_a_new_rule_can_be_created_then_edited_and_deactivated(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        made = await api.post(
            "/finance/posting-rules",
            headers=finance,
            json={
                "source_type": "GRN",
                "event": "SAMPLE_EVENT",
                "name": "A throwaway rule for this test",
                "debit_account_id": codes["1400"],
                "credit_account_id": codes["2100"],
                "priority": 5,
            },
        )
        assert made.status_code == 201, made.text
        rule = made.json()
        assert rule["priority"] == 5 and rule["is_active"] is True

        edited = await api.patch(
            f"/finance/posting-rules/{rule['id']}",
            headers={**finance, "If-Match": str(rule["version"])},
            json={"priority": 9, "is_active": False},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["priority"] == 9 and edited.json()["is_active"] is False


class TestBudgets:
    async def test_a_budget_needs_at_least_one_line(self, api: AsyncClient, login: Any) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        made = await api.post(
            "/finance/budgets", headers=finance, json=_budget_body(keys["project"])
        )
        assert made.status_code == 422

    async def test_two_lines_for_the_same_phase_cost_centre_and_account_are_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))
        made = await api.post(
            "/finance/budgets",
            headers=finance,
            json=_budget_body(
                keys["project"], _line(codes["1400"], "500"), _line(codes["1400"], "600")
            ),
        )
        assert made.status_code == 422

    async def test_only_finance_budget_create_may_raise_one(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))
        refused = await api.post(
            "/finance/budgets",
            headers=await login(PM),
            json=_budget_body(keys["project"], _line(codes["1400"])),
        )
        assert refused.status_code == 403

    async def test_draft_edit_approve_revise_and_close(self, api: AsyncClient, login: Any) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))

        made = await api.post(
            "/finance/budgets",
            headers=finance,
            json=_budget_body(keys["project"], _line(codes["1400"], "100000")),
        )
        assert made.status_code == 201, made.text
        budget = made.json()
        assert budget["status"] == "DRAFT" and budget["can_edit"] is True
        assert Decimal(budget["total_amount"]) == Decimal(100000)

        # A project manager may see it, but not approve it.
        seen = await api.get(f"/finance/budgets/{budget['id']}", headers=await login(PM))
        assert seen.status_code == 200 and seen.json()["can_approve"] is False
        blocked = await api.post(
            f"/finance/budgets/{budget['id']}/approve", headers=await login(PM)
        )
        assert blocked.status_code == 403

        edited = await api.put(
            f"/finance/budgets/{budget['id']}",
            headers={**finance, "If-Match": str(budget["version"])},
            json=_budget_body(keys["project"], _line(codes["1400"], "120000")),
        )
        assert edited.status_code == 200, edited.text
        budget = edited.json()

        approved = await api.post(
            f"/finance/budgets/{budget['id']}/approve",
            headers={**finance, "If-Match": str(budget["version"])},
        )
        assert approved.status_code == 200, approved.text
        budget = approved.json()
        assert budget["status"] == "APPROVED" and budget["can_edit"] is False

        cannot_edit = await api.put(
            f"/finance/budgets/{budget['id']}",
            headers={**finance, "If-Match": str(budget["version"])},
            json=_budget_body(keys["project"], _line(codes["1400"], "999")),
        )
        assert cannot_edit.status_code == 422

        line_id = budget["lines"][0]["id"]
        revised = await api.post(
            f"/finance/budgets/{budget['id']}/revise",
            headers={**finance, "If-Match": str(budget["version"])},
            json={"revisions": {line_id: "150000"}},
        )
        assert revised.status_code == 200, revised.text
        budget = revised.json()
        assert budget["status"] == "REVISED"
        assert Decimal(budget["lines"][0]["revised_amount"]) == Decimal(150000)
        assert Decimal(budget["total_amount"]) == Decimal(150000)

        closed = await api.post(
            f"/finance/budgets/{budget['id']}/close",
            headers={**finance, "If-Match": str(budget["version"])},
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "CLOSED"


class TestCommitmentWiring:
    async def test_approving_a_po_commits_and_posting_its_grn_releases_and_posts_the_ledger(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))

        made = await api.post(
            "/finance/budgets",
            headers=finance,
            json=_budget_body(keys["project"], _line(codes["1400"], "1000000")),
        )
        assert made.status_code == 201, made.text
        budget = made.json()
        approved = await api.post(
            f"/finance/budgets/{budget['id']}/approve",
            headers={**finance, "If-Match": str(budget["version"])},
        )
        assert approved.status_code == 200, approved.text

        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_1400 = next(
            (Decimal(r["debit"]) for r in before_tb["rows"] if r["account_id"] == codes["1400"]),
            Decimal(0),
        )

        await _rate(api, await login(PROCUREMENT), keys, "100")
        po = await _approved_order(api, login, keys, "50", rate="100")  # commits 5,000

        mid = (await api.get(f"/finance/budgets/{budget['id']}", headers=finance)).json()
        line = mid["lines"][0]
        assert Decimal(line["committed_amount"]) == Decimal(5000)
        assert Decimal(line["remaining_amount"]) == Decimal(1000000 - 5000)

        commitments = (
            await api.get(
                "/finance/commitments",
                headers=finance,
                params={"budget_line_id": line["id"]},
            )
        ).json()["items"]
        assert len(commitments) == 1
        assert commitments[0]["status"] == "OPEN"
        assert Decimal(commitments[0]["amount"]) == Decimal(5000)

        delivery = await _approved_delivery(api, login, keys, "50", po=po)
        grn = await _grn(api, login, delivery)
        posted = await _post(api, login, grn)
        assert posted["status"] == "POSTED"

        after = (await api.get(f"/finance/budgets/{budget['id']}", headers=finance)).json()
        after_line = after["lines"][0]
        assert Decimal(after_line["committed_amount"]) == Decimal(0)
        assert Decimal(after_line["actual_amount"]) == Decimal(5000)

        after_commitments = (
            await api.get(
                "/finance/commitments", headers=finance, params={"budget_line_id": line["id"]}
            )
        ).json()["items"]
        assert after_commitments[0]["status"] == "RELEASED"
        assert Decimal(after_commitments[0]["released_amount"]) == Decimal(5000)

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_1400 = next(r for r in after_tb["rows"] if r["account_id"] == codes["1400"])
        assert Decimal(after_1400["debit"]) == before_1400 + Decimal(5000)
        after_2110 = next(r for r in after_tb["rows"] if r["account_id"] == codes["2110"])
        assert Decimal(after_2110["credit"]) >= Decimal(5000)

    async def test_a_counter_purchase_posts_straight_to_accounts_payable(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))

        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_2100 = next(
            (Decimal(r["credit"]) for r in before_tb["rows"] if r["account_id"] == codes["2100"]),
            Decimal(0),
        )

        warehouses = (
            await api.get("/warehouses", headers=await login(ADMIN), params={"limit": 50})
        ).json()["items"]
        yard = next(w for w in warehouses if w["code"] == "GVH-S1-YARD")
        counter = await api.post(
            "/grns",
            headers=await login(SITE_MANAGER),
            json={
                "warehouse_id": yard["id"],
                "vendor_id": keys["vendor"],
                "reference": f"BILL-{utcnow().timestamp()}",
                "lines": [
                    {
                        "material_id": keys["crush"],
                        "unit_id": keys["ton"],
                        "quantity": "3",
                        "rate": "100",
                    }
                ],
            },
        )
        assert counter.status_code == 201, counter.text
        posted = await api.post(
            f"/grns/{counter.json()['id']}/approve", headers=await login(PM), json={}
        )
        assert posted.status_code == 200, posted.text

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_2100 = next(r for r in after_tb["rows"] if r["account_id"] == codes["2100"])
        assert Decimal(after_2100["credit"]) == before_2100 + Decimal(300)
