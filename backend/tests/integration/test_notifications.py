"""The in-app notification inbox and channel preferences end to end.

Notifications are never created from a request handler directly (see
`app.modules.notifications.services.notification_service`'s docstring) —
they come from the outbox drain, which is covered separately in
`test_outbox_worker.py`. These tests exercise the inbox/preferences API
against rows created the same way the drain creates them, via
`notification_service.send()` on the request's own session.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User
from app.modules.notifications.domain.enums import NotificationChannel, NotificationType
from app.modules.notifications.services import notification_service

pytestmark = pytest.mark.integration

ADMIN = "admin@krb.example"
HR = "hr@krb.example"


async def _user_id(db: AsyncSession, email: str) -> Any:
    return (await db.execute(select(User.id).where(User.email == email))).scalar_one()


async def _send(db: AsyncSession, *, user_id: Any, title: str = "Test notification") -> None:
    admin_id = (await db.execute(select(User.company_id).where(User.id == user_id))).scalar_one()
    await notification_service.send(
        db,
        company_id=admin_id,
        user_id=user_id,
        notification_type=NotificationType.ROLE_GRANTED.value,
        title=title,
        body="Body text",
    )
    await db.flush()


class TestInbox:
    async def test_lists_only_the_callers_own_notifications(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        admin_id = await _user_id(db, ADMIN)
        hr_id = await _user_id(db, HR)
        await _send(db, user_id=admin_id, title="For admin")
        await _send(db, user_id=hr_id, title="For HR")

        headers = await login(ADMIN)
        body = (await api.get("/notifications", headers=headers)).json()

        titles = {item["title"] for item in body["items"]}
        assert "For admin" in titles
        assert "For HR" not in titles

    async def test_unread_count_and_unread_only_filter(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        admin_id = await _user_id(db, ADMIN)
        await _send(db, user_id=admin_id, title="Unread one")
        await _send(db, user_id=admin_id, title="Unread two")

        headers = await login(ADMIN)
        body = (await api.get("/notifications", headers=headers)).json()
        assert body["unread_count"] >= 2

        unread_only = (
            await api.get("/notifications", headers=headers, params={"unread_only": True})
        ).json()
        assert all(item["read_at"] is None for item in unread_only["items"])


class TestMarkRead:
    async def test_marking_one_notification_read(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        admin_id = await _user_id(db, ADMIN)
        await _send(db, user_id=admin_id, title="Mark me read")

        headers = await login(ADMIN)
        inbox = (await api.get("/notifications", headers=headers)).json()
        target = next(i for i in inbox["items"] if i["title"] == "Mark me read")
        assert target["read_at"] is None

        response = await api.post(f"/notifications/{target['id']}/read", headers=headers)
        assert response.status_code == 200
        assert response.json()["read_at"] is not None

    async def test_cannot_mark_someone_elses_notification_read(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        hr_id = await _user_id(db, HR)
        await _send(db, user_id=hr_id, title="Belongs to HR")

        # Query as HR to find the real id, then attempt the mark-read as admin.
        hr_headers = await login(HR)
        hr_inbox = (await api.get("/notifications", headers=hr_headers)).json()
        target_id = next(i for i in hr_inbox["items"] if i["title"] == "Belongs to HR")["id"]

        admin_headers = await login(ADMIN)
        response = await api.post(f"/notifications/{target_id}/read", headers=admin_headers)
        assert response.status_code == 404

    async def test_mark_all_read(self, api: AsyncClient, login: Any, db: AsyncSession) -> None:
        admin_id = await _user_id(db, ADMIN)
        await _send(db, user_id=admin_id, title="Bulk one")
        await _send(db, user_id=admin_id, title="Bulk two")

        headers = await login(ADMIN)
        response = await api.post("/notifications/read-all", headers=headers)
        assert response.status_code == 204

        after = (await api.get("/notifications", headers=headers)).json()
        assert after["unread_count"] == 0


class TestPreferences:
    async def test_absence_of_a_preference_row_means_the_channel_is_enabled(
        self, db: AsyncSession
    ) -> None:
        admin_id = await _user_id(db, ADMIN)
        enabled = await notification_service.is_channel_enabled(
            db,
            user_id=admin_id,
            notification_type=NotificationType.ROLE_GRANTED.value,
            channel=NotificationChannel.EMAIL,
        )
        assert enabled is True

    async def test_disabling_a_channel_stops_that_channels_row_being_created(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        admin_id = await _user_id(db, ADMIN)
        headers = await login(ADMIN)

        response = await api.put(
            "/notifications/preferences",
            headers=headers,
            json={
                "notification_type": NotificationType.ROLE_GRANTED.value,
                "channel": "EMAIL",
                "is_enabled": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["is_enabled"] is False

        created = await notification_service.send(
            db,
            company_id=(
                await db.execute(select(User.company_id).where(User.id == admin_id))
            ).scalar_one(),
            user_id=admin_id,
            notification_type=NotificationType.ROLE_GRANTED.value,
            title="Should be in-app only",
            body="Body",
        )
        channels = {n.channel for n in created}
        assert channels == {"IN_APP"}

    async def test_preferences_round_trip_through_the_get_endpoint(
        self, api: AsyncClient, login: Any
    ) -> None:
        headers = await login(ADMIN)
        await api.put(
            "/notifications/preferences",
            headers=headers,
            json={
                "notification_type": NotificationType.VENDOR_STATUS_CHANGED.value,
                "channel": "EMAIL",
                "is_enabled": False,
            },
        )
        body = (await api.get("/notifications/preferences", headers=headers)).json()
        match = next(
            p
            for p in body
            if p["notification_type"] == NotificationType.VENDOR_STATUS_CHANGED.value
            and p["channel"] == "EMAIL"
        )
        assert match["is_enabled"] is False

    async def test_preferences_list_every_type_with_defaults_filled_in(
        self, api: AsyncClient, login: Any
    ) -> None:
        """Only explicit choices are stored; the settings screen still needs the
        whole matrix, with untouched combinations shown as enabled."""
        headers = await login(ADMIN)
        body = (await api.get("/notifications/preferences", headers=headers)).json()

        pairs = {(p["notification_type"], p["channel"]) for p in body}
        assert pairs == {(t.value, c) for t in NotificationType for c in ("IN_APP", "EMAIL")}
        untouched = next(
            p
            for p in body
            if p["notification_type"] == NotificationType.LEAVE_APPROVED.value
            and p["channel"] == "IN_APP"
        )
        assert untouched["is_enabled"] is True
