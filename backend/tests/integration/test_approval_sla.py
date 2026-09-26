"""Approval SLA reminders and the nightly integrity check (docs/04 §5).

Seeded people: sm.gvh1 raises the request, pm.gvh is the first-step approver.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import utcnow
from app.modules.approvals.models import ApprovalRequestStep, ApprovalStepApprover
from app.modules.approvals.services import engine, integrity
from app.modules.notifications.models import Notification
from app.modules.procurement.models import PurchaseRequest
from app.platform.models import OutboxEvent
from app.workers.tasks import approvals as approval_tasks
from app.workers.tasks import outbox

pytestmark = pytest.mark.integration

REQUESTER = "sm.gvh1@krb.example"
PM = "pm.gvh@krb.example"


@pytest.fixture(autouse=True)
def _workers_use_the_test_session(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def _test_factory() -> Any:
        yield db

    async def _noop_dispose() -> None:
        return None

    for module in (approval_tasks, outbox):
        monkeypatch.setattr(module, "SessionFactory", _test_factory)
        monkeypatch.setattr(module, "dispose_engine", _noop_dispose)
    monkeypatch.setattr(outbox, "send_email", lambda **kwargs: None)


async def _pending_request(api: AsyncClient, login: Any) -> dict[str, Any]:
    """A submitted 50 000 request, waiting at the project manager (24 h SLA)."""
    requester = await login(REQUESTER)
    projects = (await api.get("/projects", headers=requester, params={"q": "GVH"})).json()["items"]
    project = next(p for p in projects if p["code"] == "GVH")
    sites = (
        await api.get("/sites", headers=requester, params={"project_id": project["id"]})
    ).json()
    site = next(s for s in sites["items"] if s["code"] == "GVH-S1")
    materials = (await api.get("/materials", headers=requester, params={"limit": 50})).json()
    material = next(m for m in materials["items"] if m["is_purchasable"])
    created = await api.post(
        "/purchase-requests",
        headers=requester,
        json={
            "project_id": project["id"],
            "site_id": site["id"],
            "justification": "SLA reminder test",
            "items": [
                {
                    "material_id": material["id"],
                    "unit_id": material["base_unit_id"],
                    "quantity": "1",
                    "estimated_rate": "50000",
                }
            ],
        },
    )
    submitted = await api.post(
        f"/purchase-requests/{created.json()['id']}/submit", headers=requester
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()  # type: ignore[no-any-return]


async def _set_progress(db: AsyncSession, request_id: str, elapsed: float) -> None:
    """Make the pending step look `elapsed` (0-1.5) of the way through its 24 h SLA."""
    now = utcnow()
    hours = 24
    await db.execute(
        update(ApprovalRequestStep)
        .where(
            ApprovalRequestStep.request_id == UUID(request_id),
            ApprovalRequestStep.status == "PENDING",
        )
        .values(
            activated_at=now - timedelta(hours=hours * elapsed),
            due_at=now + timedelta(hours=hours * (1 - elapsed)),
        )
    )


async def _reminders(db: AsyncSession) -> list[int]:
    rows = (
        (await db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "approval.reminder")))
        .scalars()
        .all()
    )
    return sorted(int(r.payload["percent"]) for r in rows)


class TestReminders:
    async def test_nothing_is_sent_early(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        await _set_progress(db, pr["approval_request_id"], 0.3)
        assert await engine.remind_due(db) == 0
        assert await _reminders(db) == []

    async def test_a_reminder_at_half_the_time_and_another_at_ninety_percent_once_each(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        request_id = pr["approval_request_id"]

        await _set_progress(db, request_id, 0.55)
        assert await engine.remind_due(db) == 1
        assert await engine.remind_due(db) == 0  # once

        await _set_progress(db, request_id, 0.93)
        assert await engine.remind_due(db) == 1
        assert await engine.remind_due(db) == 0
        assert await _reminders(db) == [50, 90]

    async def test_a_step_first_seen_late_gets_one_reminder_not_two(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        await _set_progress(db, pr["approval_request_id"], 0.95)
        assert await engine.remind_due(db) == 1
        assert await _reminders(db) == [90]
        # The 50 % reminder is marked done too: it would be stale now.
        step = (
            await db.execute(
                select(ApprovalRequestStep).where(
                    ApprovalRequestStep.request_id == UUID(pr["approval_request_id"])
                )
            )
        ).scalar_one()
        assert step.reminded_50_at is not None and step.reminded_90_at is not None

    async def test_an_overdue_step_is_escalated_not_reminded(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        await _set_progress(db, pr["approval_request_id"], 1.2)
        assert await engine.remind_due(db) == 0

    async def test_a_decided_request_is_not_reminded(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        await _set_progress(db, pr["approval_request_id"], 0.6)
        pm = await login(PM)
        done = await api.post(
            f"/approvals/requests/{pr['approval_request_id']}/approve",
            headers=pm,
            json={"comments": "ok"},
        )
        assert done.status_code == 200
        assert await engine.remind_due(db) == 0

    async def test_the_reminder_reaches_the_approver_as_a_notification(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        await _set_progress(db, pr["approval_request_id"], 0.6)
        assert await approval_tasks._remind_once() == 1
        await outbox._drain_once()

        pm = await login(PM)
        inbox = (await api.get("/notifications", headers=pm)).json()
        reminders = [n for n in inbox["items"] if n["notification_type"] == "APPROVAL_REMINDER"]
        assert len(reminders) == 1
        assert "Reminder" in reminders[0]["title"]
        assert "50%" in reminders[0]["body"]  # the first reminder point, reached at 60 %
        assert reminders[0]["link_path"] == f"/purchase-requests/{pr['id']}"


class TestIntegrityCheck:
    async def test_a_healthy_system_has_no_findings(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        await _pending_request(api, login)
        assert await integrity.find_inconsistencies(db) == []

    async def test_a_document_pending_without_a_request_is_found(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        # Simulate a manual database fix that put a draft into "pending".
        await db.execute(
            update(ApprovalRequestStep)
            .where(ApprovalRequestStep.request_id == UUID(pr["approval_request_id"]))
            .values(status="CANCELLED")
        )
        await db.execute(
            update(PurchaseRequest)
            .where(PurchaseRequest.id == UUID(pr["id"]))
            .values(status="DRAFT")
        )
        kinds = {f.kind for f in await integrity.find_inconsistencies(db)}
        assert "request_pending_on_settled_document" in kinds
        assert "request_without_active_step" in kinds

    async def test_a_pending_document_with_no_request_behind_it_is_found(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        # A second request that claims to be pending but never was submitted.
        requester = await login(REQUESTER)
        other = (await api.get(f"/purchase-requests/{pr['id']}", headers=requester)).json()
        clone = await api.post(
            "/purchase-requests",
            headers=requester,
            json={
                "project_id": other["project_id"],
                "site_id": other["site_id"],
                "justification": "Never submitted",
                "items": [
                    {
                        "material_id": other["items"][0]["material_id"],
                        "unit_id": other["items"][0]["unit_id"],
                        "quantity": "1",
                    }
                ],
            },
        )
        await db.execute(
            update(PurchaseRequest)
            .where(PurchaseRequest.id == UUID(clone.json()["id"]))
            .values(status="PENDING_APPROVAL")
        )
        findings = await integrity.find_inconsistencies(db)
        orphan = [f for f in findings if f.kind == "document_pending_without_request"]
        assert [str(f.doc_id) for f in orphan] == [clone.json()["id"]]
        assert orphan[0].company_id != UUID(int=0)

    async def test_a_step_nobody_can_decide_is_found(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        await db.execute(
            delete(ApprovalStepApprover).where(
                ApprovalStepApprover.request_step_id.in_(
                    select(ApprovalRequestStep.id).where(
                        ApprovalRequestStep.request_id == UUID(pr["approval_request_id"])
                    )
                )
            )
        )
        kinds = [f.kind for f in await integrity.find_inconsistencies(db)]
        assert kinds == ["step_without_approvers"]

    async def test_the_nightly_task_alarms_the_super_administrators(
        self, api: AsyncClient, login: Any, db: AsyncSession
    ) -> None:
        pr = await _pending_request(api, login)
        await db.execute(
            update(PurchaseRequest)
            .where(PurchaseRequest.id == UUID(pr["id"]))
            .values(status="DRAFT")
        )
        findings = await approval_tasks._reconcile_once()
        assert [f.kind for f in findings] == ["request_pending_on_settled_document"]
        await outbox._drain_once()

        admin = await login("admin@krb.example")
        inbox = (await api.get("/notifications", headers=admin)).json()
        alarms = [n for n in inbox["items"] if n["notification_type"] == "APPROVAL_INTEGRITY_ALARM"]
        assert len(alarms) == 1 and "1 problem" in alarms[0]["title"]
        assert alarms[0]["priority"] == "URGENT"

        stored = (
            (
                await db.execute(
                    select(Notification).where(
                        Notification.notification_type == "APPROVAL_INTEGRITY_ALARM"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(stored) >= 1
