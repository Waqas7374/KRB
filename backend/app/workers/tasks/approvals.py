"""Approval SLA enforcement (docs/04 §5).

`approvals.escalate` applies each overdue step's escalation policy once. The
logic lives in the engine; this is only the scheduled entry point.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.context import system_context
from app.core.db import SessionFactory, dispose_engine
from app.core.logging import get_logger
from app.modules.approvals.services import engine
from app.workers.celery_app import celery_app

log = get_logger("worker.approvals")


async def _escalate_once() -> int:
    try:
        with system_context("approvals-escalate"):
            async with SessionFactory() as session:
                count = await engine.escalate_overdue(session)
                await session.commit()
                return count
    finally:
        # Same reason as the outbox drain: each tick runs in a fresh event
        # loop, so pooled connections must not outlive this one.
        await dispose_engine()


@celery_app.task(name="approvals.escalate")
def escalate() -> dict[str, Any]:
    escalated = asyncio.run(_escalate_once())
    if escalated:
        log.info("approvals.escalated", steps=escalated)
    return {"escalated": escalated}
