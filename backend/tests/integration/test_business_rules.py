"""Business rules end to end: the API, the resolver against the database, and
approval limits enforced by the approval engine.

Seeded people: admin (super administrator), auditor (reads everything, writes
nothing), sm.gvh1 (site manager), pm.gvh (project manager of GVH), procurement
(procurement manager), finance (finance manager).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow

pytestmark = pytest.mark.integration

ADMIN = "admin@krb.example"
AUDITOR = "auditor@krb.example"
REQUESTER = "sm.gvh1@krb.example"
PM = "pm.gvh@krb.example"
PROCUREMENT = "procurement@krb.example"
FINANCE = "finance@krb.example"

TODAY = utcnow().date()


def _tonnage(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "rule_type": "TONNAGE_MAX",
        "name": "Test tonnage",
        "value": {"max": "16"},
        "effective_from": (TODAY - timedelta(days=30)).isoformat(),
    }
    body.update(overrides)
    return body


async def _role_id(api: AsyncClient, headers: dict[str, str], code: str) -> str:
    body = (await api.get("/roles", headers=headers, params={"q": code, "limit": 5})).json()
    return str(next(r for r in body["items"] if r["code"] == code)["id"])


class TestApi:
    async def test_seeded_defaults_exist_and_are_readable(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        rows = (await api.get("/business-rules", headers=admin, params={"limit": 50})).json()[
            "items"
        ]
        types = {r["rule_type"] for r in rows if r["scope"] == {}}
        assert {
            "GEOFENCE_RADIUS",
            "QTY_TOLERANCE",
            "PRICE_TOLERANCE",
            "CLOCK_SKEW_MAX",
            "DUPLICATE_WINDOW",
            "LATE_SUBMISSION",
        } <= types

    async def test_the_type_catalogue_describes_every_rule_type(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        types = (await api.get("/business-rules/types", headers=admin)).json()
        assert len(types) == 11
        tonnage = next(t for t in types if t["rule_type"] == "TONNAGE_MAX")
        assert "truck_type_id" in tonnage["scope_keys"] and tonnage["example"]["max"] == "16"
        assert "max" in tonnage["value_schema"]["properties"]

    async def test_an_auditor_reads_but_cannot_write(self, api: AsyncClient, login: Any) -> None:
        auditor = await login(AUDITOR)
        assert (await api.get("/business-rules", headers=auditor)).status_code == 200
        refused = await api.post("/business-rules", headers=auditor, json=_tonnage())
        assert refused.status_code == 403

    async def test_a_site_manager_cannot_even_read_the_rules(
        self, api: AsyncClient, login: Any
    ) -> None:
        site = await login(REQUESTER)
        assert (await api.get("/business-rules", headers=site)).status_code == 403

    async def test_create_and_read_back_with_the_value_normalised(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        created = await api.post(
            "/business-rules", headers=admin, json=_tonnage(value={"max": 16.5})
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["value"] == {"max": "16.5", "unit": "TON"}
        assert body["rule_type_label"] == "Maximum tonnage" and body["is_active"] is True
        again = (await api.get(f"/business-rules/{body['id']}", headers=admin)).json()
        assert again["id"] == body["id"]

    async def test_bad_values_scopes_and_conditions_are_refused_with_field_errors(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)

        bad_value = await api.post(
            "/business-rules", headers=admin, json=_tonnage(value={"max": -3})
        )
        assert bad_value.status_code == 422
        assert bad_value.json()["errors"][0]["field"] == "value"

        # A geofence radius cannot be scoped by truck type.
        bad_scope = await api.post(
            "/business-rules",
            headers=admin,
            json={
                "rule_type": "GEOFENCE_RADIUS",
                "value": {"radius_m": 300},
                "scope": {"truck_type_id": "00000000-0000-0000-0000-000000000001"},
                "effective_from": TODAY.isoformat(),
            },
        )
        assert bad_scope.status_code == 422 and "truck_type_id" in bad_scope.text

        not_an_id = await api.post(
            "/business-rules", headers=admin, json=_tonnage(scope={"site_id": "GVH-S1"})
        )
        assert not_an_id.status_code == 422
        assert not_an_id.json()["errors"][0]["field"] == "scope.site_id"

        bad_condition = await api.post(
            "/business-rules", headers=admin, json=_tonnage(condition={"eval": ["1+1"]})
        )
        assert bad_condition.status_code == 422
        assert bad_condition.json()["errors"][0]["field"] == "condition"

        backwards = await api.post(
            "/business-rules",
            headers=admin,
            json=_tonnage(effective_to=(TODAY - timedelta(days=90)).isoformat()),
        )
        assert backwards.status_code == 422

    async def test_two_rules_that_would_tie_are_refused(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        ends = (TODAY + timedelta(days=5)).isoformat()
        assert (
            await api.post("/business-rules", headers=admin, json=_tonnage(effective_to=ends))
        ).status_code == 201
        tie = await api.post(
            "/business-rules", headers=admin, json=_tonnage(name="Same again", effective_to=ends)
        )
        assert tie.status_code == 422 and tie.json()["rule"] == "business_rule_conflict"
        # A different priority is not a tie...
        assert (
            await api.post("/business-rules", headers=admin, json=_tonnage(priority=1))
        ).status_code == 201
        # ...and neither is a rule that starts after the first one ends.
        assert (
            await api.post(
                "/business-rules",
                headers=admin,
                json=_tonnage(effective_from=(TODAY + timedelta(days=6)).isoformat()),
            )
        ).status_code == 201

    async def test_only_value_priority_dates_and_state_can_change(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        rule = (await api.post("/business-rules", headers=admin, json=_tonnage())).json()

        changed = await api.patch(
            f"/business-rules/{rule['id']}",
            headers={**admin, "If-Match": str(rule["version"])},
            json={"value": {"max": "14"}, "effective_to": (TODAY + timedelta(days=60)).isoformat()},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["value"]["max"] == "14" and changed.json()["version"] == 2

        # Scope, type and start date are not part of the contract at all.
        for field, value in (
            ("scope", {}),
            ("rule_type", "GEOFENCE_RADIUS"),
            ("effective_from", "2020-01-01"),
        ):
            refused = await api.patch(
                f"/business-rules/{rule['id']}", headers=admin, json={field: value}
            )
            assert refused.status_code == 422, field

        # A stale edit is refused rather than overwriting a colleague's.
        stale = await api.patch(
            f"/business-rules/{rule['id']}",
            headers={**admin, "If-Match": "1"},
            json={"priority": 9},
        )
        assert stale.status_code == 409

        cleared = await api.patch(
            f"/business-rules/{rule['id']}", headers=admin, json={"effective_to": None}
        )
        assert cleared.json()["effective_to"] is None

    async def test_rule_changes_are_audited(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        admin = await login(ADMIN)
        rule = (await api.post("/business-rules", headers=admin, json=_tonnage())).json()
        await api.patch(
            f"/business-rules/{rule['id']}", headers=admin, json={"value": {"max": "12"}}
        )
        actions = (
            (
                await db.execute(
                    text(
                        "SELECT action FROM audit_logs WHERE entity_id = :id ORDER BY occurred_at"
                    ),
                    {"id": rule["id"]},
                )
            )
            .scalars()
            .all()
        )
        assert actions == ["CREATE", "UPDATE"]


class TestResolve:
    async def test_the_most_specific_rule_wins_and_every_other_is_explained(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        truck = "00000000-0000-0000-0000-0000000000a1"
        material = "00000000-0000-0000-0000-0000000000b2"
        default = (
            await api.post(
                "/business-rules", headers=admin, json=_tonnage(scope={"truck_type_id": truck})
            )
        ).json()
        crush = (
            await api.post(
                "/business-rules",
                headers=admin,
                json=_tonnage(
                    name="Crush",
                    value={"max": "18"},
                    scope={"truck_type_id": truck, "material_id": material},
                ),
            )
        ).json()

        hit = (
            await api.post(
                "/business-rules/resolve",
                headers=admin,
                json={
                    "rule_type": "TONNAGE_MAX",
                    "context": {"truck_type_id": truck, "material_id": material},
                },
            )
        ).json()
        assert hit["winner"]["id"] == crush["id"] and hit["winner"]["value"]["max"] == "18"
        verdicts = {v["rule_id"]: v for v in hit["considered"]}
        assert verdicts[default["id"]]["applies"] and not verdicts[default["id"]]["winner"]
        assert verdicts[crush["id"]]["winner"] is True

        only_truck = (
            await api.post(
                "/business-rules/resolve",
                headers=admin,
                json={"rule_type": "TONNAGE_MAX", "context": {"truck_type_id": truck}},
            )
        ).json()
        assert only_truck["winner"]["id"] == default["id"]
        crush_verdict = next(v for v in only_truck["considered"] if v["rule_id"] == crush["id"])
        assert (
            crush_verdict["applies"] is False and crush_verdict["reason"] == "scope does not match"
        )

    async def test_resolution_honours_the_date(self, api: AsyncClient, login: Any) -> None:
        admin = await login(ADMIN)
        old = (
            await api.post(
                "/business-rules",
                headers=admin,
                json={
                    "rule_type": "GEOFENCE_RADIUS",
                    "scope": {"site_id": "00000000-0000-0000-0000-0000000000c3"},
                    "value": {"radius_m": 300},
                    "effective_from": "2026-01-01",
                    "effective_to": "2026-03-31",
                },
            )
        ).json()
        new = (
            await api.post(
                "/business-rules",
                headers=admin,
                json={
                    "rule_type": "GEOFENCE_RADIUS",
                    "scope": {"site_id": "00000000-0000-0000-0000-0000000000c3"},
                    "value": {"radius_m": 700},
                    "effective_from": "2026-04-01",
                },
            )
        ).json()
        context = {"site_id": "00000000-0000-0000-0000-0000000000c3"}

        async def winner(at: str) -> str:
            body = (
                await api.post(
                    "/business-rules/resolve",
                    headers=admin,
                    json={"rule_type": "GEOFENCE_RADIUS", "context": context, "at": at},
                )
            ).json()
            return str(body["winner"]["id"])

        assert await winner("2026-02-15") == old["id"]
        assert await winner("2026-08-01") == new["id"]


class TestApprovalLimits:
    """A workflow decides who must sign; a limit decides how much a signer may sign."""

    async def _request(self, api: AsyncClient, login: Any, amount: str) -> dict[str, Any]:
        requester = await login(REQUESTER)
        projects = (await api.get("/projects", headers=requester, params={"q": "GVH"})).json()[
            "items"
        ]
        project = next(p for p in projects if p["code"] == "GVH")
        sites = (
            await api.get("/sites", headers=requester, params={"project_id": project["id"]})
        ).json()
        site = next(s for s in sites["items"] if s["code"] == "GVH-S1")
        material = next(
            m
            for m in (await api.get("/materials", headers=requester, params={"limit": 50})).json()[
                "items"
            ]
            if m["is_purchasable"]
        )
        created = await api.post(
            "/purchase-requests",
            headers=requester,
            json={
                "project_id": project["id"],
                "site_id": site["id"],
                "justification": "Approval limit test",
                "items": [
                    {
                        "material_id": material["id"],
                        "unit_id": material["base_unit_id"],
                        "quantity": "1",
                        "estimated_rate": amount,
                    }
                ],
            },
        )
        submitted = await api.post(
            f"/purchase-requests/{created.json()['id']}/submit", headers=requester
        )
        assert submitted.status_code == 200, submitted.text
        return submitted.json()  # type: ignore[no-any-return]

    async def _limit(
        self, api: AsyncClient, admin: dict[str, str], role: str, limit: str, **scope: str
    ) -> None:
        role_id = await _role_id(api, admin, role)
        created = await api.post(
            "/business-rules",
            headers=admin,
            json={
                "rule_type": "APPROVAL_LIMIT",
                "name": f"{role} limit",
                "scope": {"role_id": role_id, **scope},
                "value": {"limit": limit, "currency": "PKR"},
                "effective_from": (TODAY - timedelta(days=1)).isoformat(),
            },
        )
        assert created.status_code == 201, created.text

    async def _trail(self, api: AsyncClient, headers: dict[str, str], pr_id: str) -> dict[str, Any]:
        rows = (
            await api.get(
                "/approvals/requests",
                headers=headers,
                params={"doc_type": "purchase_request", "doc_id": pr_id},
            )
        ).json()
        return rows[0]  # type: ignore[no-any-return]

    async def test_with_no_limit_rules_nothing_changes(self, api: AsyncClient, login: Any) -> None:
        pr = await self._request(api, login, "500000")
        assert (
            await api.post(
                f"/approvals/requests/{pr['approval_request_id']}/approve",
                headers=await login(PM),
                json={},
            )
        ).status_code == 200

    async def test_an_approver_over_their_limit_is_refused_and_told_why(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        await self._limit(api, admin, "PROJECT_MANAGER", "100000")
        pr = await self._request(api, login, "500000")
        pm = await login(PM)

        # The screen is told in advance, so it can say so instead of offering Approve.
        request = await self._trail(api, pm, pr["id"])
        # They are still an approver — they may reject or return it — but not approve.
        assert request["can_decide"] is True
        assert "above your approval limit" in request["decision_blocked_reason"]
        assert "100,000.00" in request["decision_blocked_reason"]

        refused = await api.post(
            f"/approvals/requests/{pr['approval_request_id']}/approve", headers=pm, json={}
        )
        assert refused.status_code == 422
        assert refused.json()["rule"] == "approval_limit_exceeded"

        # Rejecting or returning it is still the approver's right.
        returned = await api.post(
            f"/approvals/requests/{pr['approval_request_id']}/request-changes",
            headers=pm,
            json={"comments": "Above my limit; please split or escalate"},
        )
        assert returned.status_code == 200

    async def test_within_the_limit_the_approver_signs_as_usual(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        await self._limit(api, admin, "PROJECT_MANAGER", "100000")
        pr = await self._request(api, login, "50000")
        pm = await login(PM)
        assert (await self._trail(api, pm, pr["id"]))["can_decide"] is True
        assert (
            await api.post(
                f"/approvals/requests/{pr['approval_request_id']}/approve", headers=pm, json={}
            )
        ).status_code == 200

    async def test_a_limit_on_one_role_never_restricts_another(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        await self._limit(api, admin, "PROJECT_MANAGER", "1000")
        pr = await self._request(api, login, "500000")
        pm = await login(PM)
        # Step 1 is the project manager, who is over their (tiny) limit...
        assert (
            await api.post(
                f"/approvals/requests/{pr['approval_request_id']}/approve", headers=pm, json={}
            )
        ).status_code == 422
        # ...but the procurement manager, with no limit rule, is unlimited. Raise the
        # limit so step 1 passes and confirm step 2 is not affected by the PM's rule.
        role_id = await _role_id(api, admin, "PROJECT_MANAGER")
        rules = (
            await api.get("/business-rules", headers=admin, params={"rule_type": "APPROVAL_LIMIT"})
        ).json()["items"]
        mine = next(r for r in rules if r["scope"].get("role_id") == role_id)
        await api.patch(
            f"/business-rules/{mine['id']}", headers=admin, json={"value": {"limit": "1000000"}}
        )
        assert (
            await api.post(
                f"/approvals/requests/{pr['approval_request_id']}/approve", headers=pm, json={}
            )
        ).status_code == 200
        procurement = await login(PROCUREMENT)
        assert (await self._trail(api, procurement, pr["id"]))["can_decide"] is True

    async def test_a_limit_can_be_scoped_to_a_document_type(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        # A purchase-order-only limit must not touch purchase requests.
        await self._limit(api, admin, "PROJECT_MANAGER", "1", doc_type="purchase_order")
        pr = await self._request(api, login, "500000")
        assert (
            await api.post(
                f"/approvals/requests/{pr['approval_request_id']}/approve",
                headers=await login(PM),
                json={},
            )
        ).status_code == 200

    async def test_the_limit_in_force_when_a_role_has_two_rules_is_the_most_specific(
        self, api: AsyncClient, login: Any
    ) -> None:
        admin = await login(ADMIN)
        await self._limit(api, admin, "PROJECT_MANAGER", "1000")
        await self._limit(api, admin, "PROJECT_MANAGER", "900000", doc_type="purchase_request")
        pr = await self._request(api, login, "500000")
        assert (
            await api.post(
                f"/approvals/requests/{pr['approval_request_id']}/approve",
                headers=await login(PM),
                json={},
            )
        ).status_code == 200
