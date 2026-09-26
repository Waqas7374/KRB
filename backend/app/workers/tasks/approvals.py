"""Approval SLA enforcement and integrity (docs/04 §5).

* `approvals.remind`    - nudge approvers at 50 % and 90 % of a step's SLA, once each.
* `approvals.escalate`  - apply each overdue step's escalation policy once.
* `approvals.reconcile` - nightly proof that approvals and documents still agree.

The logic lives in the engine and services/integrity.py; these are only the
scheduled entry points.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.context import system_context
from app.core.db import SessionFactory, dispose_engine
from app.core.logging import get_logger
from app.modules.approvals.services import engine, integrity
from app.platform import outbox
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


async def _remind_once() -> int:
    try:
        with system_context("approvals-remind"):
            async with SessionFactory() as session:
                count = await engine.remind_due(session)
                await session.commit()
                return count
    finally:
        await dispose_engine()


@celery_app.task(name="approvals.remind")
def remind() -> dict[str, Any]:
    reminded = asyncio.run(_remind_once())
    if reminded:
        log.info("approvals.reminded", steps=reminded)
    return {"reminded": reminded}


async def _reconcile_once() -> list[integrity.Finding]:
    try:
        with system_context("approvals-reconcile"):
            async with SessionFactory() as session:
                findings = await integrity.find_inconsistencies(session)
                # One alarm per company, listing what was found, so a bad night
                # produces one message rather than a hundred.
                by_company: dict[Any, list[integrity.Finding]] = {}
                for finding in findings:
                    by_company.setdefault(finding.company_id, []).append(finding)
                for company_id, items in by_company.items():
                    await outbox.emit(
                        session,
                        outbox.DomainEvent(
                            event_type="approval.integrity_alarm",
                            aggregate_type="Approvals",
                            aggregate_id=company_id,
                            payload={
                                "count": len(items),
                                "findings": [
                                    {
                                        "kind": f.kind,
                                        "doc_type": f.doc_type,
                                        "doc_id": str(f.doc_id) if f.doc_id else None,
                                        "detail": f.detail,
                                    }
                                    for f in items[:20]
                                ],
                            },
                            company_id=company_id,
                        ),
                    )
                await session.commit()
                return findings
    finally:
        await dispose_engine()


@celery_app.task(name="approvals.reconcile")
def reconcile() -> dict[str, Any]:
    findings = asyncio.run(_reconcile_once())
    for f in findings:
        log.error(
            "approvals.integrity_violation",
            kind=f.kind,
            doc_type=f.doc_type,
            doc_id=str(f.doc_id) if f.doc_id else None,
            detail=f.detail,
        )
    return {"findings": len(findings)}
