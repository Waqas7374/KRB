"""Payment requests, payments, allocation, clearing and cancellation
(docs/02 §8, docs/07 §`/finance`).

Seeded people: finance@krb.example (FINANCE_MANAGER, approves and executes),
accounts@krb.example (ACCOUNTS_OFFICER, raises requests but cannot approve or
execute), ceo@krb.example (EXECUTIVE, signs large requests), admin,
staff.gvh1 (no finance permission at all).

A payment settles a vendor invoice, so several tests build one the same
lightweight way test_vendor_invoices.py does: a direct line against a named
account, matched and approved — nothing about payments cares how an invoice
got to APPROVED, only that it did.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.types import utcnow
from tests.integration.test_deliveries import ADMIN, _keys
from tests.integration.test_finance import _codes
from tests.integration.test_vendor_invoices import _body, _line

pytestmark = pytest.mark.integration

FINANCE = "finance@krb.example"
ACCOUNTS = "accounts@krb.example"
EXECUTIVE = "ceo@krb.example"
STAFF = "staff.gvh1@krb.example"


async def _decide(
    api: AsyncClient, headers: dict[str, str], request_id: str, action: str = "approve"
) -> Any:
    payload = {} if action == "approve" else {"comments": "Needs another look"}
    return await api.post(
        f"/approvals/requests/{request_id}/{action}", headers=headers, json=payload
    )


async def _bank_account_id(api: AsyncClient, headers: dict[str, str], title: str) -> str:
    rows = (await api.get("/finance/bank-accounts", headers=headers, params={"limit": 50})).json()[
        "items"
    ]
    return next(r for r in rows if r["account_title"] == title)["id"]  # type: ignore[no-any-return]


def _pr_body(vendor_id: str, amount: str = "5000", **extra: Any) -> dict[str, Any]:
    return {
        "vendor_id": vendor_id,
        "amount": amount,
        "reason": "A test payment request, raised by the suite",
        **extra,
    }


async def _approved_request(
    api: AsyncClient, login: Any, vendor_id: str, amount: str = "5000"
) -> dict[str, Any]:
    """A payment request raised by accounts, signed by finance alone (below
    the 200,000 tier)."""
    officer = await login(ACCOUNTS)
    made = await api.post(
        "/finance/payment-requests", headers=officer, json=_pr_body(vendor_id, amount)
    )
    assert made.status_code == 201, made.text
    request = made.json()
    submitted = await api.post(
        f"/finance/payment-requests/{request['id']}/submit",
        headers={**officer, "If-Match": str(request["version"])},
    )
    assert submitted.status_code == 200, submitted.text
    pending = submitted.json()
    decided = await _decide(api, await login(FINANCE), pending["approval_request_id"])
    assert decided.status_code == 200, decided.text
    fresh = await api.get(f"/finance/payment-requests/{request['id']}", headers=officer)
    return fresh.json()  # type: ignore[no-any-return]


async def _approved_invoice(
    api: AsyncClient, login: Any, vendor_id: str, amount: str = "5000"
) -> dict[str, Any]:
    """A direct-line invoice, matched and approved — how it got there is not
    a payment's concern, only that it is APPROVED and owes something."""
    finance = await login(FINANCE)
    codes = await _codes(api, finance)
    made = await api.post(
        "/finance/vendor-invoices",
        headers=finance,
        json=_body(vendor_id, _line(account_id=codes["5100"], quantity="1", rate=amount)),
    )
    assert made.status_code == 201, made.text
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
    return approved.json()  # type: ignore[no-any-return]


class TestBankAccounts:
    async def test_the_seeded_accounts_are_listed(self, api: AsyncClient, login: Any) -> None:
        rows = (
            await api.get(
                "/finance/bank-accounts", headers=await login(FINANCE), params={"limit": 50}
            )
        ).json()["items"]
        titles = {r["account_title"] for r in rows}
        assert {"Main Operating Account", "Cash Till"} <= titles
        till = next(r for r in rows if r["account_title"] == "Cash Till")
        assert till["gl_account_code"] == "1110"

    async def test_only_finance_coa_manage_may_create_or_edit(
        self, api: AsyncClient, login: Any
    ) -> None:
        codes = await _codes(api, await login(FINANCE))
        refused = await api.post(
            "/finance/bank-accounts",
            headers=await login(STAFF),
            json={
                "account_title": "Nope",
                "account_no": "X-1",
                "bank_name": "Nope Bank",
                "gl_account_id": codes["1120"],
            },
        )
        assert refused.status_code == 403

    async def test_a_duplicate_account_no_is_refused(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        codes = await _codes(api, admin)
        again = await api.post(
            "/finance/bank-accounts",
            headers=admin,
            json={
                "account_title": "Copy",
                "account_no": "0001-0000001",
                "bank_name": "Sample Bank Ltd",
                "gl_account_id": codes["1120"],
            },
        )
        assert again.status_code == 409

    async def test_an_account_can_be_edited_by_hand(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        codes = await _codes(api, admin)
        made = await api.post(
            "/finance/bank-accounts",
            headers=admin,
            json={
                "account_title": "Throwaway",
                "account_no": f"TEST-{utcnow().timestamp()}",
                "bank_name": "Test Bank",
                "gl_account_id": codes["1120"],
            },
        )
        assert made.status_code == 201, made.text
        account = made.json()
        edited = await api.patch(
            f"/finance/bank-accounts/{account['id']}",
            headers={**admin, "If-Match": str(account["version"])},
            json={"bank_name": "Renamed Bank", "is_active": False},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["bank_name"] == "Renamed Bank"
        assert edited.json()["is_active"] is False


class TestPaymentRequests:
    async def test_only_finance_payment_request_may_raise_one(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        refused = await api.post(
            "/finance/payment-requests",
            headers=await login(STAFF),
            json=_pr_body(keys["vendor"]),
        )
        assert refused.status_code == 403

    async def test_a_small_request_is_signed_by_finance_alone(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        request = await _approved_request(api, login, keys["vendor"], "5000")
        assert request["status"] == "APPROVED"
        assert request["approved_at"]

    async def test_a_large_request_needs_finance_then_the_executive(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        officer = await login(ACCOUNTS)
        made = await api.post(
            "/finance/payment-requests", headers=officer, json=_pr_body(keys["vendor"], "500000")
        )
        request = made.json()
        submitted = await api.post(
            f"/finance/payment-requests/{request['id']}/submit",
            headers={**officer, "If-Match": str(request["version"])},
        )
        assert submitted.status_code == 200, submitted.text
        pending = submitted.json()
        first = await _decide(api, await login(FINANCE), pending["approval_request_id"])
        assert first.status_code == 200, first.text
        mid = (await api.get(f"/finance/payment-requests/{request['id']}", headers=officer)).json()
        assert mid["status"] == "PENDING_APPROVAL"
        second = await _decide(api, await login(EXECUTIVE), pending["approval_request_id"])
        assert second.status_code == 200, second.text
        done = (await api.get(f"/finance/payment-requests/{request['id']}", headers=officer)).json()
        assert done["status"] == "APPROVED"

    async def test_a_rejected_request_is_a_draft_again_and_can_be_resubmitted(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        officer = await login(ACCOUNTS)
        made = await api.post(
            "/finance/payment-requests", headers=officer, json=_pr_body(keys["vendor"], "3000")
        )
        request = made.json()
        submitted = (
            await api.post(
                f"/finance/payment-requests/{request['id']}/submit",
                headers={**officer, "If-Match": str(request["version"])},
            )
        ).json()
        rejected = await _decide(
            api, await login(FINANCE), submitted["approval_request_id"], "reject"
        )
        assert rejected.status_code == 200, rejected.text
        back = (await api.get(f"/finance/payment-requests/{request['id']}", headers=officer)).json()
        assert back["status"] == "REJECTED" and back["can_edit"] is True

    async def test_a_draft_can_be_deleted(self, api: AsyncClient, login: Any) -> None:
        keys = await _keys(api, await login(ADMIN))
        officer = await login(ACCOUNTS)
        made = await api.post(
            "/finance/payment-requests", headers=officer, json=_pr_body(keys["vendor"], "1000")
        )
        request_id = made.json()["id"]
        deleted = await api.delete(f"/finance/payment-requests/{request_id}", headers=officer)
        assert deleted.status_code == 204
        gone = await api.get(f"/finance/payment-requests/{request_id}", headers=officer)
        assert gone.status_code == 404

    async def test_an_approved_request_can_be_cancelled(self, api: AsyncClient, login: Any) -> None:
        keys = await _keys(api, await login(ADMIN))
        request = await _approved_request(api, login, keys["vendor"], "2000")
        officer = await login(ACCOUNTS)
        cancelled = await api.post(
            f"/finance/payment-requests/{request['id']}/cancel",
            headers=officer,
            json={"reason": "No longer needed"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"


class TestPaymentExecution:
    async def test_execute_requires_an_approved_request(self, api: AsyncClient, login: Any) -> None:
        keys = await _keys(api, await login(ADMIN))
        finance = await login(FINANCE)
        officer = await login(ACCOUNTS)
        made = await api.post(
            "/finance/payment-requests", headers=officer, json=_pr_body(keys["vendor"], "1000")
        )
        request = made.json()
        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        refused = await api.post(
            "/finance/payments",
            headers=finance,
            json={
                "payment_request_id": request["id"],
                "payment_date": utcnow().date().isoformat(),
                "method": "BANK_TRANSFER",
                "bank_account_id": bank_id,
            },
        )
        assert refused.status_code == 422
        assert refused.json()["rule"] == "payment_request_not_approved"

    async def test_only_finance_payment_execute_may_execute(
        self, api: AsyncClient, login: Any
    ) -> None:
        keys = await _keys(api, await login(ADMIN))
        request = await _approved_request(api, login, keys["vendor"], "1000")
        officer = await login(ACCOUNTS)
        bank_id = await _bank_account_id(api, officer, "Main Operating Account")
        refused = await api.post(
            "/finance/payments",
            headers=officer,
            json={
                "payment_request_id": request["id"],
                "payment_date": utcnow().date().isoformat(),
                "method": "BANK_TRANSFER",
                "bank_account_id": bank_id,
            },
        )
        assert refused.status_code == 403

    async def test_executing_posts_the_gross_and_clears_accounts_payable(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))
        request = await _approved_request(api, login, keys["vendor"], "5000")

        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_2100 = next(
            (Decimal(r["debit"]) for r in before_tb["rows"] if r["account_id"] == codes["2100"]),
            Decimal(0),
        )
        before_1120 = next(
            (Decimal(r["credit"]) for r in before_tb["rows"] if r["account_id"] == codes["1120"]),
            Decimal(0),
        )

        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        made = await api.post(
            "/finance/payments",
            headers=finance,
            json={
                "payment_request_id": request["id"],
                "payment_date": utcnow().date().isoformat(),
                "method": "BANK_TRANSFER",
                "bank_account_id": bank_id,
                "instrument_no": "CHQ-0001",
            },
        )
        assert made.status_code == 201, made.text
        payment = made.json()
        assert payment["status"] == "ISSUED"
        assert Decimal(payment["gross_amount"]) == Decimal(5000)
        assert Decimal(payment["net_amount"]) == Decimal(5000)
        assert payment["journal_entry_id"] is not None

        after_pr = (
            await api.get(f"/finance/payment-requests/{request['id']}", headers=finance)
        ).json()
        assert after_pr["status"] == "PAID"

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_2100 = next(r for r in after_tb["rows"] if r["account_id"] == codes["2100"])
        after_1120 = next(r for r in after_tb["rows"] if r["account_id"] == codes["1120"])
        assert Decimal(after_2100["debit"]) == before_2100 + Decimal(5000)
        assert Decimal(after_1120["credit"]) == before_1120 + Decimal(5000)

    async def test_withholding_reduces_net_and_credits_the_withholding_account(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))
        request = await _approved_request(api, login, keys["vendor"], "10000")

        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_2200 = next(
            (Decimal(r["credit"]) for r in before_tb["rows"] if r["account_id"] == codes["2200"]),
            Decimal(0),
        )

        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        made = await api.post(
            "/finance/payments",
            headers=finance,
            json={
                "payment_request_id": request["id"],
                "payment_date": utcnow().date().isoformat(),
                "method": "BANK_TRANSFER",
                "bank_account_id": bank_id,
                "withholding_amount": "400",
            },
        )
        assert made.status_code == 201, made.text
        payment = made.json()
        assert Decimal(payment["gross_amount"]) == Decimal(10000)
        assert Decimal(payment["withholding_amount"]) == Decimal(400)
        assert Decimal(payment["net_amount"]) == Decimal(9600)

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_2200 = next(r for r in after_tb["rows"] if r["account_id"] == codes["2200"])
        assert Decimal(after_2200["credit"]) == before_2200 + Decimal(400)

    async def test_a_cash_payment_credits_the_cash_till(self, api: AsyncClient, login: Any) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))
        request = await _approved_request(api, login, keys["vendor"], "1500")

        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_1110 = next(
            (Decimal(r["credit"]) for r in before_tb["rows"] if r["account_id"] == codes["1110"]),
            Decimal(0),
        )

        bank_id = await _bank_account_id(api, finance, "Cash Till")
        made = await api.post(
            "/finance/payments",
            headers=finance,
            json={
                "payment_request_id": request["id"],
                "payment_date": utcnow().date().isoformat(),
                "method": "CASH",
                "bank_account_id": bank_id,
            },
        )
        assert made.status_code == 201, made.text

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_1110 = next(r for r in after_tb["rows"] if r["account_id"] == codes["1110"])
        assert Decimal(after_1110["credit"]) == before_1110 + Decimal(1500)


class TestAllocation:
    async def test_allocating_settles_the_invoice(self, api: AsyncClient, login: Any) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        invoice = await _approved_invoice(api, login, keys["vendor"], "4000")
        request = await _approved_request(api, login, keys["vendor"], "4000")
        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        payment = (
            await api.post(
                "/finance/payments",
                headers=finance,
                json={
                    "payment_request_id": request["id"],
                    "payment_date": utcnow().date().isoformat(),
                    "method": "BANK_TRANSFER",
                    "bank_account_id": bank_id,
                },
            )
        ).json()

        allocated = await api.post(
            f"/finance/payments/{payment['id']}/allocate",
            headers=finance,
            json={"items": [{"invoice_id": invoice["id"], "allocated_amount": "4000"}]},
        )
        assert allocated.status_code == 200, allocated.text
        assert Decimal(allocated.json()["allocated_amount"]) == Decimal(4000)
        assert len(allocated.json()["allocations"]) == 1

        after_invoice = (
            await api.get(f"/finance/vendor-invoices/{invoice['id']}", headers=finance)
        ).json()
        assert after_invoice["status"] == "PAID"
        assert Decimal(after_invoice["paid_amount"]) == Decimal(4000)

    async def test_allocating_more_than_the_net_amount_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        invoice = await _approved_invoice(api, login, keys["vendor"], "4000")
        request = await _approved_request(api, login, keys["vendor"], "1000")
        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        payment = (
            await api.post(
                "/finance/payments",
                headers=finance,
                json={
                    "payment_request_id": request["id"],
                    "payment_date": utcnow().date().isoformat(),
                    "method": "BANK_TRANSFER",
                    "bank_account_id": bank_id,
                },
            )
        ).json()

        refused = await api.post(
            f"/finance/payments/{payment['id']}/allocate",
            headers=finance,
            json={"items": [{"invoice_id": invoice["id"], "allocated_amount": "4000"}]},
        )
        assert refused.status_code == 422
        assert refused.json()["rule"] == "payment_allocation_exceeds_net"

    async def test_allocating_against_another_vendors_invoice_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        invoice = await _approved_invoice(api, login, keys["vendor2"], "1000")
        request = await _approved_request(api, login, keys["vendor"], "1000")
        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        payment = (
            await api.post(
                "/finance/payments",
                headers=finance,
                json={
                    "payment_request_id": request["id"],
                    "payment_date": utcnow().date().isoformat(),
                    "method": "BANK_TRANSFER",
                    "bank_account_id": bank_id,
                },
            )
        ).json()

        refused = await api.post(
            f"/finance/payments/{payment['id']}/allocate",
            headers=finance,
            json={"items": [{"invoice_id": invoice["id"], "allocated_amount": "1000"}]},
        )
        assert refused.status_code == 422
        assert refused.json()["rule"] == "payment_allocation_vendor_mismatch"


class TestClearAndCancel:
    async def test_mark_cleared(self, api: AsyncClient, login: Any) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        request = await _approved_request(api, login, keys["vendor"], "800")
        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        payment = (
            await api.post(
                "/finance/payments",
                headers=finance,
                json={
                    "payment_request_id": request["id"],
                    "payment_date": utcnow().date().isoformat(),
                    "method": "BANK_TRANSFER",
                    "bank_account_id": bank_id,
                },
            )
        ).json()
        cleared = await api.post(f"/finance/payments/{payment['id']}/mark-cleared", headers=finance)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["status"] == "CLEARED"

    async def test_cancelling_reverses_the_entry_and_reopens_what_it_settled(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        keys = await _keys(api, await login(ADMIN))
        invoice = await _approved_invoice(api, login, keys["vendor"], "2500")
        request = await _approved_request(api, login, keys["vendor"], "2500")
        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        payment = (
            await api.post(
                "/finance/payments",
                headers=finance,
                json={
                    "payment_request_id": request["id"],
                    "payment_date": utcnow().date().isoformat(),
                    "method": "BANK_TRANSFER",
                    "bank_account_id": bank_id,
                },
            )
        ).json()
        await api.post(
            f"/finance/payments/{payment['id']}/allocate",
            headers=finance,
            json={"items": [{"invoice_id": invoice["id"], "allocated_amount": "2500"}]},
        )

        before_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_2100 = next(
            (Decimal(r["debit"]) for r in before_tb["rows"] if r["account_id"] == codes["2100"]),
            Decimal(0),
        )

        cancelled = await api.post(
            f"/finance/payments/{payment['id']}/cancel",
            headers=finance,
            json={"reason": "Wrong vendor selected by mistake"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"

        after_invoice = (
            await api.get(f"/finance/vendor-invoices/{invoice['id']}", headers=finance)
        ).json()
        assert after_invoice["status"] == "APPROVED"
        assert Decimal(after_invoice["paid_amount"]) == Decimal(0)

        after_request = (
            await api.get(f"/finance/payment-requests/{request['id']}", headers=finance)
        ).json()
        assert after_request["status"] == "APPROVED"

        after_tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        after_2100 = next(
            (Decimal(r["debit"]) for r in after_tb["rows"] if r["account_id"] == codes["2100"]),
            Decimal(0),
        )
        # The reversal nets the debit straight back out.
        assert after_2100 <= before_2100

    async def test_only_finance_payment_execute_may_cancel(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        keys = await _keys(api, await login(ADMIN))
        request = await _approved_request(api, login, keys["vendor"], "600")
        bank_id = await _bank_account_id(api, finance, "Main Operating Account")
        payment = (
            await api.post(
                "/finance/payments",
                headers=finance,
                json={
                    "payment_request_id": request["id"],
                    "payment_date": utcnow().date().isoformat(),
                    "method": "BANK_TRANSFER",
                    "bank_account_id": bank_id,
                },
            )
        ).json()
        refused = await api.post(
            f"/finance/payments/{payment['id']}/cancel",
            headers=await login(ACCOUNTS),
            json={"reason": "Not the officer's call to make"},
        )
        assert refused.status_code == 403
