"""The outbox drain worker end to end.

`app.workers.tasks.outbox` runs outside any HTTP request: `_drain_once()`
opens its own sessions via a module-level `SessionFactory` and, in
production, is invoked once per event loop that `asyncio.run()` spins up on
every Celery-beat tick — the reason it also disposes the shared engine's pool
on the way out (see the comment in `outbox.py`): a pooled connection must
never survive from one tick's loop into the next tick's *different* loop.

These tests redirect that `SessionFactory` into the test's own transaction
(the same trick `test_auth_flow.py` uses for `auth.SessionFactory`) so the
drain's writes are visible to assertions and roll back with everything else,
and stub out `dispose_engine` so it does not tear down the shared test
connection that the redirect depends on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow, uuid7
from app.modules.notifications.models import Notification
from app.platform.domain.enums import OutboxStatus
from app.platform.models import OutboxEvent
from app.platform.outbox import MAX_ATTEMPTS
from app.workers.tasks import outbox

pytestmark = pytest.mark.integration

ADMIN = "admin@krb.example"


@pytest.fixture(autouse=True)
def _outbox_uses_the_test_session(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def _test_factory() -> Any:
        yield db

    async def _noop_dispose() -> None:
        return None

    monkeypatch.setattr(outbox, "SessionFactory", _test_factory)
    monkeypatch.setattr(outbox, "dispose_engine", _noop_dispose)


async def _role_id(api: AsyncClient, headers: dict[str, str], code: str) -> str:
    body = (await api.get("/roles", headers=headers, params={"q": code, "limit": 5})).json()
    match = next(r for r in body["items"] if r["code"] == code)
    return str(match["id"])


def _pending_event(event_type: str, **overrides: Any) -> OutboxEvent:
    defaults: dict[str, Any] = {
        "id": uuid7(),
        "event_type": event_type,
        "aggregate_type": "Test",
        "aggregate_id": uuid7(),
        "payload": {},
        "company_id": None,
        "status": OutboxStatus.PENDING.value,
        "occurred_at": utcnow(),
        "next_attempt_at": utcnow(),
    }
    defaults.update(overrides)
    return OutboxEvent(**defaults)


class TestDrainProcessesRoleGrantEvents:
    async def test_a_role_grant_is_drained_into_in_app_and_email_notifications(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        admin_headers = await login(ADMIN)
        invited = (
            await api.post(
                "/users",
                headers=admin_headers,
                json={"email": "outbox-grantee@krb.example", "full_name": "Outbox Grantee"},
            )
        ).json()["user"]
        role_id = await _role_id(api, admin_headers, "SITE_STAFF")
        sites = (await api.get("/sites", headers=admin_headers, params={"limit": 1})).json()
        site_id = sites["items"][0]["id"]

        grant = await api.post(
            f"/users/{invited['id']}/role-grants",
            headers=admin_headers,
            json={"role_id": role_id, "scope_type": "SITE", "scope_id": site_id},
        )
        assert grant.status_code == 201

        event = (
            await db.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "user.role_granted",
                    OutboxEvent.aggregate_id == UUID(invited["id"]),
                )
            )
        ).scalar_one()
        assert event.status == OutboxStatus.PENDING.value

        outcomes = await outbox._drain_once()
        assert outcomes.get("processed", 0) >= 1

        await db.refresh(event)
        assert event.status == OutboxStatus.PROCESSED.value
        assert event.attempts == 1
        assert event.processed_at is not None

        notifications = (
            (
                await db.execute(
                    select(Notification).where(Notification.entity_id == UUID(invited["id"]))
                )
            )
            .scalars()
            .all()
        )
        channels = {n.channel for n in notifications}
        assert channels == {"IN_APP", "EMAIL"}
        assert all(n.notification_type == "ROLE_GRANTED" for n in notifications)
        assert "SITE_STAFF" in notifications[0].body


class TestDrainHandlesUnregisteredEventTypes:
    async def test_an_event_with_no_handler_is_processed_with_no_notification(
        self, db: AsyncSession
    ) -> None:
        """Most event types (`project.created`, `site.created`, ...) have no
        handler today — a deliberate gap, not a bug (see the module
        docstring), so drain must mark them PROCESSED rather than retry
        forever waiting for one to appear."""
        event = _pending_event("project.created")
        db.add(event)
        await db.flush()

        outcomes = await outbox._drain_once()
        assert outcomes.get("no-handler", 0) >= 1

        await db.refresh(event)
        assert event.status == OutboxStatus.PROCESSED.value
        assert event.processed_at is not None


class TestDrainRetriesAFailingHandler:
    async def test_a_raising_handler_schedules_a_retry_with_the_error_recorded(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(session: AsyncSession, event: OutboxEvent) -> None:
            raise RuntimeError("simulated handler failure")

        monkeypatch.setitem(outbox._HANDLERS, "test.always_fails", _boom)

        event = _pending_event("test.always_fails")
        db.add(event)
        await db.flush()

        outcomes = await outbox._drain_once()
        assert outcomes.get("retrying", 0) >= 1

        await db.refresh(event)
        assert event.status == OutboxStatus.RETRYING.value
        assert event.attempts == 1
        assert "simulated handler failure" in (event.last_error or "")
        assert event.next_attempt_at is not None
        assert event.next_attempt_at > utcnow()

    async def test_a_handler_that_keeps_failing_eventually_goes_dead(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(session: AsyncSession, event: OutboxEvent) -> None:
            raise RuntimeError("simulated handler failure")

        monkeypatch.setitem(outbox._HANDLERS, "test.always_fails_repeatedly", _boom)

        event = _pending_event("test.always_fails_repeatedly")
        db.add(event)
        await db.flush()

        for _ in range(MAX_ATTEMPTS):
            # Force the row due immediately instead of waiting out the real
            # backoff schedule (up to six hours by the last step).
            event.next_attempt_at = utcnow()
            await db.flush()
            await outbox._drain_once()
            await db.refresh(event)
            if event.status == OutboxStatus.DEAD.value:
                break

        assert event.status == OutboxStatus.DEAD.value
        assert event.attempts == MAX_ATTEMPTS


class TestDrainSkipsLockedOrAlreadyFinishedEvents:
    async def test_an_already_processed_event_is_skipped_not_reprocessed(
        self, db: AsyncSession
    ) -> None:
        event = _pending_event(
            "test.already_done",
            status=OutboxStatus.PROCESSED.value,
            attempts=1,
            processed_at=utcnow(),
        )
        db.add(event)
        await db.flush()

        outcome = await outbox._process_event(event.id)
        assert outcome == "skipped"

        await db.refresh(event)
        assert event.attempts == 1
