"""Inventory integrity (docs/02 §7).

`inventory.reconcile_balances` proves each night that the cached balances still
equal the sum of the append-only ledger, and raises an alarm if they do not.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.context import system_context
from app.core.db import SessionFactory, dispose_engine
from app.core.logging import get_logger
from app.modules.inventory.services import reconcile
from app.platform import outbox
from app.workers.celery_app import celery_app

log = get_logger("worker.inventory")


async def _reconcile_once() -> list[reconcile.Drift]:
    try:
        with system_context("inventory-reconcile"):
            async with SessionFactory() as session:
                drift = await reconcile.find_drift(session)
                by_company: dict[Any, list[reconcile.Drift]] = {}
                for item in drift:
                    by_company.setdefault(item.company_id, []).append(item)
                for company_id, items in by_company.items():
                    await outbox.emit(
                        session,
                        outbox.DomainEvent(
                            event_type="inventory.integrity_alarm",
                            aggregate_type="Inventory",
                            aggregate_id=company_id,
                            payload={
                                "count": len(items),
                                "findings": [{"detail": i.detail} for i in items[:20]],
                            },
                            company_id=company_id,
                        ),
                    )
                await session.commit()
                return drift
    finally:
        await dispose_engine()


@celery_app.task(name="inventory.reconcile_balances")
def reconcile_balances() -> dict[str, Any]:
    drift = asyncio.run(_reconcile_once())
    for item in drift:
        log.error(
            "inventory.balance_drift",
            warehouse_id=str(item.warehouse_id),
            material_id=str(item.material_id),
            detail=item.detail,
        )
    return {"drift": len(drift)}
