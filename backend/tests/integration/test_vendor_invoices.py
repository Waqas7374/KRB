"""Vendor invoices, the 3-way match and AP ageing (docs/02 §8, docs/07
§`/finance`).

Seeded people: finance@krb.example (FINANCE_MANAGER, matches, disputes and
approves), accounts@krb.example (ACCOUNTS_OFFICER, enters and matches but
cannot approve), admin, procurement@krb.example (approves purchase orders),
sm.gvh1/pm.gvh (delivery -> GRN, reused from test_deliveries/test_grn_inventory),
staff.gvh1 (no finance permission at all).

Builds on the same PO -> delivery -> GRN pipeline test_budgets.py already
reuses, since it is the one path in the app that gets a real GRN line (with a
real po_item_id) posted end to end.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.types import utcnow
from tests.integration.test_deliveries import ADMIN, PROCUREMENT, _approved_order, _keys, _rate
from tests.integration.test_finance import _codes
from tests.integration.test_grn_inventory import _approved_delivery, _grn, _post

pytestmark = pytest.mark.integration

FINANCE = "finance@krb.example"
ACCOUNTS = "accounts@krb.example"
STAFF = "staff.gvh1@krb.example"


def _ref(prefix: str = "BILL") -> str:
    return f"{prefix}-{utcnow().timestamp()}"


def _line(**extra: Any) -> dict[str, Any]:
    return {"quantity": "50", "rate": "100", **extra}


def _body(vendor_id: str, *lines: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "vendor_id": vendor_id,
        "vendor_invoice_ref": _ref(),
        "invoice_date": utcnow().date().isoformat(),
        "items": list(lines),
        **extra,
    }


async def _po_and_grn(
    api: AsyncClient, login: Any, keys: dict[str, str], quantity: str = "50", rate: str = "100"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A PO, approved, delivered and received in full — a real 3-way-matchable
    GRN line, with its own po_item_id, the way test_budgets.py builds one."""
    await _rate(api, await login(PROCUREMENT), keys, rate)
    po = await _approved_order(api, login, keys, quantity, rate=rate)
    delivery = await _approved_delivery(api, login, keys, quantity, po=po)
    grn = await _grn(api, login, delivery)
    posted = await _post(api, login, grn)
    return po, posted


async def _counter_grn(
    api: AsyncClient, login: Any, keys: dict[str, str], quantity: str = "3", rate: str = "100"
) -> dict[str, Any]:
    """A GRN with no PO behind it — a counter purchase, degrading any invoice
    matched against it to 2-way."""
    from tests.integration.test_deliveries import SITE_MANAGER
    from tests.integration.test_grn_inventory import PM

    warehouses = (
        await api.get("/warehouses", headers=await login(ADMIN), params={"limit": 50})
    ).json()["items"]
    yard = next(w for w in warehouses if w["code"] == "GVH-S1-YARD")
    created = await api.post(
        "/grns",
        headers=await login(SITE_MANAGER),
        json={
            "warehouse_id": yard["id"],
            "vendor_id": keys["vendor"],
            "reference": _ref("GRN"),
            "lines": [
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
    posted = await api.post(
        f"/grns/{created.json()['id']}/approve", headers=await login(PM), json={}
    )
    assert posted.status_code == 200, posted.text
    return posted.json()  # type: ignore[no-any-return]


class TestTaxCodes:
    async def test_the_seeded_tax_codes_are_listed(self, api: AsyncClient, login: Any) -> None:
        rows = (
            await api.get("/finance/tax-codes", headers=await login(FINANCE), params={"limit": 50})
        ).json()["items"]
        assert {r["code"] for r in rows} >= {"GST", "SST", "WHT-GOODS", "WHT-SERVICES"}
        withholding = next(r for r in rows if r["code"] == "WHT-GOODS")
        assert withholding["tax_type"] == "WITHHOLDING" and withholding["section_code"]

    async def test_only_finance_coa_manage_may_create_or_edit_a_code(
        self, api: AsyncClient, login: Any
    ) -> None:
        refused = await api.post(
            "/finance/tax-codes",
            headers=await login(STAFF),
            json={
                "code": "NOPE",
                "name": "Not allowed",
                "tax_type": "SALES_TAX",
                "rate_pct": "5",
                "applies_to": "GOODS",
            },
        )
        assert refused.status_code == 403

    async def test_a_duplicate_code_is_refused(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        again = await api.post(
            "/finance/tax-codes",
            headers=admin,
            json={
                "code": "GST",
                "name": "Copy",
                "tax_type": "SALES_TAX",
                "rate_pct": "5",
                "applies_to": "GOODS",
            },
        )
        assert again.status_code == 409

    async def test_a_code_can_be_edited_by_hand(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        made = await api.post(
            "/finance/tax-codes",
            headers=admin,
            json={
                "code": "TST1",
                "name": "Throwaway",
                "tax_type": "SALES_TAX",
                "rate_pct": "5",
                "applies_to": "GOODS",
            },
        )
        assert made.status_code == 201, made.text
        code = made.json()
        edited = await api.patch(
            f"/finance/tax-codes/{code['id']}",
            headers={**admin, "If-Match": str(code["version"])},
            json={"rate_pct": "7.5", "is_active": False},
        )
        assert edited.status_code == 200, edited.text
        assert Decimal(edited.json()["rate_pct"]) == Decimal("7.5")
        assert edited.json()["is_active"] is False


class TestVendorInvoiceCreation:
    async def test_a_line_with_no_order_or_grn_needs_an_account(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        refused = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line()),
        )
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "items.0.account_id"

    async def test_a_direct_line_with_an_account_is_accepted_as_draft(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(account_id=codes["5100"])),
        )
        assert made.status_code == 201, made.text
        invoice = made.json()
        assert invoice["status"] == "DRAFT"
        assert invoice["can_edit"] is True and invoice["can_delete"] is True
        assert Decimal(invoice["total_amount"]) == Decimal(5000)
        assert invoice["items"][0]["account_code"] == "5100"

    async def test_a_grn_backed_line_resolves_its_own_account_from_the_posting_rule(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        _po, grn = await _po_and_grn(api, login, keys)
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(grn_item_id=grn["items"][0]["id"])),
        )
        assert made.status_code == 201, made.text
        item = made.json()["items"][0]
        assert item["account_code"] == "2110"  # the GRN's own accrual account

    async def test_duplicate_vendor_invoice_ref_for_the_same_vendor_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)
        ref = _ref()
        first = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(account_id=codes["5100"]), vendor_invoice_ref=ref),
        )
        assert first.status_code == 201, first.text
        again = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(account_id=codes["5100"]), vendor_invoice_ref=ref),
        )
        assert again.status_code == 409

    async def test_only_finance_ap_create_may_raise_an_invoice(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, await login(FINANCE))
        refused = await api.post(
            "/finance/vendor-invoices",
            headers=await login(STAFF),
            json=_body(keys["vendor"], _line(account_id=codes["5100"])),
        )
        assert refused.status_code == 403

    async def test_a_draft_can_be_edited_and_a_draft_can_be_deleted(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(account_id=codes["5100"])),
        )
        invoice = made.json()
        edited = await api.put(
            f"/finance/vendor-invoices/{invoice['id']}",
            headers={**finance, "If-Match": str(invoice["version"])},
            json=_body(
                keys["vendor"],
                _line(account_id=codes["5100"], quantity="10", rate="50"),
                vendor_invoice_ref=invoice["vendor_invoice_ref"],
            ),
        )
        assert edited.status_code == 200, edited.text
        assert Decimal(edited.json()["total_amount"]) == Decimal(500)

        deleted = await api.delete(f"/finance/vendor-invoices/{invoice['id']}", headers=finance)
        assert deleted.status_code == 204
        gone = await api.get(f"/finance/vendor-invoices/{invoice['id']}", headers=finance)
        assert gone.status_code == 404


class TestMatching:
    async def test_a_3way_line_within_tolerance_matches_clean(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        _po, grn = await _po_and_grn(api, login, keys, quantity="50", rate="100")
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(grn_item_id=grn["items"][0]["id"])),
        )
        invoice = made.json()
        matched = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/match",
            headers={**finance, "If-Match": str(invoice["version"])},
        )
        assert matched.status_code == 200, matched.text
        result = matched.json()
        assert result["status"] == "MATCHED"
        item = result["items"][0]
        assert item["match_type"] == "THREE_WAY"
        assert item["within_tolerance"] is True
        assert result["can_approve"] is True

    async def test_a_3way_line_with_a_rate_outside_tolerance_disputes_itself(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        _po, grn = await _po_and_grn(api, login, keys, quantity="50", rate="100")
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            # Billed at 110, but the order (and the tolerance) says 100.
            json=_body(keys["vendor"], _line(grn_item_id=grn["items"][0]["id"], rate="110")),
        )
        invoice = made.json()
        matched = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/match",
            headers={**finance, "If-Match": str(invoice["version"])},
        )
        assert matched.status_code == 200, matched.text
        result = matched.json()
        assert result["status"] == "DISPUTED"
        assert result["items"][0]["within_tolerance"] is False
        assert result["decision_reason"]
        assert result["can_approve"] is False

    async def test_a_3way_line_with_qty_within_the_configured_tolerance_still_matches(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        # Tolerance is max(abs=0.5, 2% of 50=1) = 1 ton either way.
        _po, grn = await _po_and_grn(api, login, keys, quantity="50", rate="100")
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(grn_item_id=grn["items"][0]["id"], quantity="50.8")),
        )
        invoice = made.json()
        matched = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/match",
            headers={**finance, "If-Match": str(invoice["version"])},
        )
        assert matched.status_code == 200, matched.text
        assert matched.json()["status"] == "MATCHED"

    async def test_a_2way_counter_purchase_line_degrades_gracefully(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        grn = await _counter_grn(api, login, keys, quantity="3", rate="100")
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(
                keys["vendor"], _line(grn_item_id=grn["items"][0]["id"], quantity="3", rate="100")
            ),
        )
        assert made.status_code == 201, made.text
        assert made.json()["items"][0]["account_id"] is None  # nothing to post at approval
        invoice = made.json()
        matched = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/match",
            headers={**finance, "If-Match": str(invoice["version"])},
        )
        assert matched.status_code == 200, matched.text
        result = matched.json()
        assert result["status"] == "MATCHED"
        assert result["items"][0]["match_type"] == "TWO_WAY"

    async def test_an_unmatched_direct_line_always_passes_its_own_check(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(account_id=codes["5100"])),
        )
        invoice = made.json()
        matched = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/match",
            headers={**finance, "If-Match": str(invoice["version"])},
        )
        assert matched.status_code == 200, matched.text
        result = matched.json()
        assert result["status"] == "MATCHED"
        assert result["items"][0]["match_type"] == "UNMATCHED"

    async def test_a_disputed_invoice_can_be_corrected_and_rematched(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        _po, grn = await _po_and_grn(api, login, keys, quantity="50", rate="100")
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(grn_item_id=grn["items"][0]["id"], rate="110")),
        )
        invoice = made.json()
        disputed = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/match",
                headers={**finance, "If-Match": str(invoice["version"])},
            )
        ).json()
        assert disputed["status"] == "DISPUTED"

        fixed = await api.put(
            f"/finance/vendor-invoices/{invoice['id']}",
            headers={**finance, "If-Match": str(disputed["version"])},
            json=_body(
                keys["vendor"],
                _line(grn_item_id=grn["items"][0]["id"], rate="100"),
                vendor_invoice_ref=disputed["vendor_invoice_ref"],
            ),
        )
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["status"] == "DRAFT"

        rematched = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/match",
            headers={**finance, "If-Match": str(fixed.json()["version"])},
        )
        assert rematched.status_code == 200, rematched.text
        assert rematched.json()["status"] == "MATCHED"

    async def test_a_matched_invoice_can_be_disputed_by_hand(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(account_id=codes["5100"])),
        )
        invoice = made.json()
        matched = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/match",
                headers={**finance, "If-Match": str(invoice["version"])},
            )
        ).json()
        disputed = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/dispute",
            headers={**finance, "If-Match": str(matched["version"])},
            json={"reason": "Vendor sent a corrected bill separately"},
        )
        assert disputed.status_code == 200, disputed.text
        assert disputed.json()["status"] == "DISPUTED"

    async def test_only_finance_ap_approve_may_dispute_by_hand(
        self, api: AsyncClient, login: Any
    ) -> None:
        officer = await login(ACCOUNTS)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, officer)
        made = await api.post(
            "/finance/vendor-invoices",
            headers=officer,
            json=_body(keys["vendor"], _line(account_id=codes["5100"])),
        )
        invoice = made.json()
        matched = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/match",
                headers={**officer, "If-Match": str(invoice["version"])},
            )
        ).json()
        refused = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/dispute",
            headers={**officer, "If-Match": str(matched["version"])},
            json={"reason": "Not the officer's call to make"},
        )
        assert refused.status_code == 403


class TestApproval:
    async def test_approve_requires_the_invoice_to_be_matched_first(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(account_id=codes["5100"])),
        )
        invoice = made.json()
        refused = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/approve",
            headers={**finance, "If-Match": str(invoice["version"])},
        )
        assert refused.status_code == 422
        assert refused.json()["rule"] == "vendor_invoice_not_matched"

    async def test_approving_a_3way_matched_invoice_clears_the_grn_accrual_into_payable(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        admin = await login(ADMIN)
        keys = await _keys(api, admin)
        codes = await _codes(api, finance)
        po, grn = await _po_and_grn(api, login, keys, quantity="50", rate="100")

        # 2110 nets to zero once this invoice clears exactly what the GRN
        # accrued, so the trial balance (which only shows a nonzero net) would
        # simply drop the row — the general ledger, which lists every posted
        # line regardless of net, is the tool that actually proves it moved.
        before_2110_gl = (
            await api.get(
                "/finance/general-ledger",
                headers=finance,
                params={"account_id": codes["2110"]},
            )
        ).json()
        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_2100 = next(
            (Decimal(r["credit"]) for r in before_tb["rows"] if r["account_id"] == codes["2100"]),
            Decimal(0),
        )

        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(grn_item_id=grn["items"][0]["id"])),
        )
        invoice = made.json()
        matched = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/match",
                headers={**finance, "If-Match": str(invoice["version"])},
            )
        ).json()
        approved = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/approve",
            headers={**finance, "If-Match": str(matched["version"])},
        )
        assert approved.status_code == 200, approved.text
        result = approved.json()
        assert result["status"] == "APPROVED"
        assert result["journal_entry_id"] is not None

        after_2110_gl = (
            await api.get(
                "/finance/general-ledger",
                headers=finance,
                params={"account_id": codes["2110"]},
            )
        ).json()
        # The invoice's own entry debits 2110 (clearing the GRN's accrual) and
        # credits 2100 (the real payable) by the same amount: 5,000. 2110 is a
        # credit-normal account, so a new 5,000 debit moves its balance down
        # by exactly that much from what the GRN alone left it at.
        assert len(after_2110_gl["rows"]) == len(before_2110_gl["rows"]) + 1
        new_line = after_2110_gl["rows"][-1]
        assert Decimal(new_line["debit"]) == Decimal(5000) and Decimal(new_line["credit"]) == 0
        assert Decimal(after_2110_gl["closing_balance"]) == Decimal(
            before_2110_gl["closing_balance"]
        ) - Decimal(5000)

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_2100 = next(r for r in after_tb["rows"] if r["account_id"] == codes["2100"])
        assert Decimal(after_2100["credit"]) == before_2100 + Decimal(5000)

        po_after = (await api.get(f"/purchase-orders/{po['id']}", headers=admin)).json()
        assert Decimal(po_after["items"][0]["invoiced_quantity"]) == Decimal(50)

    async def test_approving_a_2way_matched_invoice_posts_nothing_new(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)
        grn = await _counter_grn(api, login, keys, quantity="3", rate="100")

        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_2100 = next(
            (Decimal(r["credit"]) for r in before_tb["rows"] if r["account_id"] == codes["2100"]),
            Decimal(0),
        )

        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(
                keys["vendor"], _line(grn_item_id=grn["items"][0]["id"], quantity="3", rate="100")
            ),
        )
        invoice = made.json()
        matched = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/match",
                headers={**finance, "If-Match": str(invoice["version"])},
            )
        ).json()
        approved = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/approve",
            headers={**finance, "If-Match": str(matched["version"])},
        )
        assert approved.status_code == 200, approved.text
        result = approved.json()
        assert result["status"] == "APPROVED"
        assert result["journal_entry_id"] is None  # the GRN already booked it in full

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_2100 = next(r for r in after_tb["rows"] if r["account_id"] == codes["2100"])
        assert Decimal(after_2100["credit"]) == before_2100  # unchanged by the invoice

    async def test_an_unmatched_direct_line_posts_from_its_own_named_account(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)

        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_5100 = next(
            (Decimal(r["debit"]) for r in before_tb["rows"] if r["account_id"] == codes["5100"]),
            Decimal(0),
        )

        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(keys["vendor"], _line(account_id=codes["5100"], quantity="1", rate="750")),
        )
        invoice = made.json()
        matched = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/match",
                headers={**finance, "If-Match": str(invoice["version"])},
            )
        ).json()
        approved = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/approve",
            headers={**finance, "If-Match": str(matched["version"])},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["journal_entry_id"] is not None

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_5100 = next(r for r in after_tb["rows"] if r["account_id"] == codes["5100"])
        assert Decimal(after_5100["debit"]) == before_5100 + Decimal(750)

    async def test_only_finance_ap_approve_may_approve(self, api: AsyncClient, login: Any) -> None:
        officer = await login(ACCOUNTS)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, officer)
        made = await api.post(
            "/finance/vendor-invoices",
            headers=officer,
            json=_body(keys["vendor"], _line(account_id=codes["5100"])),
        )
        invoice = made.json()
        matched = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/match",
                headers={**officer, "If-Match": str(invoice["version"])},
            )
        ).json()
        refused = await api.post(
            f"/finance/vendor-invoices/{invoice['id']}/approve",
            headers={**officer, "If-Match": str(matched["version"])},
        )
        assert refused.status_code == 403


class TestPayablesAging:
    async def test_an_approved_invoice_ages_into_the_right_bucket(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)
        today = utcnow().date()
        # Due 45 days ago -> the 31-60 bucket, as of today.
        made = await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(
                keys["vendor"],
                _line(account_id=codes["5100"]),
                invoice_date=(today - timedelta(days=60)).isoformat(),
                due_date=(today - timedelta(days=45)).isoformat(),
            ),
        )
        invoice = made.json()
        matched = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/match",
                headers={**finance, "If-Match": str(invoice["version"])},
            )
        ).json()
        approved = (
            await api.post(
                f"/finance/vendor-invoices/{invoice['id']}/approve",
                headers={**finance, "If-Match": str(matched["version"])},
            )
        ).json()

        aging = (
            await api.get(
                "/finance/payables/aging", headers=finance, params={"as_of": today.isoformat()}
            )
        ).json()
        row = next(r for r in aging["rows"] if r["vendor_id"] == keys["vendor"])
        assert Decimal(row["days_31_60"]) >= Decimal(approved["total_amount"])
        assert Decimal(row["current"]) == 0 or Decimal(row["current"]) < Decimal(
            approved["total_amount"]
        )

    async def test_a_draft_invoice_never_appears_in_aging(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        codes = await _codes(api, finance)

        def _outstanding(rows: list[dict[str, Any]]) -> Decimal:
            row = next((r for r in rows if r["vendor_id"] == keys["vendor2"]), None)
            return Decimal(row["total"]) if row else Decimal(0)

        before = (await api.get("/finance/payables/aging", headers=finance)).json()["rows"]
        await api.post(
            "/finance/vendor-invoices",
            headers=finance,
            json=_body(
                keys["vendor2"], _line(account_id=codes["5100"], quantity="1", rate="99999")
            ),
        )
        after = (await api.get("/finance/payables/aging", headers=finance)).json()["rows"]
        # A brand-new (unmatched, unapproved) draft is not yet a real
        # obligation and contributes nothing to what a vendor is owed.
        assert _outstanding(after) == _outstanding(before)

    async def test_only_finance_ap_view_may_see_aging(self, api: AsyncClient, login: Any) -> None:
        refused = await api.get("/finance/payables/aging", headers=await login(STAFF))
        assert refused.status_code == 403
