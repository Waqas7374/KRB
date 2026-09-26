"""RFQ -> quotation -> selection -> purchase order -> approval, and the sourcing
loop that gives purchase-request lines back their `sourced_quantity`.

Seeded people used here:
  sm.gvh1      Imran Shah    site manager, GVH-S1 — raises the purchase request
  pm.gvh       Bilal Ahmad   project manager of GVH — approves the request
  procurement  Ahmed Raza    procurement manager — runs RFQs and quotations
  finance      Nadia Hussain finance manager — approves what procurement raised
  admin                      super administrator — raises orders procurement then approves
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow

pytestmark = pytest.mark.integration

REQUESTER = "sm.gvh1@krb.example"
PM = "pm.gvh@krb.example"
PROCUREMENT = "procurement@krb.example"
FINANCE = "finance@krb.example"
ADMIN = "admin@krb.example"

_SUSPEND = text(
    "UPDATE vendors SET status = 'SUSPENDED', suspension_reason = 'Under review' WHERE id = :id"
)
WHY = "Lowest total for the full quantity and can deliver within the week."


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


async def _place(api: AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    projects = (await api.get("/projects", headers=headers, params={"q": "GVH"})).json()["items"]
    project = next(p for p in projects if p["code"] == "GVH")
    sites = (await api.get("/sites", headers=headers, params={"project_id": project["id"]})).json()
    site = next(s for s in sites["items"] if s["code"] == "GVH-S1")
    return {"project_id": project["id"], "site_id": site["id"]}


async def _materials(api: AsyncClient, headers: dict[str, str], n: int) -> list[dict[str, Any]]:
    items = (await api.get("/materials", headers=headers, params={"limit": 50})).json()["items"]
    found = [m for m in items if m["is_purchasable"]][:n]
    assert len(found) == n
    return found


async def _decide(api: AsyncClient, headers: dict[str, str], request_id: str) -> None:
    done = await api.post(
        f"/approvals/requests/{request_id}/approve", headers=headers, json={"comments": "ok"}
    )
    assert done.status_code == 200, done.text


async def _approved_pr(
    api: AsyncClient, login: Any, *, quantities: tuple[str, ...] = ("100",)
) -> dict[str, Any]:
    """An approved purchase request with one line per quantity, estimated at
    100 a unit so the total stays under the 100 000 single-approver tier."""
    requester = await login(REQUESTER)
    materials = await _materials(api, requester, len(quantities))
    created = await api.post(
        "/purchase-requests",
        headers=requester,
        json={
            **await _place(api, requester),
            "justification": "Block A slab works",
            "items": [
                {
                    "material_id": m["id"],
                    "unit_id": m["base_unit_id"],
                    "quantity": q,
                    "estimated_rate": "100",
                }
                for m, q in zip(materials, quantities, strict=True)
            ],
        },
    )
    assert created.status_code == 201, created.text
    submitted = await api.post(
        f"/purchase-requests/{created.json()['id']}/submit", headers=requester
    )
    assert submitted.status_code == 200, submitted.text
    await _decide(api, await login(PM), submitted.json()["approval_request_id"])
    pr = (await api.get(f"/purchase-requests/{created.json()['id']}", headers=requester)).json()
    assert pr["status"] == "APPROVED"
    return pr  # type: ignore[no-any-return]


async def _vendors(api: AsyncClient, headers: dict[str, str], n: int) -> list[dict[str, Any]]:
    items = (
        await api.get("/vendors", headers=headers, params={"limit": 50, "status": "ACTIVE"})
    ).json()["items"]
    assert len(items) >= n
    return items[:n]


def _due() -> str:
    return (utcnow().date() + timedelta(days=7)).isoformat()


async def _issued_rfq(
    api: AsyncClient, buyer: dict[str, str], pr: dict[str, Any], vendor_count: int = 3
) -> dict[str, Any]:
    vendors = await _vendors(api, buyer, vendor_count)
    created = await api.post(
        "/rfqs",
        headers=buyer,
        json={
            "purchase_request_id": pr["id"],
            "title": "Slab materials",
            "due_date": _due(),
            "vendor_ids": [v["id"] for v in vendors],
        },
    )
    assert created.status_code == 201, created.text
    issued = await api.post(f"/rfqs/{created.json()['id']}/issue", headers=buyer)
    assert issued.status_code == 200, issued.text
    return issued.json()  # type: ignore[no-any-return]


def _quote(rfq: dict[str, Any], vendor_id: str, rates: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "vendor_id": vendor_id,
        "quote_date": utcnow().date().isoformat(),
        "valid_until": (utcnow().date() + timedelta(days=30)).isoformat(),
        "delivery_days": 5,
        "payment_terms": "30 days",
        "items": [
            {"rfq_item_id": item["id"], "rate": rate, **extra}
            for item, rate in zip(rfq["items"], rates, strict=False)
        ],
    }


async def _record(
    api: AsyncClient,
    buyer: dict[str, str],
    rfq: dict[str, Any],
    vendor_id: str,
    rates: list[str],
    **extra: Any,
) -> dict[str, Any]:
    response = await api.post(
        f"/rfqs/{rfq['id']}/quotations", headers=buyer, json=_quote(rfq, vendor_id, rates, **extra)
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def _select(
    api: AsyncClient, buyer: dict[str, str], quotation_id: str, reason: str = WHY
) -> Any:
    return await api.post(
        f"/quotations/{quotation_id}/select", headers=buyer, json={"reason": reason}
    )


async def _order_from(api: AsyncClient, buyer: dict[str, str], quotation_id: str) -> dict[str, Any]:
    created = await api.post(f"/purchase-orders/from-quotation/{quotation_id}", headers=buyer)
    assert created.status_code == 201, created.text
    return created.json()  # type: ignore[no-any-return]


async def _approve_order(
    api: AsyncClient, login: Any, po: dict[str, Any], approver: str = PROCUREMENT
) -> dict[str, Any]:
    """Submit as the administrator and approve as `approver` (procurement is
    the first tier for the seeded PO workflow)."""
    admin = await login(ADMIN)
    submitted = await api.post(f"/purchase-orders/{po['id']}/submit", headers=admin)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "PENDING_APPROVAL"
    await _decide(api, await login(approver), submitted.json()["approval_request_id"])
    final = (await api.get(f"/purchase-orders/{po['id']}", headers=admin)).json()
    assert final["status"] == "APPROVED", final
    return final  # type: ignore[no-any-return]


async def _pr(api: AsyncClient, login: Any, pr_id: str) -> dict[str, Any]:
    return (  # type: ignore[no-any-return]
        await api.get(f"/purchase-requests/{pr_id}", headers=await login(REQUESTER))
    ).json()


# -----------------------------------------------------------------------------
# RFQs
# -----------------------------------------------------------------------------


class TestRfq:
    async def test_an_rfq_is_raised_from_the_unsourced_lines_of_an_approved_request(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login, quantities=("100", "40"))
        buyer = await login(PROCUREMENT)
        vendors = await _vendors(api, buyer, 2)
        created = await api.post(
            "/rfqs",
            headers=buyer,
            json={
                "purchase_request_id": pr["id"],
                "title": "Slab materials",
                "due_date": _due(),
                "vendor_ids": [v["id"] for v in vendors],
            },
        )
        assert created.status_code == 201, created.text
        rfq = created.json()
        assert rfq["status"] == "DRAFT"
        assert rfq["rfq_number"].startswith("RFQ-")
        assert rfq["pr_number"] == pr["pr_number"]
        assert [Decimal(i["quantity"]) for i in rfq["items"]] == [Decimal(100), Decimal(40)]
        assert all(i["pr_item_id"] for i in rfq["items"])
        # What the requester budgeted travels with the line, to read bids against.
        assert {Decimal(i["estimated_rate"]) for i in rfq["items"]} == {Decimal(100)}
        assert {v["status"] for v in rfq["vendors"]} == {"INVITED"}
        # The RFQ inherits the request's place.
        assert rfq["project_id"] == pr["project_id"] and rfq["site_id"] == pr["site_id"]

    async def test_a_request_that_is_not_approved_cannot_be_sourced(
        self, api: AsyncClient, login: Any
    ) -> None:
        requester = await login(REQUESTER)
        material = (await _materials(api, requester, 1))[0]
        draft = await api.post(
            "/purchase-requests",
            headers=requester,
            json={
                **await _place(api, requester),
                "justification": "Not yet approved",
                "items": [
                    {
                        "material_id": material["id"],
                        "unit_id": material["base_unit_id"],
                        "quantity": "5",
                        "estimated_rate": "10",
                    }
                ],
            },
        )
        buyer = await login(PROCUREMENT)
        refused = await api.post(
            "/rfqs",
            headers=buyer,
            json={"purchase_request_id": draft.json()["id"], "title": "Too early"},
        )
        assert refused.status_code == 422
        assert refused.json()["rule"] == "purchase_request_not_sourceable"

    async def test_issuing_needs_vendors_and_a_due_date(self, api: AsyncClient, login: Any) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        bare = (
            await api.post(
                "/rfqs", headers=buyer, json={"purchase_request_id": pr["id"], "title": "Bare"}
            )
        ).json()
        refused = await api.post(f"/rfqs/{bare['id']}/issue", headers=buyer)
        assert refused.status_code == 422 and refused.json()["rule"] == "rfq_no_vendors"

        vendor = (await _vendors(api, buyer, 1))[0]
        await api.post(
            f"/rfqs/{bare['id']}/vendors", headers=buyer, json={"vendor_ids": [vendor["id"]]}
        )
        refused = await api.post(f"/rfqs/{bare['id']}/issue", headers=buyer)
        assert refused.status_code == 422 and refused.json()["rule"] == "rfq_no_due_date"

    async def test_only_active_vendors_can_be_invited(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        vendor = (await _vendors(api, buyer, 1))[0]
        await db.execute(_SUSPEND, {"id": vendor["id"]})
        refused = await api.post(
            "/rfqs",
            headers=buyer,
            json={
                "purchase_request_id": pr["id"],
                "title": "Suspended vendor",
                "vendor_ids": [vendor["id"]],
            },
        )
        assert refused.status_code == 422, refused.text
        assert "suspended" in refused.text

    async def test_an_issued_rfq_is_no_longer_editable_and_vendors_are_not_withdrawn(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=2)
        edit = await api.put(
            f"/rfqs/{rfq['id']}",
            headers=buyer,
            json={
                "title": "Changed",
                "due_date": _due(),
                "items": [
                    {
                        "material_id": rfq["items"][0]["material_id"],
                        "unit_id": rfq["items"][0]["unit_id"],
                        "quantity": "1",
                        "pr_item_id": rfq["items"][0]["pr_item_id"],
                    }
                ],
            },
        )
        assert edit.status_code == 422 and edit.json()["rule"] == "rfq_locked"
        withdrawn = await api.delete(
            f"/rfqs/{rfq['id']}/vendors/{rfq['vendors'][0]['id']}", headers=buyer
        )
        assert withdrawn.status_code == 422 and withdrawn.json()["rule"] == "rfq_locked"

    async def test_a_quantity_beyond_what_the_request_still_needs_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login, quantities=("10",))
        buyer = await login(PROCUREMENT)
        line = pr["items"][0]
        refused = await api.post(
            "/rfqs",
            headers=buyer,
            json={
                "purchase_request_id": pr["id"],
                "title": "Too much",
                "items": [
                    {
                        "material_id": line["material_id"],
                        "unit_id": line["unit_id"],
                        "quantity": "11",
                        "pr_item_id": line["id"],
                    }
                ],
            },
        )
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "items.0.quantity"

    async def test_the_site_manager_cannot_run_rfqs(self, api: AsyncClient, login: Any) -> None:
        requester = await login(REQUESTER)
        assert (await api.get("/rfqs", headers=requester)).status_code == 403


# -----------------------------------------------------------------------------
# Quotations and the comparison
# -----------------------------------------------------------------------------


class TestQuotations:
    async def test_totals_are_computed_from_rate_discount_and_tax(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login, quantities=("100",))
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr)
        quote = await _record(
            api,
            buyer,
            rfq,
            rfq["vendors"][0]["vendor_id"],
            ["80"],
            discount_pct="10",
            tax_pct="17",
        )
        # 100 x 80 = 8 000; 10% off = 7 200; 17% tax on that = 1 224.
        assert Decimal(quote["subtotal"]) == Decimal(8000)
        assert Decimal(quote["discount_amount"]) == Decimal(800)
        assert Decimal(quote["tax_amount"]) == Decimal(1224)
        assert Decimal(quote["total_amount"]) == Decimal(8424)
        assert Decimal(quote["items"][0]["net_rate"]) == Decimal(72)
        assert quote["status"] == "RECEIVED"

        reloaded = (await api.get(f"/rfqs/{rfq['id']}", headers=buyer)).json()
        answered = next(v for v in reloaded["vendors"] if v["vendor_id"] == quote["vendor_id"])
        assert (
            answered["status"] == "QUOTED"
            and answered["quotation_number"] == quote["quotation_number"]
        )

    async def test_one_quotation_per_vendor_and_only_from_invited_vendors(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=2)
        vendor_id = rfq["vendors"][0]["vendor_id"]
        await _record(api, buyer, rfq, vendor_id, ["90"])

        again = await api.post(
            f"/rfqs/{rfq['id']}/quotations", headers=buyer, json=_quote(rfq, vendor_id, ["85"])
        )
        assert again.status_code == 422 and again.json()["rule"] == "quotation_exists"

        stranger = (await _vendors(api, buyer, 10))[-1]["id"]
        assert stranger not in {v["vendor_id"] for v in rfq["vendors"]}
        refused = await api.post(
            f"/rfqs/{rfq['id']}/quotations", headers=buyer, json=_quote(rfq, stranger, ["85"])
        )
        assert refused.status_code == 422

    async def test_quotations_can_only_be_recorded_against_an_issued_rfq(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        vendor = (await _vendors(api, buyer, 1))[0]
        draft = (
            await api.post(
                "/rfqs",
                headers=buyer,
                json={
                    "purchase_request_id": pr["id"],
                    "title": "Draft",
                    "vendor_ids": [vendor["id"]],
                },
            )
        ).json()
        refused = await api.post(
            f"/rfqs/{draft['id']}/quotations",
            headers=buyer,
            json=_quote(draft, vendor["id"], ["10"]),
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "rfq_not_issued"

    async def test_comparison_marks_the_lowest_rate_on_each_line(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login, quantities=("100", "50"))
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=3)
        a, b, c = (v["vendor_id"] for v in rfq["vendors"])
        # A is cheaper on line 1, B on line 2; C quotes only line 1 (and is the
        # cheapest there) so its total must not be presented as the lowest.
        qa = await _record(api, buyer, rfq, a, ["90", "60"])
        qb = await _record(api, buyer, rfq, b, ["95", "50"])
        qc = await api.post(
            f"/rfqs/{rfq['id']}/quotations",
            headers=buyer,
            json={
                "vendor_id": c,
                "quote_date": utcnow().date().isoformat(),
                "items": [{"rfq_item_id": rfq["items"][0]["id"], "rate": "85"}],
            },
        )
        assert qc.status_code == 201

        grid = (await api.get(f"/rfqs/{rfq['id']}/comparison", headers=buyer)).json()
        assert [col["vendor_id"] for col in grid["columns"]] == [a, b, c]
        line1, line2 = grid["rows"]
        assert {v for v, cell in line1["cells"].items() if cell["is_lowest"]} == {c}
        assert {v for v, cell in line2["cells"].items() if cell["is_lowest"]} == {b}
        assert Decimal(line1["estimated_rate"]) == Decimal(100)

        by_vendor = {col["vendor_id"]: col for col in grid["columns"]}
        assert by_vendor[c]["covers_all"] is False and by_vendor[c]["lines_quoted"] == 1
        assert by_vendor[c]["is_lowest_total"] is False
        # 100x90 + 50x60 = 12 000 vs 100x95 + 50x50 = 12 000 — a tie, both lowest.
        assert Decimal(qa["total_amount"]) == Decimal(qb["total_amount"]) == Decimal(12000)
        assert by_vendor[a]["is_lowest_total"] and by_vendor[b]["is_lowest_total"]
        assert grid["can_select"] is True

    async def test_a_rejected_quotation_leaves_the_grid(self, api: AsyncClient, login: Any) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=2)
        a, b = (v["vendor_id"] for v in rfq["vendors"])
        qa = await _record(api, buyer, rfq, a, ["70"])
        await _record(api, buyer, rfq, b, ["80"])
        rejected = await api.post(
            f"/quotations/{qa['id']}/reject", headers=buyer, json={"reason": "Wrong specification"}
        )
        assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
        grid = (await api.get(f"/rfqs/{rfq['id']}/comparison", headers=buyer)).json()
        assert set(grid["rows"][0]["cells"]) == {b}


class TestSelection:
    async def test_selection_needs_a_real_reason_whatever_the_price(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=2)
        cheapest = await _record(api, buyer, rfq, rfq["vendors"][0]["vendor_id"], ["50"])

        for reason in ("", "ok", "cheapest"):
            refused = await _select(api, buyer, cheapest["id"], reason)
            assert refused.status_code in {422}, reason
        chosen = await _select(api, buyer, cheapest["id"])
        assert chosen.status_code == 200, chosen.text
        body = chosen.json()
        assert body["status"] == "SELECTED" and body["selection_reason"] == WHY
        assert body["selected_by_name"] == "Ahmed Raza" and body["selected_at"]

    async def test_choosing_another_vendor_demotes_the_first_and_keeps_one_winner(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=2)
        qa = await _record(api, buyer, rfq, rfq["vendors"][0]["vendor_id"], ["50"])
        qb = await _record(api, buyer, rfq, rfq["vendors"][1]["vendor_id"], ["60"])
        assert (await _select(api, buyer, qa["id"])).status_code == 200
        second = await _select(api, buyer, qb["id"], "B holds stock and A cannot deliver in time.")
        assert second.status_code == 200

        first = (await api.get(f"/quotations/{qa['id']}", headers=buyer)).json()
        assert first["status"] == "SHORTLISTED" and first["selection_reason"] is None

    async def test_an_expired_quotation_cannot_be_selected(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=1)
        body = _quote(rfq, rfq["vendors"][0]["vendor_id"], ["50"])
        body["quote_date"] = (utcnow().date() - timedelta(days=40)).isoformat()
        body["valid_until"] = (utcnow().date() - timedelta(days=10)).isoformat()
        quote = (await api.post(f"/rfqs/{rfq['id']}/quotations", headers=buyer, json=body)).json()
        refused = await _select(api, buyer, quote["id"])
        assert refused.status_code == 422 and refused.json()["rule"] == "quotation_expired"

    async def test_selecting_writes_the_reason_to_the_audit_log(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=1)
        quote = await _record(api, buyer, rfq, rfq["vendors"][0]["vendor_id"], ["50"])
        assert (await _select(api, buyer, quote["id"])).status_code == 200
        summaries = (
            (
                await db.execute(
                    text(
                        "SELECT summary FROM audit_logs "
                        "WHERE entity_id = :id AND action = 'APPROVE'"
                    ),
                    {"id": quote["id"]},
                )
            )
            .scalars()
            .all()
        )
        assert any(WHY in s for s in summaries)

    async def test_the_database_refuses_a_selection_without_a_reason(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        from sqlalchemy.exc import IntegrityError

        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=1)
        quote = await _record(api, buyer, rfq, rfq["vendors"][0]["vendor_id"], ["50"])
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    text("UPDATE vendor_quotations SET status = 'SELECTED' WHERE id = :id"),
                    {"id": quote["id"]},
                )


# -----------------------------------------------------------------------------
# Purchase orders and the sourcing loop
# -----------------------------------------------------------------------------


class TestPurchaseOrders:
    async def _selected(
        self, api: AsyncClient, login: Any, *, quantities: tuple[str, ...] = ("100",)
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
        pr = await _approved_pr(api, login, quantities=quantities)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=2)
        quote = await _record(
            api, buyer, rfq, rfq["vendors"][0]["vendor_id"], ["100"] * len(quantities), tax_pct="17"
        )
        assert (await _select(api, buyer, quote["id"])).status_code == 200
        return pr, rfq, quote, buyer

    async def test_an_order_cannot_be_raised_from_a_quotation_nobody_selected(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login)
        buyer = await login(PROCUREMENT)
        rfq = await _issued_rfq(api, buyer, pr, vendor_count=1)
        quote = await _record(api, buyer, rfq, rfq["vendors"][0]["vendor_id"], ["50"])
        refused = await api.post(f"/purchase-orders/from-quotation/{quote['id']}", headers=buyer)
        assert refused.status_code == 422 and refused.json()["rule"] == "quotation_not_selected"

    async def test_the_order_inherits_the_quotation_and_the_request_link(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr, rfq, quote, buyer = await self._selected(api, login)
        po = await _order_from(api, buyer, quote["id"])
        assert po["status"] == "DRAFT" and po["revision"] == 0
        assert po["po_number"].startswith("PO-")
        assert po["quotation_number"] == quote["quotation_number"]
        assert po["rfq_number"] == rfq["rfq_number"]
        assert po["vendor_id"] == quote["vendor_id"]
        assert po["payment_terms"] == "30 days"
        [line] = po["items"]
        assert line["pr_item_id"] == pr["items"][0]["id"] and line["pr_number"] == pr["pr_number"]
        # 100 x 100 = 10 000 + 17% = 11 700, exactly the quotation.
        assert Decimal(po["total_amount"]) == Decimal(quote["total_amount"]) == Decimal(11700)

        # A second order from the same quotation is refused while the first stands.
        again = await api.post(f"/purchase-orders/from-quotation/{quote['id']}", headers=buyer)
        assert again.status_code == 422 and again.json()["rule"] == "quotation_ordered"

    async def test_approval_sources_the_request_and_closes_the_rfq(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr, rfq, quote, buyer = await self._selected(api, login)
        po = await _order_from(api, buyer, quote["id"])

        # Nothing is counted until the order is approved.
        assert Decimal((await _pr(api, login, pr["id"]))["items"][0]["sourced_quantity"]) == 0

        approved = await _approve_order(api, login, po)
        assert approved["approved_at"]

        after = await _pr(api, login, pr["id"])
        assert Decimal(after["items"][0]["sourced_quantity"]) == Decimal(100)
        assert after["status"] == "SOURCED"
        closed = (await api.get(f"/rfqs/{rfq['id']}", headers=buyer)).json()
        assert closed["status"] == "CLOSED" and closed["close_reason"].startswith("Ordered:")

    async def test_partial_sourcing_and_oversourcing(self, api: AsyncClient, login: Any) -> None:
        pr = await _approved_pr(api, login, quantities=("100",))
        admin = await login(ADMIN)
        vendor = (await _vendors(api, admin, 1))[0]
        line = pr["items"][0]

        def order(quantity: str) -> dict[str, Any]:
            return {
                "vendor_id": vendor["id"],
                "project_id": pr["project_id"],
                "site_id": pr["site_id"],
                "items": [
                    {
                        "material_id": line["material_id"],
                        "unit_id": line["unit_id"],
                        "quantity": quantity,
                        "rate": "100",
                        "pr_item_id": line["id"],
                    }
                ],
            }

        first = (await api.post("/purchase-orders", headers=admin, json=order("60"))).json()
        await _approve_order(api, login, first)
        partial = await _pr(api, login, pr["id"])
        assert Decimal(partial["items"][0]["sourced_quantity"]) == Decimal(60)
        assert partial["status"] == "PARTIALLY_SOURCED"

        # Only 40 remain, so an order for 41 is refused at once, not at approval.
        too_many = await api.post("/purchase-orders", headers=admin, json=order("41"))
        assert too_many.status_code == 422
        assert "only 40" in too_many.text

        rest = (await api.post("/purchase-orders", headers=admin, json=order("40"))).json()
        await _approve_order(api, login, rest)
        assert (await _pr(api, login, pr["id"]))["status"] == "SOURCED"

    async def test_two_orders_drafted_together_cannot_both_be_approved_for_the_same_units(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr = await _approved_pr(api, login, quantities=("100",))
        admin = await login(ADMIN)
        vendor = (await _vendors(api, admin, 1))[0]
        line = pr["items"][0]
        body = {
            "vendor_id": vendor["id"],
            "project_id": pr["project_id"],
            "site_id": pr["site_id"],
            "items": [
                {
                    "material_id": line["material_id"],
                    "unit_id": line["unit_id"],
                    "quantity": "80",
                    "rate": "100",
                    "pr_item_id": line["id"],
                }
            ],
        }
        first = (await api.post("/purchase-orders", headers=admin, json=body)).json()
        second = (await api.post("/purchase-orders", headers=admin, json=body)).json()
        s1 = (await api.post(f"/purchase-orders/{first['id']}/submit", headers=admin)).json()
        s2 = (await api.post(f"/purchase-orders/{second['id']}/submit", headers=admin)).json()
        procurement = await login(PROCUREMENT)
        await _decide(api, procurement, s1["approval_request_id"])
        refused = await api.post(
            f"/approvals/requests/{s2['approval_request_id']}/approve",
            headers=procurement,
            json={"comments": "ok"},
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["rule"] == "purchase_request_oversourced"
        assert Decimal((await _pr(api, login, pr["id"]))["items"][0]["sourced_quantity"]) == 80

    async def test_cancelling_an_approved_order_gives_the_quantity_back_and_reopens_the_rfq(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr, rfq, quote, buyer = await self._selected(api, login)
        po = await _approve_order(api, login, await _order_from(api, buyer, quote["id"]))
        cancelled = await api.post(
            f"/purchase-orders/{po['id']}/cancel",
            headers=buyer,
            json={"reason": "Vendor withdrew the offer"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"
        after = await _pr(api, login, pr["id"])
        assert Decimal(after["items"][0]["sourced_quantity"]) == 0
        assert after["status"] == "APPROVED"
        reopened = (await api.get(f"/rfqs/{rfq['id']}", headers=buyer)).json()
        assert reopened["status"] == "ISSUED"
        # The quotation is free again: another order may be raised from it.
        assert (await _order_from(api, buyer, quote["id"]))["status"] == "DRAFT"

    async def test_an_amended_order_must_be_approved_again(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr, _rfq, quote, buyer = await self._selected(api, login)
        po = await _approve_order(api, login, await _order_from(api, buyer, quote["id"]))

        amended = await api.post(
            f"/purchase-orders/{po['id']}/amend",
            headers=buyer,
            json={"reason": "Vendor revised the quantity"},
        )
        assert amended.status_code == 200, amended.text
        body = amended.json()
        assert body["status"] == "DRAFT" and body["revision"] == 1 and body["approved_at"] is None
        # Reopening gives the request its units back until re-approval.
        assert Decimal((await _pr(api, login, pr["id"]))["items"][0]["sourced_quantity"]) == 0

        line = body["items"][0]
        change = {
            "vendor_id": body["vendor_id"],
            "project_id": body["project_id"],
            "site_id": body["site_id"],
            "items": [
                {
                    "material_id": line["material_id"],
                    "unit_id": line["unit_id"],
                    "quantity": "90",
                    "rate": "100",
                    "tax_pct": "17",
                    "pr_item_id": line["pr_item_id"],
                }
            ],
        }
        put = await api.put(f"/purchase-orders/{po['id']}", headers=buyer, json=change)
        assert put.status_code == 200, put.text
        assert Decimal(put.json()["total_amount"]) == Decimal(10530)

        admin = await login(ADMIN)
        submitted = await api.post(f"/purchase-orders/{po['id']}/submit", headers=admin)
        assert submitted.status_code == 200, submitted.text
        await _decide(api, await login(PROCUREMENT), submitted.json()["approval_request_id"])
        final = (await api.get(f"/purchase-orders/{po['id']}", headers=admin)).json()
        assert final["status"] == "APPROVED" and final["revision"] == 1
        assert Decimal((await _pr(api, login, pr["id"]))["items"][0]["sourced_quantity"]) == 90

    async def test_send_acknowledge_and_close_return_the_unreceived_quantity(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr, _rfq, quote, buyer = await self._selected(api, login)
        po = await _approve_order(api, login, await _order_from(api, buyer, quote["id"]))

        # Not before approval, and only once.
        assert (
            await api.post(f"/purchase-orders/{po['id']}/acknowledge", headers=buyer)
        ).status_code == 422
        sent = await api.post(f"/purchase-orders/{po['id']}/send", headers=buyer)
        assert sent.status_code == 200 and sent.json()["status"] == "SENT"
        assert (
            await api.post(f"/purchase-orders/{po['id']}/send", headers=buyer)
        ).status_code == 422
        ack = await api.post(f"/purchase-orders/{po['id']}/acknowledge", headers=buyer)
        assert ack.json()["status"] == "ACKNOWLEDGED"

        closed = await api.post(
            f"/purchase-orders/{po['id']}/close",
            headers=buyer,
            json={"reason": "Site no longer needs the balance"},
        )
        assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
        after = await _pr(api, login, pr["id"])
        assert Decimal(after["items"][0]["sourced_quantity"]) == 0

    async def test_a_suspended_vendor_gets_no_order(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        _pr, _rfq, quote, buyer = await self._selected(api, login)
        po = await _approve_order(api, login, await _order_from(api, buyer, quote["id"]))
        await db.execute(_SUSPEND, {"id": po["vendor_id"]})
        refused = await api.post(f"/purchase-orders/{po['id']}/send", headers=buyer)
        assert refused.status_code == 422 and refused.json()["rule"] == "vendor_not_active"

    async def test_an_order_procurement_raised_is_not_approved_by_procurement(
        self, api: AsyncClient, login: Any
    ) -> None:
        """Self-approval is blocked; the first step's escalation target — the
        finance manager — signs instead."""
        _pr, _rfq, quote, buyer = await self._selected(api, login)
        po = await _order_from(api, buyer, quote["id"])
        submitted = await api.post(f"/purchase-orders/{po['id']}/submit", headers=buyer)
        assert submitted.status_code == 200, submitted.text
        request_id = submitted.json()["approval_request_id"]
        own = await api.post(
            f"/approvals/requests/{request_id}/approve", headers=buyer, json={"comments": "mine"}
        )
        assert own.status_code == 403
        await _decide(api, await login(FINANCE), request_id)
        final = (await api.get(f"/purchase-orders/{po['id']}", headers=buyer)).json()
        assert final["status"] == "APPROVED"

    async def test_large_orders_take_the_longer_chain(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        vendor = (await _vendors(api, admin, 1))[0]
        material = (await _materials(api, admin, 1))[0]
        place = await _place(api, admin)
        chain_for = {}
        for total in ("400000", "800000", "6000000"):
            po = (
                await api.post(
                    "/purchase-orders",
                    headers=admin,
                    json={
                        "vendor_id": vendor["id"],
                        **place,
                        "items": [
                            {
                                "material_id": material["id"],
                                "unit_id": material["base_unit_id"],
                                "quantity": "1",
                                "rate": total,
                            }
                        ],
                    },
                )
            ).json()
            submitted = (
                await api.post(f"/purchase-orders/{po['id']}/submit", headers=admin)
            ).json()
            trail = (
                await api.get(
                    "/approvals/requests",
                    headers=admin,
                    params={"doc_type": "purchase_order", "doc_id": po["id"]},
                )
            ).json()
            chain_for[total] = [s["name"] for s in trail[0]["steps"]]
            assert submitted["status"] == "PENDING_APPROVAL"
        assert chain_for["400000"] == ["Procurement manager"]
        assert chain_for["800000"] == ["Procurement manager", "Finance manager"]
        assert chain_for["6000000"] == ["Procurement manager", "Finance manager", "Executive"]

    async def test_a_reader_without_pricing_permission_sees_no_money(
        self, api: AsyncClient, login: Any
    ) -> None:
        pr, _rfq, quote, buyer = await self._selected(api, login)
        po = await _order_from(api, buyer, quote["id"])
        site_manager = await login(REQUESTER)
        seen = await api.get(f"/purchase-orders/{po['id']}", headers=site_manager)
        assert seen.status_code == 200, seen.text
        body = seen.json()
        assert body["prices_hidden"] is True
        assert body["total_amount"] is None and body["subtotal"] is None
        assert body["items"][0]["rate"] is None and body["items"][0]["line_total"] is None
        # Quantities are not prices: the site still sees what is on order.
        assert Decimal(body["items"][0]["quantity"]) == Decimal(100)
        listing = (await api.get("/purchase-orders", headers=site_manager)).json()["items"]
        assert all(row["total_amount"] is None for row in listing)
        _ = pr

    async def test_the_direct_order_path_needs_no_rfq(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        vendor = (await _vendors(api, admin, 1))[0]
        material = (await _materials(api, admin, 1))[0]
        created = await api.post(
            "/purchase-orders",
            headers=admin,
            json={
                "vendor_id": vendor["id"],
                **await _place(api, admin),
                "items": [
                    {
                        "material_id": material["id"],
                        "unit_id": material["base_unit_id"],
                        "quantity": "3",
                        "rate": "1000",
                        "discount_pct": "5",
                        "tax_pct": "17",
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["quotation_id"] is None
        # 3 000 - 5% = 2 850; + 17% = 3 334.50
        assert Decimal(body["total_amount"]) == Decimal("3334.5")


class TestPurchaseOrderPdf:
    async def _order(
        self, api: AsyncClient, login: Any, **extra: str
    ) -> tuple[dict[str, Any], dict[str, str]]:
        admin = await login(ADMIN)
        vendor = (await _vendors(api, admin, 1))[0]
        material = (await _materials(api, admin, 1))[0]
        created = await api.post(
            "/purchase-orders",
            headers=admin,
            json={
                "vendor_id": vendor["id"],
                **await _place(api, admin),
                "terms_and_conditions": extra.get("terms", "Pay within 30 days."),
                "items": [
                    {
                        "material_id": material["id"],
                        "unit_id": material["base_unit_id"],
                        "quantity": "3",
                        "rate": "1000",
                        "tax_pct": "17",
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        return created.json(), admin

    async def test_the_pdf_is_a_real_document_named_for_the_order(
        self, api: AsyncClient, login: Any
    ) -> None:
        po, admin = await self._order(api, login)
        response = await api.get(f"/purchase-orders/{po['id']}/pdf", headers=admin)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/pdf"
        assert f'filename="{po["po_number"]}.pdf"' in response.headers["content-disposition"]
        assert response.content.startswith(b"%PDF-") and len(response.content) > 1500

    async def test_a_reader_without_pricing_permission_gets_no_pdf(
        self, api: AsyncClient, login: Any
    ) -> None:
        po, _ = await self._order(api, login)
        site_manager = await login(REQUESTER)
        response = await api.get(f"/purchase-orders/{po['id']}/pdf", headers=site_manager)
        assert response.status_code == 403

    async def test_a_draft_is_watermarked_and_typed_text_cannot_inject_markup(
        self, api: AsyncClient, login: Any
    ) -> None:
        from app.modules.procurement.api.po_document import render_purchase_order
        from app.modules.procurement.sourcing_schemas import PurchaseOrderRead

        po, admin = await self._order(api, login, terms="<script>alert(1)</script> & <b>bold</b>")
        view = PurchaseOrderRead.model_validate(
            (await api.get(f"/purchase-orders/{po['id']}", headers=admin)).json()
        )
        html = render_purchase_order(view, company_name="KRB <Developments>")
        assert "NOT APPROVED" in html
        assert "<script>" not in html and "&lt;script&gt;" in html
        assert "KRB &lt;Developments&gt;" in html
        assert "PKR 3,510.00" in html  # 3 x 1000 + 17 %
