"""The authorisation matrix.

For each seeded role and each protected endpoint, assert the expected outcome.
The expectations live in a data table, so adding an endpoint without stating
who may call it is a visible omission rather than an accident.

Conventions being verified:

* a missing permission is 403;
* a record outside the caller's scope is 404, never 403 — a 403 would confirm
  the record exists;
* the Auditor can read everything and write nothing.
"""

from __future__ import annotations

from typing import Any, Literal

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.authz]

Outcome = Literal["allow", "forbidden", "not_found"]

# Seeded users, one per role.
USERS: dict[str, str] = {
    "SUPER_ADMIN": "admin@krb.example",
    "EXECUTIVE": "ceo@krb.example",
    "FINANCE_MANAGER": "finance@krb.example",
    "HR_MANAGER": "hr@krb.example",
    "PROCUREMENT_MANAGER": "procurement@krb.example",
    "PROJECT_MANAGER": "pm.gvh@krb.example",
    "SITE_MANAGER": "sm.gvh1@krb.example",
    "STORE_MANAGER": "store.cs@krb.example",
    "SITE_STAFF": "staff.gvh1@krb.example",
    "ACCOUNTS_OFFICER": "accounts@krb.example",
    "AUDITOR": "auditor@krb.example",
}

VALID_VENDOR: dict[str, Any] = {
    "legal_name": "Matrix Test Supplier",
    "ntn": "1234567-0",
    "contacts": [],
}

# (method, path, body, {role: expected}) — roles absent from the mapping are
# expected to be "forbidden".
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, dict[str, Outcome]]] = [
    (
        "GET",
        "/vendors",
        None,
        {
            "SUPER_ADMIN": "allow",
            "EXECUTIVE": "allow",
            "FINANCE_MANAGER": "allow",
            "PROCUREMENT_MANAGER": "allow",
            "PROJECT_MANAGER": "allow",
            "SITE_MANAGER": "allow",
            "STORE_MANAGER": "allow",
            "SITE_STAFF": "allow",
            "ACCOUNTS_OFFICER": "allow",
            "AUDITOR": "allow",
            # HR has no business reading the supplier list.
            "HR_MANAGER": "forbidden",
        },
    ),
    (
        "POST",
        "/vendors",
        VALID_VENDOR,
        {
            "SUPER_ADMIN": "allow",
            "PROCUREMENT_MANAGER": "allow",
        },
    ),
    (
        "GET",
        "/vendors/{vendor_id}/bank-accounts",
        None,
        {
            "SUPER_ADMIN": "allow",
            "FINANCE_MANAGER": "allow",
        },
    ),
    (
        "GET",
        "/auth/me",
        None,
        dict.fromkeys(USERS, "allow"),
    ),
    (
        "GET",
        "/auth/sessions",
        None,
        dict.fromkeys(USERS, "allow"),
    ),
]

EXPECTED_STATUS: dict[Outcome, tuple[int, ...]] = {
    # 200 for reads, 201 for creates.
    "allow": (200, 201),
    "forbidden": (403,),
    "not_found": (404,),
}


@pytest.fixture
async def vendor_id(api: AsyncClient, login: Any) -> str:
    headers = await login(USERS["SUPER_ADMIN"])
    return str((await api.get("/vendors", headers=headers)).json()["items"][0]["id"])


def _cases() -> list[tuple[str, str, str, dict[str, Any] | None, Outcome]]:
    cases = []
    for method, path, body, expectations in ENDPOINTS:
        for role in USERS:
            cases.append((role, method, path, body, expectations.get(role, "forbidden")))
    return cases


@pytest.mark.parametrize(("role", "method", "path", "body", "expected"), _cases())
async def test_permission_matrix(
    api: AsyncClient,
    login: Any,
    vendor_id: str,
    role: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    expected: Outcome,
) -> None:
    headers = await login(USERS[role])
    url = path.format(vendor_id=vendor_id)

    if method == "GET":
        response = await api.get(url, headers=headers)
    elif method == "POST":
        response = await api.post(url, headers=headers, json=body or {})
    else:  # pragma: no cover - extend as methods are added
        raise AssertionError(f"Unhandled method {method}")

    assert response.status_code in EXPECTED_STATUS[expected], (
        f"{role} {method} {path}: expected {expected} "
        f"{EXPECTED_STATUS[expected]}, got {response.status_code} — {response.text[:200]}"
    )


class TestMatrixCompleteness:
    def test_every_seeded_role_is_covered(self) -> None:
        """A new role must not slip in without an authorisation expectation."""
        from app.modules.access.domain.roles import STANDARD_ROLES

        assert {role.code for role in STANDARD_ROLES} == set(USERS)

    def test_every_write_endpoint_states_who_may_call_it(self) -> None:
        for method, path, _body, expectations in ENDPOINTS:
            if method == "GET":
                continue
            allowed = [role for role, outcome in expectations.items() if outcome == "allow"]
            assert allowed, f"{method} {path} has no role allowed to call it"


class TestAuditorIsReadOnly:
    """The Auditor role is guarded twice: by holding no write permissions, and
    by a read-only flag on the resolved context. Both are checked."""

    async def test_holds_no_write_permission(self, api: AsyncClient, login: Any) -> None:
        headers = await login(USERS["AUDITOR"])
        body = (await api.get("/auth/me", headers=headers)).json()

        assert body["is_read_only"] is True

        read_suffixes = {
            "view",
            "view_own",
            "view_salary",
            "view_financial",
            "view_valuation",
            "view_history",
            "export",
        }
        writes = [
            permission
            for permission in body["permissions"]
            if permission.rsplit(".", 1)[-1] not in read_suffixes
        ]
        assert writes == [], f"Auditor holds write permissions: {writes}"

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("POST", "/vendors", VALID_VENDOR),
            ("POST", "/vendors/{vendor_id}/approve", {}),
            ("POST", "/vendors/{vendor_id}/suspend", {"reason": "matrix test reason"}),
        ],
    )
    async def test_every_mutation_is_refused(
        self,
        api: AsyncClient,
        login: Any,
        vendor_id: str,
        method: str,
        path: str,
        body: dict[str, Any],
    ) -> None:
        headers = await login(USERS["AUDITOR"])
        response = await api.post(path.format(vendor_id=vendor_id), headers=headers, json=body)
        assert response.status_code == 403, f"{method} {path} returned {response.status_code}"


class TestSiteStaffScope:
    async def test_permissions_are_scoped_to_one_site(self, api: AsyncClient, login: Any) -> None:
        headers = await login(USERS["SITE_STAFF"])
        body = (await api.get("/auth/me", headers=headers)).json()

        scope = body["scopes"]["deliveries.create"]
        assert scope["is_global"] is False
        assert scope["is_company_wide"] is False
        assert len(scope["site_ids"]) == 1

    async def test_cannot_reach_approval_permissions(self, api: AsyncClient, login: Any) -> None:
        headers = await login(USERS["SITE_STAFF"])
        permissions = set((await api.get("/auth/me", headers=headers)).json()["permissions"])

        for forbidden in (
            "deliveries.approve",
            "deliveries.review",
            "deliveries.reject",
            "grn.approve",
            "finance.gl.post",
            "finance.payment.execute",
            "rates.create",
            "users.assign_roles",
            "vendors.manage_bank_details",
            "settings.manage",
        ):
            assert forbidden not in permissions, forbidden


class TestSeparationOfDuties:
    """Shipped defaults must not let one role both raise and authorise the same
    thing, and must keep the high-risk permissions apart."""

    def test_procurement_cannot_change_vendor_bank_details(self) -> None:
        from app.modules.access.domain.roles import ROLES_BY_CODE

        assert "vendors.manage_bank_details" not in ROLES_BY_CODE["PROCUREMENT_MANAGER"].resolve()

    def test_hr_cannot_reach_finance(self) -> None:
        from app.modules.access.domain.roles import ROLES_BY_CODE

        finance = {p for p in ROLES_BY_CODE["HR_MANAGER"].resolve() if p.startswith("finance.")}
        assert finance == set()

    def test_only_the_super_administrator_holds_restricted_permissions(self) -> None:
        from app.modules.access.domain.permissions import RESTRICTED_CODES
        from app.modules.access.domain.roles import ROLES_BY_CODE, SUPER_ADMIN_CODE

        for code, role in ROLES_BY_CODE.items():
            if code == SUPER_ADMIN_CODE:
                continue
            leaked = role.resolve() & RESTRICTED_CODES
            assert leaked == set(), f"{code} holds {sorted(leaked)}"

    def test_site_staff_cannot_approve_their_own_entries(self) -> None:
        from app.modules.access.domain.roles import ROLES_BY_CODE

        permissions = ROLES_BY_CODE["SITE_STAFF"].resolve()
        assert "deliveries.create" in permissions
        assert "deliveries.approve" not in permissions
        assert "deliveries.review" not in permissions

    def test_accounts_officer_cannot_approve_the_payments_it_requests(self) -> None:
        from app.modules.access.domain.roles import ROLES_BY_CODE

        permissions = ROLES_BY_CODE["ACCOUNTS_OFFICER"].resolve()
        assert "finance.payment.request" in permissions
        assert "finance.payment.approve" not in permissions
        assert "finance.payment.execute" not in permissions
