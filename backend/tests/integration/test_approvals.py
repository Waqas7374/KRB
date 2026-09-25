"""The approval engine end to end, through purchase requests.

docs/10 Phase 2 "done when": the §25 three-tier PR example routes correctly at
50 000 / 500 000 / 5 000 000, and editing a workflow does not change an
in-flight request. Plus the rules docs/04 §6 lists: self-approval blocked,
rejection resets the document, a changed document auto-recalls, an ineligible
user is refused even with the permission bit.

Seeded people used here:
  sm.gvh1   Imran Shah     site manager, GVH-S1 — raises the requests
  pm.gvh    Bilal Ahmad    project manager of GVH
  procurement Ahmed Raza   procurement manager (company)
  finance   Nadia Hussain  finance manager (company)
  ceo       Kamran Rauf    executive (company)
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.modules.approvals.models import ApprovalAction, ApprovalRequestStep
from app.modules.approvals.services import engine
from app.modules.procurement.models import PurchaseRequest

pytestmark = pytest.mark.integration

REQUESTER = "sm.gvh1@krb.example"
PM = "pm.gvh@krb.example"
PROCUREMENT = "procurement@krb.example"
FINANCE = "finance@krb.example"
EXECUTIVE = "ceo@krb.example"
AUDITOR = "auditor@krb.example"
ADMIN = "admin@krb.example"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


async def _place(api: AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    projects = (await api.get("/projects", headers=headers, params={"q": "GVH"})).json()["items"]
    project = next(p for p in projects if p["code"] == "GVH")
    sites = (await api.get("/sites", headers=headers, params={"project_id": project["id"]})).json()
    site = next(s for s in sites["items"] if s["code"] == "GVH-S1")
    return {"project_id": project["id"], "site_id": site["id"]}


async def _material(api: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    items = (await api.get("/materials", headers=headers, params={"limit": 50})).json()["items"]
    return next(m for m in items if m["is_purchasable"])


async def _new_pr(
    api: AsyncClient,
    headers: dict[str, str],
    amount: str,
    *,
    submit: bool = True,
) -> dict[str, Any]:
    """A one-line request whose estimate is exactly `amount`."""
    place = await _place(api, headers)
    material = await _material(api, headers)
    body = {
        **place,
        "justification": f"Material for Block A works ({amount})",
        "items": [
            {
                "material_id": material["id"],
                "unit_id": material["base_unit_id"],
                "quantity": "1",
                "estimated_rate": amount,
            }
        ],
    }
    created = await api.post("/purchase-requests", headers=headers, json=body)
    assert created.status_code == 201, created.text
    pr = created.json()
    assert Decimal(pr["estimated_amount"]) == Decimal(amount)
    if not submit:
        return pr
    submitted = await api.post(f"/purchase-requests/{pr['id']}/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    return submitted.json()


async def _trail(api: AsyncClient, headers: dict[str, str], pr_id: str) -> list[dict[str, Any]]:
    response = await api.get(
        "/approvals/requests",
        headers=headers,
        params={"doc_type": "purchase_request", "doc_id": pr_id},
    )
    assert response.status_code == 200, response.text
    return list(response.json())


async def _user_id(api: AsyncClient, admin: dict[str, str], email: str) -> str:
    users = (await api.get("/users", headers=admin, params={"q": email})).json()["items"]
    return str(next(u for u in users if u["email"] == email)["id"])


async def _decide(
    api: AsyncClient,
    headers: dict[str, str],
    request_id: str,
    verb: str,
    comments: str | None = None,
) -> Any:
    return await api.post(
        f"/approvals/requests/{request_id}/{verb}", headers=headers, json={"comments": comments}
    )


# -----------------------------------------------------------------------------
# The Phase 2 "done when"
# -----------------------------------------------------------------------------


class TestSection25Routing:
    @pytest.mark.parametrize(
        ("amount", "rule", "chain"),
        [
            ("50000", "Under 100,000", ["Project manager"]),
            ("500000", "100,000 to under 1,000,000", ["Project manager", "Procurement manager"]),
            (
                "5000000",
                "1,000,000 and above",
                ["Project manager", "Procurement manager", "Finance manager", "Executive"],
            ),
        ],
    )
    async def test_the_three_tiers_route_to_the_right_chain(
        self, api: AsyncClient, login: Any, amount: str, rule: str, chain: list[str]
    ) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, amount)
        assert pr["status"] == "PENDING_APPROVAL"

        [request] = await _trail(api, requester, pr["id"])
        assert request["rule_name"] == rule
        assert [s["name"] for s in request["steps"]] == chain
        assert request["steps"][0]["status"] == "PENDING"
        assert all(s["status"] == "WAITING" for s in request["steps"][1:])
        # Step 1 is the project's manager, resolved from the document.
        assert [a["full_name"] for a in request["steps"][0]["approvers"]] == ["Bilal Ahmad"]

    async def test_a_5m_request_walks_the_whole_chain_in_order(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "5000000")
        request_id = pr["approval_request_id"]

        chain = [
            (PM, "Bilal Ahmad"),
            (PROCUREMENT, "Ahmed Raza"),
            (FINANCE, "Nadia Hussain"),
            (EXECUTIVE, "Kamran Rauf"),
        ]
        for position, (email, _name) in enumerate(chain):
            headers = await login(email)
            inbox = (await api.get("/approvals/inbox", headers=headers)).json()["items"]
            assert request_id in {i["request_id"] for i in inbox}, f"{email} should see it"

            # Everyone later in the chain is refused until it is their turn.
            for later_email, _ in chain[position + 1 :]:
                later = await login(later_email)
                refused = await _decide(api, later, request_id, "approve")
                assert refused.status_code == 403, later_email

            decided = await _decide(api, headers, request_id, "approve", "Checked against the BOQ")
            assert decided.status_code == 200, decided.text

        final = (await api.get(f"/purchase-requests/{pr['id']}", headers=requester)).json()
        assert final["status"] == "APPROVED"
        assert final["approved_at"] is not None
        [request] = await _trail(api, requester, pr["id"])
        assert request["status"] == "APPROVED"
        assert [a["actor_name"] for a in request["actions"] if a["action"] == "APPROVE"] == [
            name for _, name in chain
        ]

    async def test_editing_the_workflow_does_not_change_an_in_flight_request(
        self, api: AsyncClient, login: Any
    ) -> None:
        requester = await login(REQUESTER)
        admin = await login(ADMIN)
        in_flight = await _new_pr(api, requester, "500000")

        # The administrator replaces the workflow: everything now goes to the
        # executive alone.
        published = await api.post(
            "/approval-workflows",
            headers=admin,
            json={
                "doc_type": "purchase_request",
                "name": "Executive-only (test)",
                "definition": {
                    "rules": [
                        {
                            "sequence": 1,
                            "condition": True,
                            "steps": [
                                {
                                    "step_no": 1,
                                    "name": "Executive",
                                    "approver_type": "ROLE",
                                    "approver_ref": "EXECUTIVE",
                                }
                            ],
                        }
                    ]
                },
                "change_note": "test",
            },
        )
        assert published.status_code == 201, published.text
        assert published.json()["version"] == 2

        # The in-flight request still has its original two steps and approvers.
        [old] = await _trail(api, requester, in_flight["id"])
        assert old["workflow_version"] == 1
        assert [s["name"] for s in old["steps"]] == ["Project manager", "Procurement manager"]
        for email in (PM, PROCUREMENT):
            assert (await _decide(api, await login(email), old["id"], "approve")).status_code == 200
        [old] = await _trail(api, requester, in_flight["id"])
        assert old["status"] == "APPROVED"

        # A request submitted now follows version 2.
        fresh = await _new_pr(api, requester, "500000")
        [new] = await _trail(api, requester, fresh["id"])
        assert new["workflow_version"] == 2
        assert [s["name"] for s in new["steps"]] == ["Executive"]


# -----------------------------------------------------------------------------
# docs/04 §6 rules
# -----------------------------------------------------------------------------


class TestEligibility:
    async def test_the_submitter_is_never_their_own_approver(
        self, api: AsyncClient, login: Any
    ) -> None:
        """The PM raises a request on their own project: they are skipped, and
        the step's escalation target (procurement) receives it instead of it
        being silently auto-approved."""
        pm = await login(PM)
        pr = await _new_pr(api, pm, "50000")
        [request] = await _trail(api, pm, pr["id"])
        approvers = request["steps"][0]["approvers"]
        assert [a["full_name"] for a in approvers] == ["Ahmed Raza"]
        assert approvers[0]["source"] == "ESCALATION"
        assert (await _decide(api, pm, request["id"], "approve")).status_code == 403

    async def test_an_ineligible_user_is_refused_even_with_the_permission(
        self, api: AsyncClient, login: Any
    ) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "50000")
        # Finance holds procurement.pr.approve but is not on step 1.
        finance = await login(FINANCE)
        response = await _decide(api, finance, pr["approval_request_id"], "approve")
        assert response.status_code == 403

    async def test_an_auditor_can_read_the_trail_but_not_decide(
        self, api: AsyncClient, login: Any
    ) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "50000")
        auditor = await login(AUDITOR)
        trail = await _trail(api, auditor, pr["id"])
        assert len(trail) == 1
        assert trail[0]["can_decide"] is False
        assert (
            await _decide(api, auditor, pr["approval_request_id"], "approve")
        ).status_code == 403


class TestOutcomes:
    async def test_rejection_returns_the_document_and_resubmission_is_a_new_request(
        self, api: AsyncClient, login: Any
    ) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "50000")
        pm = await login(PM)

        short = await _decide(api, pm, pr["approval_request_id"], "reject", "no")
        assert short.status_code == 422  # a rejection needs a real reason

        rejected = await _decide(
            api, pm, pr["approval_request_id"], "reject", "Quantity is double the BOQ"
        )
        assert rejected.status_code == 200
        state = (await api.get(f"/purchase-requests/{pr['id']}", headers=requester)).json()
        assert state["status"] == "REJECTED"
        assert state["decision_reason"] == "Quantity is double the BOQ"
        assert state["can_edit"] and state["can_submit"]

        resubmitted = await api.post(f"/purchase-requests/{pr['id']}/submit", headers=requester)
        assert resubmitted.status_code == 200
        trail = await _trail(api, requester, pr["id"])
        assert [r["status"] for r in trail] == ["PENDING", "REJECTED"]  # newest first
        assert trail[0]["id"] != trail[1]["id"]

    async def test_changes_requested_then_a_bigger_edit_is_routed_afresh(
        self, api: AsyncClient, login: Any
    ) -> None:
        """Why CHANGES_REQUESTED ends the request: the edit can move the
        document into a stricter tier, which the old snapshot knows nothing of."""
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "90000")
        pm = await login(PM)
        returned = await _decide(
            api, pm, pr["approval_request_id"], "request-changes", "Add the drainage pipes too"
        )
        assert returned.status_code == 200

        current = (await api.get(f"/purchase-requests/{pr['id']}", headers=requester)).json()
        assert current["status"] == "CHANGES_REQUESTED"
        line = current["items"][0]
        edited = await api.put(
            f"/purchase-requests/{pr['id']}",
            headers={**requester, "If-Match": str(current["version"])},
            json={
                "project_id": current["project_id"],
                "site_id": current["site_id"],
                "justification": current["justification"],
                "items": [
                    {
                        "material_id": line["material_id"],
                        "unit_id": line["unit_id"],
                        "quantity": "10",
                        "estimated_rate": "90000",
                    }
                ],
            },
        )
        assert edited.status_code == 200, edited.text
        assert Decimal(edited.json()["estimated_amount"]) == Decimal("900000")

        await api.post(f"/purchase-requests/{pr['id']}/submit", headers=requester)
        newest = (await _trail(api, requester, pr["id"]))[0]
        assert [s["name"] for s in newest["steps"]] == ["Project manager", "Procurement manager"]

    async def test_a_document_changed_while_pending_is_recalled_not_approved(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "50000")
        # A change that bypassed the edit lock (a future code path, a data
        # fix) — the hash taken at submission no longer matches.
        await db.execute(
            update(PurchaseRequest)
            .where(PurchaseRequest.id == UUID(pr["id"]))
            .values(justification="Quietly changed after submission")
        )
        pm = await login(PM)
        response = await _decide(api, pm, pr["approval_request_id"], "approve")
        assert response.status_code == 200
        body = response.json()
        assert body["auto_recalled"] is True
        assert body["request"]["status"] == "RECALLED"
        state = (await api.get(f"/purchase-requests/{pr['id']}", headers=requester)).json()
        assert state["status"] == "DRAFT"

    async def test_the_submitter_can_recall_only_before_anyone_approves(
        self, api: AsyncClient, login: Any
    ) -> None:
        requester = await login(REQUESTER)
        early = await _new_pr(api, requester, "50000")
        recalled = await api.post(
            f"/approvals/requests/{early['approval_request_id']}/recall",
            headers=requester,
            json={"comments": "Wrong site"},
        )
        assert recalled.status_code == 200
        assert (await api.get(f"/purchase-requests/{early['id']}", headers=requester)).json()[
            "status"
        ] == "DRAFT"

        late = await _new_pr(api, requester, "500000")
        await _decide(api, await login(PM), late["approval_request_id"], "approve")
        refused = await api.post(
            f"/approvals/requests/{late['approval_request_id']}/recall", headers=requester, json={}
        )
        assert refused.status_code == 422


class TestSubmissionGuards:
    async def test_unpriced_lines_cannot_be_submitted(self, api: AsyncClient, login: Any) -> None:
        """Otherwise a large request could be routed as if it cost nothing."""
        requester = await login(REQUESTER)
        place = await _place(api, requester)
        material = await _material(api, requester)
        created = await api.post(
            "/purchase-requests",
            headers=requester,
            json={
                **place,
                "justification": "Rates to follow",
                "items": [
                    {
                        "material_id": material["id"],
                        "unit_id": material["base_unit_id"],
                        "quantity": "500",
                    }
                ],
            },
        )
        assert created.status_code == 201
        response = await api.post(
            f"/purchase-requests/{created.json()['id']}/submit", headers=requester
        )
        assert response.status_code == 422
        assert "estimated rate" in response.json()["detail"]

    async def test_no_workflow_means_no_submission_not_auto_approval(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        [active] = (
            await api.get(
                "/approval-workflows", headers=admin, params={"doc_type": "purchase_request"}
            )
        ).json()
        assert (
            await api.post(f"/approval-workflows/{active['id']}/deactivate", headers=admin)
        ).status_code == 200

        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "50000", submit=False)
        response = await api.post(f"/purchase-requests/{pr['id']}/submit", headers=requester)
        assert response.status_code == 422
        assert response.json()["rule"] == "approval_workflow_missing"
        state = (await api.get(f"/purchase-requests/{pr['id']}", headers=requester)).json()
        assert state["status"] == "DRAFT"  # the failed submit left nothing behind

    async def test_a_pending_request_cannot_be_edited(self, api: AsyncClient, login: Any) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "50000")
        response = await api.put(
            f"/purchase-requests/{pr['id']}",
            headers=requester,
            json={
                "project_id": pr["project_id"],
                "justification": "Changing it mid-approval",
                "items": [
                    {
                        "material_id": pr["items"][0]["material_id"],
                        "unit_id": pr["items"][0]["unit_id"],
                        "quantity": "1",
                        "estimated_rate": "10",
                    }
                ],
            },
        )
        assert response.status_code == 422
        assert "Recall it from approval first" in response.json()["detail"]


class TestWorkflowConfiguration:
    async def test_a_role_that_cannot_approve_is_refused_at_save_time(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        response = await api.post(
            "/approval-workflows",
            headers=admin,
            json={
                "doc_type": "purchase_request",
                "name": "Broken",
                "definition": {
                    "rules": [
                        {
                            "sequence": 1,
                            "condition": True,
                            "steps": [
                                {
                                    "step_no": 1,
                                    "name": "Staff",
                                    "approver_type": "ROLE",
                                    "approver_ref": "SITE_STAFF",
                                }
                            ],
                        }
                    ]
                },
            },
        )
        assert response.status_code == 422
        messages = " ".join(e["message"] for e in response.json()["errors"])
        assert "lacks procurement.pr.approve" in messages

    async def test_a_misspelt_variable_is_refused_at_save_time(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        response = await api.post(
            "/approval-workflows",
            headers=admin,
            json={
                "doc_type": "purchase_request",
                "name": "Typo",
                "definition": {
                    "rules": [
                        {
                            "sequence": 1,
                            "condition": {">": [{"var": "totl_amount"}, 5]},
                            "steps": [],
                        },
                        {"sequence": 2, "condition": True, "steps": []},
                    ]
                },
            },
        )
        assert response.status_code == 422
        assert "unknown variable 'totl_amount'" in response.text

    async def test_only_workflow_administrators_can_publish(
        self, api: AsyncClient, login: Any
    ) -> None:
        procurement = await login(PROCUREMENT)
        response = await api.post(
            "/approval-workflows",
            headers=procurement,
            json={"doc_type": "purchase_request", "name": "Mine", "definition": {"rules": []}},
        )
        assert response.status_code == 403

    async def test_the_simulator_shows_the_chain_before_anyone_depends_on_it(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        response = await api.post(
            "/approval-workflows/simulate",
            headers=admin,
            json={"doc_type": "purchase_request", "context": {"total_amount": 2500000}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rule_name"] == "1,000,000 and above"
        assert [s["name"] for s in body["steps"]] == [
            "Project manager",
            "Procurement manager",
            "Finance manager",
            "Executive",
        ]


class TestEscalationAndTrail:
    async def test_an_overdue_step_escalates_once_to_its_target(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "500000")
        pm = await login(PM)
        await _decide(api, pm, pr["approval_request_id"], "approve")  # now at procurement

        request_id = UUID(pr["approval_request_id"])
        await db.execute(
            update(ApprovalRequestStep)
            .where(ApprovalRequestStep.request_id == request_id, ApprovalRequestStep.step_no == 2)
            .values(due_at=utcnow() - timedelta(hours=1))
        )
        assert await engine.escalate_overdue(db) >= 1
        assert await engine.escalate_overdue(db) == 0  # once only

        [request] = await _trail(api, requester, pr["id"])
        step = request["steps"][1]
        assert step["escalated_at"] is not None
        added = [a for a in step["approvers"] if a["source"] == "ESCALATION"]
        assert [a["full_name"] for a in added] == ["Nadia Hussain"]  # FINANCE_MANAGER
        assert any(a["action"] == "ESCALATE" for a in request["actions"])

        # The escalation target can now decide it.
        finance = await login(FINANCE)
        assert request["id"] in {
            i["request_id"]
            for i in (await api.get("/approvals/inbox", headers=finance)).json()["items"]
        }

    async def test_the_approval_trail_is_append_only(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        requester = await login(REQUESTER)
        pr = await _new_pr(api, requester, "50000")
        action_id = (
            await db.execute(
                select(ApprovalAction.id).where(
                    ApprovalAction.request_id == UUID(pr["approval_request_id"])
                )
            )
        ).scalar_one()
        with pytest.raises(Exception, match="append-only"):
            async with db.begin_nested():
                await db.execute(
                    text("UPDATE approval_actions SET comments = 'rewritten' WHERE id = :id"),
                    {"id": action_id},
                )
