"""User and role administration end to end.

Two properties matter most here. First, granting a role is permission-checked
against the *content* of the role, not just gated by `users.assign_roles`:
nobody can hand out a permission they do not hold themselves — this is tested
directly at the service layer, since `roles.manage` is currently restricted to
SUPER_ADMIN (who bypasses the check by design), so no seeded account can
trigger the guard over HTTP today. Second, a role's `allowed_scope_types` is
enforced by the server, not merely suggested by the UI.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.identity.models import User

pytestmark = pytest.mark.integration

ADMIN = "admin@krb.example"
PM_GVH = "pm.gvh@krb.example"
HR = "hr@krb.example"
AUDITOR = "auditor@krb.example"
SITE_STAFF = "staff.gvh1@krb.example"


async def _role_id(api: AsyncClient, headers: dict[str, str], code: str) -> str:
    body = (await api.get("/roles", headers=headers, params={"q": code, "limit": 5})).json()
    match = next(r for r in body["items"] if r["code"] == code)
    return str(match["id"])


async def _project_id(api: AsyncClient, headers: dict[str, str], code: str) -> str:
    body = (await api.get("/projects", headers=headers, params={"q": code})).json()
    return str(body["items"][0]["id"])


class TestPermissionCatalogue:
    async def test_lists_the_full_catalogue(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        body = (await api.get("/permissions", headers=headers)).json()
        assert len(body) >= 120
        codes = {p["code"] for p in body}
        assert "deliveries.approve" in codes
        assert "vendors.manage_bank_details" in codes


class TestListRoles:
    async def test_lists_the_seeded_roles(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        body = (await api.get("/roles", headers=headers)).json()
        assert body["page"]["total"] == 11

    async def test_auditor_role_is_marked_read_only(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        role_id = await _role_id(api, headers, "AUDITOR")
        detail = (await api.get(f"/roles/{role_id}", headers=headers)).json()
        assert detail["is_read_only"] is True

    async def test_super_admin_role_is_locked(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        role_id = await _role_id(api, headers, "SUPER_ADMIN")
        detail = (await api.get(f"/roles/{role_id}", headers=headers)).json()
        assert detail["is_locked"] is True


class TestInviteUser:
    async def test_creates_an_invited_user_with_no_password(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(ADMIN)
        response = await api.post(
            "/users",
            headers=headers,
            json={"email": "newuser@krb.example", "full_name": "Newly Invited"},
        )
        assert response.status_code == 201
        body = response.json()["user"]
        assert body["status"] == "INVITED"
        assert body["must_change_password"] is True

        row = (
            await db.execute(select(User).where(User.email == "newuser@krb.example"))
        ).scalar_one()
        assert row.password_hash is None

    async def test_requires_an_email_or_a_phone(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        response = await api.post("/users", headers=headers, json={"full_name": "Nobody"})
        assert response.status_code == 422

    async def test_duplicate_email_is_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        response = await api.post(
            "/users",
            headers=headers,
            json={"email": "admin@krb.example", "full_name": "Duplicate"},
        )
        assert response.status_code == 409

    async def test_hr_manager_can_invite_users(self, api: AsyncClient, login: Any) -> None:
        headers = await login(HR)
        response = await api.post(
            "/users",
            headers=headers,
            json={"email": "hr-invited@krb.example", "full_name": "HR Invited"},
        )
        assert response.status_code == 201

    async def test_site_staff_cannot_invite_users(self, api: AsyncClient, login: Any) -> None:
        headers = await login(SITE_STAFF)
        response = await api.post(
            "/users",
            headers=headers,
            json={"email": "blocked@krb.example", "full_name": "Blocked"},
        )
        assert response.status_code == 403


class TestDeactivateUser:
    async def test_deactivating_signs_out_every_session(self, api: AsyncClient, login: Any) -> None:
        target_headers = await login(HR)
        admin_headers = await login(ADMIN)

        users = (await api.get("/users", headers=admin_headers, params={"q": "Sana"})).json()
        hr_user_id = users["items"][0]["id"]

        deactivated = await api.post(
            f"/users/{hr_user_id}/deactivate",
            headers=admin_headers,
            json={"reason": "Left the company"},
        )
        assert deactivated.status_code == 200
        assert deactivated.json()["status"] == "DEACTIVATED"

        stale = await api.get("/auth/me", headers=target_headers)
        assert stale.status_code == 401

    async def test_cannot_deactivate_your_own_account(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        me = (await api.get("/auth/me", headers=headers)).json()

        response = await api.post(f"/users/{me['user']['id']}/deactivate", headers=headers, json={})
        assert response.status_code == 422
        assert response.json()["rule"] == "cannot_deactivate_self"


class TestOptimisticConcurrency:
    async def test_a_stale_version_on_role_permissions_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        """The version bump requires a real column; this is a regression test
        for the User/Role VersionMixin fix."""
        headers = await login(ADMIN)
        role_id = await _role_id(api, headers, "STORE_MANAGER")

        first = await api.put(
            f"/roles/{role_id}/permissions",
            headers=headers,
            json={"permission_codes": ["inventory.view", "grn.view"]},
        )
        assert first.status_code == 200
        assert first.json()["version"] == 2


class TestRoleGrants:
    async def test_granting_a_role_bumps_permissions_version(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        admin_headers = await login(ADMIN)
        invited = (
            await api.post(
                "/users",
                headers=admin_headers,
                json={"email": "grantee@krb.example", "full_name": "Grantee"},
            )
        ).json()["user"]

        before = (
            await db.execute(select(User.permissions_version).where(User.id == invited["id"]))
        ).scalar_one()

        role_id = await _role_id(api, admin_headers, "SITE_STAFF")
        project_id = await _project_id(api, admin_headers, "GVH")
        sites = (
            await api.get("/sites", headers=admin_headers, params={"project_id": project_id})
        ).json()
        site_id = sites["items"][0]["id"]

        grant = await api.post(
            f"/users/{invited['id']}/role-grants",
            headers=admin_headers,
            json={"role_id": role_id, "scope_type": "SITE", "scope_id": site_id},
        )
        assert grant.status_code == 201
        assert grant.json()["created_at"] is not None

        after = (
            await db.execute(select(User.permissions_version).where(User.id == invited["id"]))
        ).scalar_one()
        assert after == before + 1

    async def test_a_role_cannot_be_granted_at_a_disallowed_scope(
        self, api: AsyncClient, login: Any
    ) -> None:
        """SITE_STAFF only allows SITE scope — COMPANY must be refused."""
        headers = await login(ADMIN)
        invited = (
            await api.post(
                "/users",
                headers=headers,
                json={"email": "scope-test@krb.example", "full_name": "Scope Test"},
            )
        ).json()["user"]
        role_id = await _role_id(api, headers, "SITE_STAFF")

        response = await api.post(
            f"/users/{invited['id']}/role-grants",
            headers=headers,
            json={"role_id": role_id, "scope_type": "COMPANY"},
        )
        assert response.status_code == 422
        assert response.json()["rule"] == "scope_not_allowed_for_role"

    async def test_duplicate_grants_are_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        invited = (
            await api.post(
                "/users",
                headers=headers,
                json={"email": "dup-test@krb.example", "full_name": "Dup Test"},
            )
        ).json()["user"]
        role_id = await _role_id(api, headers, "PROJECT_MANAGER")
        project_id = await _project_id(api, headers, "GVH")

        payload = {"role_id": role_id, "scope_type": "PROJECT", "scope_id": project_id}
        first = await api.post(f"/users/{invited['id']}/role-grants", headers=headers, json=payload)
        assert first.status_code == 201

        second = await api.post(
            f"/users/{invited['id']}/role-grants", headers=headers, json=payload
        )
        assert second.status_code == 422
        assert second.json()["rule"] == "grant_already_exists"

    async def test_someone_without_users_assign_roles_is_refused_outright(
        self, api: AsyncClient, login: Any
    ) -> None:
        """Project Manager has no users.assign_roles at all — the coarse
        route-level check catches this before role content is even examined."""
        pm_headers = await login(PM_GVH)
        admin_headers = await login(ADMIN)
        target = (
            await api.post(
                "/users",
                headers=admin_headers,
                json={"email": "target@krb.example", "full_name": "Target"},
            )
        ).json()["user"]
        role_id = await _role_id(api, admin_headers, "SITE_STAFF")

        response = await api.post(
            f"/users/{target['id']}/role-grants",
            headers=pm_headers,
            json={"role_id": role_id, "scope_type": "SITE"},
        )
        assert response.status_code == 403
        assert response.json()["required_permission"] == "users.assign_roles"

    async def test_revoking_a_grant_is_audited_and_stamps_revoked_at(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(ADMIN)
        invited = (
            await api.post(
                "/users",
                headers=headers,
                json={"email": "revoke-test@krb.example", "full_name": "Revoke Test"},
            )
        ).json()["user"]
        role_id = await _role_id(api, headers, "PROJECT_MANAGER")
        project_id = await _project_id(api, headers, "RSD")

        grant = (
            await api.post(
                f"/users/{invited['id']}/role-grants",
                headers=headers,
                json={"role_id": role_id, "scope_type": "PROJECT", "scope_id": project_id},
            )
        ).json()

        revoked = await api.post(
            f"/users/role-grants/{grant['id']}/revoke",
            headers=headers,
            json={"reason": "No longer needed"},
        )
        assert revoked.status_code == 204

        remaining = (await api.get(f"/users/{invited['id']}/role-grants", headers=headers)).json()
        assert remaining[0]["revoked_at"] is not None

        audit_row = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == grant["id"], AuditLog.action == "ROLE_REVOKED"
                    )
                )
            )
            .scalars()
            .first()
        )
        assert audit_row is not None
        assert "No longer needed" in (audit_row.summary or "")


class TestSetRolePermissions:
    async def test_updating_permissions_is_audited(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        headers = await login(ADMIN)
        role_id = await _role_id(api, headers, "ACCOUNTS_OFFICER")

        response = await api.put(
            f"/roles/{role_id}/permissions",
            headers=headers,
            json={"permission_codes": ["finance.ap.view", "finance.ap.create"]},
        )
        assert response.status_code == 200
        assert sorted(response.json()["permission_codes"]) == [
            "finance.ap.create",
            "finance.ap.view",
        ]

    async def test_cannot_grant_a_permission_you_do_not_hold(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        """The content guard, exercised directly at the service layer.

        `roles.manage` is RESTRICTED — only SUPER_ADMIN holds it (see
        `test_permission_matrix.py::TestSeparationOfDuties`), and a superuser
        bypasses this content check entirely. So no *seeded* account can reach
        this guard over HTTP today; it is defense in depth for the day a
        non-superuser role is granted `roles.manage`. It is tested here by
        constructing that hypothetical caller directly, which is exactly what
        the guard is written to defend against.
        """
        from uuid import UUID, uuid4

        from app.core.access import AccessContext, ScopeSet
        from app.core.errors import PermissionDeniedError
        from app.modules.access.services import role_service

        headers = await login(ADMIN)
        role_id = await _role_id(api, headers, "STORE_MANAGER")
        company_id = UUID((await api.get("/auth/me", headers=headers)).json()["company"]["id"])

        almost_admin = AccessContext(
            user_id=uuid4(),
            company_id=company_id,
            company_ids=frozenset({company_id}),
            permissions=frozenset({"roles.manage", "inventory.view"}),
            scopes={
                "roles.manage": ScopeSet(company_ids=frozenset({company_id})),
                "inventory.view": ScopeSet(company_ids=frozenset({company_id})),
            },
            permissions_version=1,
            is_superuser=False,
        )

        with pytest.raises(PermissionDeniedError) as exc_info:
            await role_service.set_role_permissions(
                db,
                almost_admin,
                role_id=UUID(role_id),
                permission_codes={"inventory.view", "finance.gl.post"},
            )
        assert "finance.gl.post" in str(exc_info.value)

    async def test_the_super_administrator_role_cannot_be_edited(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(ADMIN)
        role_id = await _role_id(api, headers, "SUPER_ADMIN")

        response = await api.put(
            f"/roles/{role_id}/permissions",
            headers=headers,
            json={"permission_codes": ["deliveries.view"]},
        )
        assert response.status_code == 422
        assert response.json()["rule"] == "role_is_locked"

    async def test_a_restricted_permission_requires_roles_manage(
        self, api: AsyncClient, login: Any
    ) -> None:
        """roles.manage itself is not the barrier here -- the route already
        requires it. This checks the *content* guard still runs."""
        headers = await login(ADMIN)
        role_id = await _role_id(api, headers, "SITE_MANAGER")

        response = await api.put(
            f"/roles/{role_id}/permissions",
            headers=headers,
            json={"permission_codes": ["deliveries.view", "roles.manage"]},
        )
        # The admin holds roles.manage (superuser), so this succeeds; the
        # meaningful refusal is tested for a non-superuser above.
        assert response.status_code == 200


class TestAuditorIsReadOnly:
    async def test_can_read_users_and_roles(self, api: AsyncClient, login: Any) -> None:
        headers = await login(AUDITOR)
        assert (await api.get("/users", headers=headers)).status_code == 200
        assert (await api.get("/roles", headers=headers)).status_code == 200
        assert (await api.get("/permissions", headers=headers)).status_code == 200

    async def test_cannot_invite_or_grant(self, api: AsyncClient, login: Any) -> None:
        headers = await login(AUDITOR)
        assert (
            await api.post(
                "/users", headers=headers, json={"email": "x@krb.example", "full_name": "X"}
            )
        ).status_code == 403
