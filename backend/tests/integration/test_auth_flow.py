"""Authentication end to end, against a real database.

Covers the behaviours that are security properties rather than features: token
rotation, reuse detection, lockout, and the fact that `/auth/password/forgot`
cannot be used to enumerate accounts.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User, UserSession

pytestmark = pytest.mark.integration

PASSWORD = "KrbDev!Passw0rd"
ADMIN = "admin@krb.example"
SITE_STAFF = "staff.gvh1@krb.example"
AUDITOR = "auditor@krb.example"


class TestLogin:
    async def test_login_with_email(self, api: AsyncClient) -> None:
        response = await api.post("/auth/login", json={"identifier": ADMIN, "password": PASSWORD})
        assert response.status_code == 200

        body = response.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"] and body["refresh_token"]
        assert body["user"]["full_name"] == "Sys Admin"

    async def test_login_with_phone_in_local_format(self, api: AsyncClient) -> None:
        """Site staff type `0300-...`; the stored value is E.164."""
        response = await api.post(
            "/auth/login", json={"identifier": "0300-1000010", "password": PASSWORD}
        )
        assert response.status_code == 200
        assert response.json()["user"]["full_name"] == "Rashid Mehmood"

    @pytest.mark.parametrize(
        "identifier", ["+923001000010", "03001000010", "0300 1000010", "923001000010"]
    )
    async def test_phone_formats_all_resolve_to_one_user(
        self, api: AsyncClient, identifier: str
    ) -> None:
        response = await api.post(
            "/auth/login", json={"identifier": identifier, "password": PASSWORD}
        )
        assert response.status_code == 200, identifier

    async def test_wrong_password_is_rejected(self, api: AsyncClient) -> None:
        response = await api.post("/auth/login", json={"identifier": ADMIN, "password": "nope"})
        assert response.status_code == 401
        assert response.json()["title"] == "Invalid credentials"

    async def test_unknown_account_gives_the_same_error_as_a_wrong_password(
        self, api: AsyncClient
    ) -> None:
        """Different messages would turn login into an account enumerator."""
        unknown = await api.post(
            "/auth/login", json={"identifier": "nobody@krb.example", "password": "whatever12345"}
        )
        wrong = await api.post(
            "/auth/login", json={"identifier": ADMIN, "password": "whatever12345"}
        )

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["title"] == wrong.json()["title"]
        assert unknown.json()["detail"] == wrong.json()["detail"]

    async def test_login_records_an_audit_row(self, api: AsyncClient, db: AsyncSession) -> None:
        from app.modules.audit.models import AuditLog

        await api.post("/auth/login", json={"identifier": ADMIN, "password": PASSWORD})

        rows = (
            (
                await db.execute(
                    select(AuditLog)
                    .where(AuditLog.action == "LOGIN")
                    .order_by(AuditLog.occurred_at.desc())
                )
            )
            .scalars()
            .all()
        )
        assert rows, "a successful sign-in must be auditable"
        assert rows[0].summary is not None
        assert "email" in rows[0].summary


class TestLockout:
    async def test_account_locks_after_repeated_failures(
        self, api: AsyncClient, db: AsyncSession
    ) -> None:
        from app.core.config import settings

        target = "accounts@krb.example"
        for _ in range(settings.max_failed_logins):
            response = await api.post(
                "/auth/login", json={"identifier": target, "password": "definitely-wrong"}
            )
            assert response.status_code == 401

        # The next attempt reports the lock rather than a bad password, so the
        # user knows to wait instead of trying more combinations.
        locked = await api.post("/auth/login", json={"identifier": target, "password": PASSWORD})
        assert locked.status_code == 423
        assert "locked" in locked.json()["detail"].lower()

        user = (await db.execute(select(User).where(User.email == target))).scalar_one()
        assert user.locked_until is not None
        assert user.failed_login_count >= settings.max_failed_logins

    async def test_successful_login_clears_the_failure_counter(
        self, api: AsyncClient, db: AsyncSession
    ) -> None:
        target = "hr@krb.example"
        await api.post("/auth/login", json={"identifier": target, "password": "wrong-one"})
        await api.post("/auth/login", json={"identifier": target, "password": PASSWORD})

        user = (await db.execute(select(User).where(User.email == target))).scalar_one()
        assert user.failed_login_count == 0
        assert user.locked_until is None


class TestRefreshRotation:
    async def test_refresh_returns_a_different_token(self, api: AsyncClient) -> None:
        tokens = (
            await api.post("/auth/login", json={"identifier": ADMIN, "password": PASSWORD})
        ).json()

        rotated = await api.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert rotated.status_code == 200
        assert rotated.json()["refresh_token"] != tokens["refresh_token"]

    async def test_reusing_a_rotated_token_revokes_every_session(
        self, api: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A replayed refresh token means it leaked. Distrust the account.

        The revocation runs in its own committed transaction, precisely because
        the request is about to raise and the caller's transaction will be
        rolled back. Here that side effect is redirected into the test's own
        transaction so the assertions can see it.
        """
        from contextlib import asynccontextmanager

        from app.modules.identity.services import auth as auth_service

        @asynccontextmanager
        async def _test_factory():  # type: ignore[no-untyped-def]
            yield db

        monkeypatch.setattr(auth_service, "SessionFactory", _test_factory)

        first = (
            await api.post("/auth/login", json={"identifier": ADMIN, "password": PASSWORD})
        ).json()

        second = (
            await api.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
        ).json()

        replay = await api.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
        assert replay.status_code == 401

        # The token that was legitimately issued is now dead too.
        after = await api.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
        assert after.status_code == 401
        assert "revoked" in after.json()["detail"].lower()

    async def test_unknown_refresh_token_is_rejected(self, api: AsyncClient) -> None:
        response = await api.post("/auth/refresh", json={"refresh_token": "x" * 60})
        assert response.status_code == 401


class TestSessionRevocation:
    async def test_logout_revokes_only_this_session(
        self, api: AsyncClient, db: AsyncSession
    ) -> None:
        first = (
            await api.post("/auth/login", json={"identifier": ADMIN, "password": PASSWORD})
        ).json()
        second = (
            await api.post("/auth/login", json={"identifier": ADMIN, "password": PASSWORD})
        ).json()

        headers = {"Authorization": f"Bearer {first['access_token']}"}
        assert (await api.post("/auth/logout", headers=headers)).status_code == 200

        # The revoked session's access token stops working immediately, even
        # though it has not expired.
        assert (await api.get("/auth/me", headers=headers)).status_code == 401
        # The other session is untouched.
        other = {"Authorization": f"Bearer {second['access_token']}"}
        assert (await api.get("/auth/me", headers=other)).status_code == 200

    async def test_password_change_signs_out_other_sessions(
        self, api: AsyncClient, db: AsyncSession
    ) -> None:
        email = "pm.rsd@krb.example"
        old = (
            await api.post("/auth/login", json={"identifier": email, "password": PASSWORD})
        ).json()
        current = (
            await api.post("/auth/login", json={"identifier": email, "password": PASSWORD})
        ).json()

        changed = await api.post(
            "/auth/password/change",
            headers={"Authorization": f"Bearer {current['access_token']}"},
            json={"current_password": PASSWORD, "new_password": "A-Much-Longer-Passphrase-9"},
        )
        assert changed.status_code == 200

        stale = await api.get(
            "/auth/me", headers={"Authorization": f"Bearer {old['access_token']}"}
        )
        assert stale.status_code == 401

        # The session that made the change is the one that survives, and it
        # can still rotate its refresh token.
        still_here = await api.get(
            "/auth/me", headers={"Authorization": f"Bearer {current['access_token']}"}
        )
        assert still_here.status_code == 200
        rotated = await api.post("/auth/refresh", json={"refresh_token": current["refresh_token"]})
        assert rotated.status_code == 200

        live = (
            (
                await db.execute(
                    select(UserSession)
                    .join(User, User.id == UserSession.user_id)
                    .where(User.email == email, UserSession.revoked_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        assert len(live) == 1

    async def test_password_change_clears_the_forced_change_flag(self, api: AsyncClient) -> None:
        """Seeded accounts start with must_change_password; changing it clears it."""
        email = "ceo@krb.example"
        tokens = (
            await api.post("/auth/login", json={"identifier": email, "password": PASSWORD})
        ).json()
        assert tokens["user"]["must_change_password"] is True
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        changed = await api.post(
            "/auth/password/change",
            headers=headers,
            json={"current_password": PASSWORD, "new_password": "Another-Long-Passphrase-42"},
        )
        assert changed.status_code == 200
        me = (await api.get("/auth/me", headers=headers)).json()
        assert me["user"]["must_change_password"] is False


class TestPasswordPolicy:
    async def test_weak_new_password_is_refused_with_field_errors(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login("finance@krb.example")
        response = await api.post(
            "/auth/password/change",
            headers=headers,
            json={"current_password": PASSWORD, "new_password": "short"},
        )
        assert response.status_code == 422
        fields = {error["field"] for error in response.json()["errors"]}
        assert "new_password" in fields

    async def test_password_containing_the_username_is_refused(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login("procurement@krb.example")
        response = await api.post(
            "/auth/password/change",
            headers=headers,
            json={
                "current_password": PASSWORD,
                "new_password": "procurement-is-my-password",
            },
        )
        assert response.status_code == 422
        messages = " ".join(e["message"] for e in response.json()["errors"])
        assert "username or email" in messages

    async def test_wrong_current_password_is_refused(self, api: AsyncClient, login: Any) -> None:
        headers = await login("hr@krb.example")
        response = await api.post(
            "/auth/password/change",
            headers=headers,
            json={"current_password": "not-it", "new_password": "An-Acceptable-Passphrase-1"},
        )
        assert response.status_code == 401


class TestForgotPassword:
    @pytest.mark.parametrize("identifier", [ADMIN, "does-not-exist@krb.example"])
    async def test_response_is_identical_whether_or_not_the_account_exists(
        self, api: AsyncClient, identifier: str
    ) -> None:
        response = await api.post("/auth/password/forgot", json={"identifier": identifier})
        assert response.status_code == 202
        assert response.json()["message"] == "If that account exists, a reset link has been sent."

    async def test_the_token_is_never_returned_in_the_response(self, api: AsyncClient) -> None:
        body = (await api.post("/auth/password/forgot", json={"identifier": ADMIN})).text
        assert "token" not in body.lower()


class TestMe:
    async def test_returns_resolved_permissions_and_scopes(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(ADMIN)
        body = (await api.get("/auth/me", headers=headers)).json()

        assert body["is_superuser"] is True
        assert body["company"]["base_currency"] == "PKR"
        assert body["company"]["fiscal_year_start_month"] == 7
        assert len(body["permissions"]) > 100
        assert body["roles"][0]["role_code"] == "SUPER_ADMIN"

    async def test_site_staff_see_a_narrow_permission_set(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(SITE_STAFF)
        body = (await api.get("/auth/me", headers=headers)).json()

        assert body["is_superuser"] is False
        assert "deliveries.create" in body["permissions"]
        # The things site staff must not be able to do.
        for forbidden in (
            "deliveries.approve",
            "deliveries.review",
            "finance.gl.post",
            "rates.create",
            "users.assign_roles",
        ):
            assert forbidden not in body["permissions"], forbidden

        scope = body["scopes"]["deliveries.create"]
        assert scope["is_company_wide"] is False
        assert len(scope["site_ids"]) == 1

    async def test_auditor_is_read_only(self, api: AsyncClient, login: Any) -> None:
        headers = await login(AUDITOR)
        body = (await api.get("/auth/me", headers=headers)).json()

        assert body["is_read_only"] is True
        writes = [
            permission
            for permission in body["permissions"]
            if permission.rsplit(".", 1)[-1]
            not in {
                "view",
                "view_own",
                "view_salary",
                "view_financial",
                "view_valuation",
                "view_history",
                "export",
            }
        ]
        assert writes == [], f"Auditor holds write permissions: {writes}"

    async def test_requires_a_token(self, api: AsyncClient) -> None:
        assert (await api.get("/auth/me")).status_code == 401

    async def test_rejects_a_tampered_token(self, api: AsyncClient, login: Any) -> None:
        headers = await login(ADMIN)
        headers["Authorization"] = headers["Authorization"][:-3] + "abc"
        assert (await api.get("/auth/me", headers=headers)).status_code == 401
