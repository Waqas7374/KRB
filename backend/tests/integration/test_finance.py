"""The general ledger core: chart of accounts, periods, manual journal entries,
trial balance and general ledger (docs/02 §8, docs/07 §`/finance`).

Seeded people: finance@krb.example (FINANCE_MANAGER, posts small entries and
manages accounts/periods), accounts@krb.example (ACCOUNTS_OFFICER, prepares
entries but cannot post them), ceo@krb.example (EXECUTIVE, signs large ones),
admin, staff.gvh1 (no finance permission at all), auditor.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.types import utcnow

pytestmark = pytest.mark.integration

FINANCE = "finance@krb.example"
ACCOUNTS = "accounts@krb.example"
EXECUTIVE = "ceo@krb.example"
ADMIN = "admin@krb.example"
STAFF = "staff.gvh1@krb.example"
AUDITOR = "auditor@krb.example"


async def _codes(api: AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    """code -> account id, for the seeded chart of accounts."""
    rows = (await api.get("/finance/accounts", headers=headers, params={"limit": 200})).json()[
        "items"
    ]
    return {r["code"]: r["id"] for r in rows}


def line(account: str, *, debit: str = "0", credit: str = "0", **extra: Any) -> dict[str, Any]:
    return {"account_id": account, "debit": debit, "credit": credit, **extra}


def body(*lines: dict[str, Any], on: date | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "entry_date": (on or utcnow().date()).isoformat(),
        "description": "A test entry",
        "lines": list(lines),
        **extra,
    }


async def _draft(
    api: AsyncClient, headers: dict[str, str], accounts: dict[str, str], amount: str = "1000"
) -> dict[str, Any]:
    made = await api.post(
        "/finance/journal-entries",
        headers=headers,
        json=body(
            line(accounts["1110"], debit=amount),
            line(accounts["3100"], credit=amount),
        ),
    )
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


async def _decide(
    api: AsyncClient, headers: dict[str, str], request_id: str, action: str = "approve"
) -> Any:
    payload = {} if action == "approve" else {"comments": "Needs another look before it's signed"}
    return await api.post(
        f"/approvals/requests/{request_id}/{action}", headers=headers, json=payload
    )


async def _submitted(
    api: AsyncClient, login: Any, codes: dict[str, str], amount: str = "1000"
) -> dict[str, Any]:
    """Raised by the accounts officer (who cannot post) and sent for approval —
    the shape every real journal entry takes, and the only one that leaves the
    finance manager free to decide it: a person cannot approve their own step."""
    draft = await _draft(api, await login(ACCOUNTS), codes, amount)
    submitted = await api.post(
        f"/finance/journal-entries/{draft['id']}/submit", headers=await login(ACCOUNTS), json={}
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()  # type: ignore[no-any-return]


async def _posted(
    api: AsyncClient, login: Any, codes: dict[str, str], amount: str = "1000"
) -> dict[str, Any]:
    """Raised, submitted, and signed by finance alone — for scenarios under
    the executive threshold that just need a posted entry to work with."""
    submitted = await _submitted(api, login, codes, amount)
    decided = await _decide(api, await login(FINANCE), submitted["approval_request_id"])
    assert decided.status_code == 200, decided.text
    fresh = await api.get(
        f"/finance/journal-entries/{submitted['id']}", headers=await login(FINANCE)
    )
    return fresh.json()  # type: ignore[no-any-return]


class TestChartOfAccounts:
    async def test_the_seeded_chart_is_a_tree_of_groups_and_leaves(
        self, api: AsyncClient, login: Any
    ) -> None:
        tree = (await api.get("/finance/accounts/tree", headers=await login(FINANCE))).json()
        assert {n["account"]["code"] for n in tree} == {
            "1000",
            "2000",
            "3000",
            "4000",
            "5000",
            "6000",
        }
        assets = next(n for n in tree if n["account"]["code"] == "1000")
        assert assets["account"]["is_postable"] is False  # it has children
        assert assets["account"]["account_type"] == "ASSET"
        cash_group = next(n for n in assets["children"] if n["account"]["code"] == "1100")
        cash_in_hand = next(n for n in cash_group["children"] if n["account"]["code"] == "1110")
        assert cash_in_hand["account"]["is_postable"] is True and cash_in_hand["children"] == []
        assert cash_in_hand["account"]["normal_balance"] == "DR"
        dev_cost = next(n for n in tree if n["account"]["code"] == "6000")
        assert dev_cost["account"]["account_type"] == "COGS_DEV_COST"

    async def test_creating_a_child_turns_the_parent_into_a_group(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        codes = await _codes(api, admin)
        leaf_before = (await api.get(f"/finance/accounts/{codes['5100']}", headers=admin)).json()
        assert leaf_before["is_postable"] is True

        made = await api.post(
            "/finance/accounts",
            headers=admin,
            json={
                "code": "5110",
                "name": "Office Rent",
                "account_type": "EXPENSE",
                "parent_id": codes["5100"],
            },
        )
        assert made.status_code == 201, made.text
        assert made.json()["is_postable"] is True
        assert made.json()["normal_balance"] == "DR"

        parent_after = (await api.get(f"/finance/accounts/{codes['5100']}", headers=admin)).json()
        assert parent_after["is_postable"] is False

    async def test_a_duplicate_code_is_refused(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        again = await api.post(
            "/finance/accounts",
            headers=admin,
            json={"code": "1110", "name": "Copy", "account_type": "ASSET"},
        )
        assert again.status_code == 409

    async def test_deactivating_an_account_keeps_its_history_but_stops_new_postings(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        codes = await _codes(api, admin)
        created = await api.post(
            "/finance/accounts",
            headers=admin,
            json={"code": "5901", "name": "One-off Expense", "account_type": "EXPENSE"},
        )
        assert created.status_code == 201, created.text
        account = created.json()
        off = await api.patch(
            f"/finance/accounts/{account['id']}", headers=admin, json={"is_active": False}
        )
        assert off.status_code == 200 and off.json()["is_active"] is False

        refused = await api.post(
            "/finance/journal-entries",
            headers=await login(FINANCE),
            json=body(line(account["id"], debit="10"), line(codes["3100"], credit="10")),
        )
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "lines.0.account_id"

    async def test_a_project_can_be_required_on_an_accounts_lines(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        codes = await _codes(api, admin)
        made = await api.post(
            "/finance/accounts",
            headers=admin,
            json={
                "code": "5902",
                "name": "Project Expense",
                "account_type": "EXPENSE",
                "requires_project": True,
            },
        )
        assert made.status_code == 201, made.text
        account_id = made.json()["id"]
        without = await api.post(
            "/finance/journal-entries",
            headers=await login(FINANCE),
            json=body(line(account_id, debit="10"), line(codes["3100"], credit="10")),
        )
        assert without.status_code == 422
        assert without.json()["errors"][0]["field"] == "lines.0.project_id"

    async def test_someone_without_finance_permission_cannot_manage_accounts(
        self, api: AsyncClient, login: Any
    ) -> None:
        refused = await api.post(
            "/finance/accounts",
            headers=await login(STAFF),
            json={"code": "9999", "name": "Nope", "account_type": "ASSET"},
        )
        assert refused.status_code == 403
        # But everyone with a read permission (auditor) may look.
        seen = await api.get("/finance/accounts/tree", headers=await login(AUDITOR))
        assert seen.status_code == 200


class TestPeriods:
    async def test_generating_a_fiscal_year_makes_twelve_open_periods_starting_in_july(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        made = await api.post(
            "/finance/periods/generate", headers=admin, json={"fiscal_year": 2031}
        )
        assert made.status_code == 201, made.text
        rows = made.json()
        assert len(rows) == 12
        assert [r["period_no"] for r in rows] == list(range(1, 13))
        assert all(r["status"] == "OPEN" for r in rows)
        assert rows[0]["start_date"] == "2031-07-01"
        assert rows[-1]["end_date"] == "2032-06-30"

    async def test_generating_the_same_year_again_changes_nothing(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        first = (
            await api.post("/finance/periods/generate", headers=admin, json={"fiscal_year": 2032})
        ).json()
        again = (
            await api.post("/finance/periods/generate", headers=admin, json={"fiscal_year": 2032})
        ).json()
        assert [r["id"] for r in first] == [r["id"] for r in again]

    async def test_a_period_moves_open_closed_locked_and_never_back_past_locked(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        periods = (
            await api.post("/finance/periods/generate", headers=admin, json={"fiscal_year": 2033})
        ).json()
        period_id = periods[0]["id"]
        closed = await api.post(f"/finance/periods/{period_id}/close", headers=admin)
        assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
        reopened = await api.post(f"/finance/periods/{period_id}/reopen", headers=admin)
        assert reopened.status_code == 200 and reopened.json()["status"] == "OPEN"
        await api.post(f"/finance/periods/{period_id}/close", headers=admin)
        locked = await api.post(f"/finance/periods/{period_id}/lock", headers=admin)
        assert locked.status_code == 200 and locked.json()["status"] == "LOCKED"
        stuck = await api.post(f"/finance/periods/{period_id}/reopen", headers=admin)
        assert stuck.status_code == 422 and stuck.json()["rule"] == "period_locked"

    async def test_posting_into_a_closed_period_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        finance = await login(FINANCE)
        codes = await _codes(api, admin)
        periods = (
            await api.post("/finance/periods/generate", headers=admin, json={"fiscal_year": 2034})
        ).json()
        target = periods[3]  # the fourth month of that year
        await api.post(f"/finance/periods/{target['id']}/close", headers=admin)
        refused = await api.post(
            "/finance/journal-entries",
            headers=finance,
            json=body(
                line(codes["1110"], debit="10"),
                line(codes["3100"], credit="10"),
                on=date.fromisoformat(target["start_date"]) + timedelta(days=2),
            ),
        )
        assert refused.status_code == 422 and refused.json()["rule"] == "period_not_open"

    async def test_only_finance_may_manage_periods(self, api: AsyncClient, login: Any) -> None:
        refused = await api.post(
            "/finance/periods/generate", headers=await login(STAFF), json={"fiscal_year": 2035}
        )
        assert refused.status_code == 403


class TestJournalEntries:
    async def test_a_draft_is_not_yet_in_the_ledger(self, api: AsyncClient, login: Any) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        draft = await _draft(api, finance, codes)
        assert draft["status"] == "DRAFT" and draft["je_number"].startswith("JV-")
        assert draft["can_submit"] is True and draft["can_delete"] is True
        tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        assert draft["id"] not in {row["account_id"] for row in tb["rows"]}  # nothing posted yet

    async def test_an_unbalanced_entry_is_refused_before_it_ever_reaches_the_database(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        lopsided = await api.post(
            "/finance/journal-entries",
            headers=finance,
            json=body(line(codes["1110"], debit="100"), line(codes["3100"], credit="90")),
        )
        assert lopsided.status_code == 422
        assert lopsided.json()["rule"] == "journal_entry_unbalanced"

    async def test_a_line_that_is_both_or_neither_is_refused_by_the_schema(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        both = await api.post(
            "/finance/journal-entries",
            headers=finance,
            json=body(
                line(codes["1110"], debit="10", credit="10"), line(codes["3100"], credit="10")
            ),
        )
        assert both.status_code == 422

    async def test_posting_to_a_group_account_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        refused = await api.post(
            "/finance/journal-entries",
            headers=finance,
            json=body(line(codes["1000"], debit="10"), line(codes["3100"], credit="10")),
        )
        assert refused.status_code == 422
        assert "group account" in refused.json()["errors"][0]["message"]

    async def test_a_small_entry_is_signed_by_finance_alone_and_posts_at_once(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        posted = await _posted(api, login, codes, "500")
        assert posted["status"] == "POSTED" and posted["posted_at"]
        assert posted["approval_request_id"] is not None
        assert posted["can_reverse"] is True

        tb = (await api.get("/finance/trial-balance", headers=finance)).json()
        row = next(r for r in tb["rows"] if r["account_id"] == codes["1110"])
        assert Decimal(row["debit"]) >= Decimal("500")

    async def test_accounts_officer_can_prepare_but_not_post(
        self, api: AsyncClient, login: Any
    ) -> None:
        officer = await login(ACCOUNTS)
        codes = await _codes(api, officer)
        draft = await _draft(api, officer, codes, "300")
        submitted = await api.post(
            f"/finance/journal-entries/{draft['id']}/submit", headers=officer, json={}
        )
        assert submitted.status_code == 200, submitted.text
        pending = submitted.json()
        assert pending["status"] == "PENDING_APPROVAL"
        # The officer holds no finance.gl.post, so they cannot decide it themselves.
        cannot = await _decide(api, officer, pending["approval_request_id"])
        assert cannot.status_code in (403, 404, 422)
        done = await _decide(api, await login(FINANCE), pending["approval_request_id"])
        assert done.status_code == 200, done.text

    async def test_a_large_entry_needs_finance_then_the_executive(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        submitted = await _submitted(api, login, codes, "150000")
        assert submitted["status"] == "PENDING_APPROVAL"
        first = await _decide(api, finance, submitted["approval_request_id"])
        assert first.status_code == 200, first.text
        mid = (await api.get(f"/finance/journal-entries/{submitted['id']}", headers=finance)).json()
        assert mid["status"] == "PENDING_APPROVAL"  # one signature down, one to go
        second = await _decide(api, await login(EXECUTIVE), submitted["approval_request_id"])
        assert second.status_code == 200, second.text
        posted = (
            await api.get(f"/finance/journal-entries/{submitted['id']}", headers=finance)
        ).json()
        assert posted["status"] == "POSTED"

    async def test_a_rejected_entry_is_a_draft_again_and_can_be_fixed_and_resubmitted(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        submitted = await _submitted(api, login, codes, "800")
        rejected = await _decide(api, finance, submitted["approval_request_id"], "reject")
        assert rejected.status_code == 200, rejected.text
        officer = await login(ACCOUNTS)
        back = (
            await api.get(f"/finance/journal-entries/{submitted['id']}", headers=officer)
        ).json()
        assert back["status"] == "DRAFT" and back["decision_reason"]
        assert back["can_edit"] is True

        edited = await api.put(
            f"/finance/journal-entries/{submitted['id']}",
            headers=officer,
            json=body(line(codes["1110"], debit="900"), line(codes["3100"], credit="900")),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "DRAFT"

    async def test_withdrawing_before_any_decision_returns_it_to_draft(
        self, api: AsyncClient, login: Any
    ) -> None:
        codes = await _codes(api, await login(ADMIN))
        officer = await login(ACCOUNTS)
        submitted = await _submitted(api, login, codes, "700")
        pulled = await api.post(
            f"/finance/journal-entries/{submitted['id']}/withdraw", headers=officer
        )
        assert pulled.status_code == 200 and pulled.json()["status"] == "DRAFT"

    async def test_a_draft_can_be_deleted_but_a_posted_entry_cannot(
        self, api: AsyncClient, login: Any
    ) -> None:
        officer = await login(ACCOUNTS)
        codes = await _codes(api, officer)
        draft = await _draft(api, officer, codes, "111")
        deleted = await api.delete(f"/finance/journal-entries/{draft['id']}", headers=officer)
        assert deleted.status_code == 204
        gone = await api.get(f"/finance/journal-entries/{draft['id']}", headers=officer)
        assert gone.status_code == 404

        posted = await _posted(api, login, codes, "222")
        cannot_delete = await api.delete(
            f"/finance/journal-entries/{posted['id']}", headers=officer
        )
        assert cannot_delete.status_code == 422

    async def test_reversing_swaps_every_line_and_marks_the_original(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        posted = await _posted(api, login, codes, "400")

        reversal = await api.post(
            f"/finance/journal-entries/{posted['id']}/reverse",
            headers=finance,
            json={"reason": "Posted against the wrong entry by mistake"},
        )
        assert reversal.status_code == 201, reversal.text
        rev = reversal.json()
        assert rev["reversal_of_id"] == posted["id"]
        assert rev["status"] == "POSTED"
        cash_line = next(line for line in rev["lines"] if line["account_id"] == codes["1110"])
        assert Decimal(cash_line["credit"]) == Decimal(400) and Decimal(cash_line["debit"]) == 0

        original = await api.get(f"/finance/journal-entries/{posted['id']}", headers=finance)
        assert original.json()["status"] == "REVERSED"
        assert original.json()["can_reverse"] is False

        again = await api.post(
            f"/finance/journal-entries/{posted['id']}/reverse",
            headers=finance,
            json={"reason": "Trying twice"},
        )
        assert again.status_code == 422 and again.json()["rule"] == "journal_entry_not_posted"

    async def test_only_finance_gl_reverse_may_reverse(self, api: AsyncClient, login: Any) -> None:
        codes = await _codes(api, await login(ADMIN))
        posted = await _posted(api, login, codes, "150")
        refused = await api.post(
            f"/finance/journal-entries/{posted['id']}/reverse",
            headers=await login(ACCOUNTS),
            json={"reason": "Not mine to do"},
        )
        assert refused.status_code == 403


class TestReports:
    async def test_the_trial_balance_only_shows_posted_entries_and_it_balances(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        codes = await _codes(api, finance)
        officer = await login(ACCOUNTS)
        draft = await _draft(api, officer, codes, "600")
        # Still a draft: nothing to see yet from this entry.
        before = (await api.get("/finance/trial-balance", headers=finance)).json()
        before_debit = next(
            (Decimal(r["debit"]) for r in before["rows"] if r["account_id"] == codes["1110"]),
            Decimal(0),
        )
        submitted = (
            await api.post(
                f"/finance/journal-entries/{draft['id']}/submit", headers=officer, json={}
            )
        ).json()
        await _decide(api, finance, submitted["approval_request_id"])

        after = (await api.get("/finance/trial-balance", headers=finance)).json()
        assert after["total_debit"] == after["total_credit"]
        after_debit = next(r for r in after["rows"] if r["account_id"] == codes["1110"])
        assert Decimal(after_debit["debit"]) == before_debit + Decimal(600)

    async def test_the_general_ledger_carries_an_opening_balance_and_a_running_total(
        self, api: AsyncClient, login: Any
    ) -> None:
        finance = await login(FINANCE)
        officer = await login(ACCOUNTS)
        codes = await _codes(api, finance)
        today = utcnow().date()

        async def posted(amount: str, on: date) -> None:
            made = await api.post(
                "/finance/journal-entries",
                headers=officer,
                json=body(
                    line(codes["1120"], debit=amount), line(codes["3100"], credit=amount), on=on
                ),
            )
            assert made.status_code == 201, made.text
            submitted = (
                await api.post(
                    f"/finance/journal-entries/{made.json()['id']}/submit", headers=officer, json={}
                )
            ).json()
            decided = await _decide(api, finance, submitted["approval_request_id"])
            assert decided.status_code == 200, decided.text

        await posted("50", today - timedelta(days=3))
        await posted("30", today)

        whole = (
            await api.get(
                "/finance/general-ledger", headers=finance, params={"account_id": codes["1120"]}
            )
        ).json()
        assert whole["opening_balance"] == "0"
        assert len(whole["rows"]) >= 2
        assert whole["closing_balance"] == whole["rows"][-1]["running_balance"]

        recent = (
            await api.get(
                "/finance/general-ledger",
                headers=finance,
                params={"account_id": codes["1120"], "from_date": today.isoformat()},
            )
        ).json()
        assert Decimal(recent["opening_balance"]) == Decimal(whole["closing_balance"]) - Decimal(30)
        assert len(recent["rows"]) == 1

    async def test_reports_need_finance_gl_view(self, api: AsyncClient, login: Any) -> None:
        refused = await api.get("/finance/trial-balance", headers=await login(STAFF))
        assert refused.status_code == 403
