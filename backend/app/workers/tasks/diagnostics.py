"""Diagnostic tasks.

Used by the deploy smoke test and by `make ps` troubleshooting to prove the
broker, the worker and the database are all reachable from the worker process —
which is a different network path from the API's.
"""

from __future__ import annotations

from typing import Any

from app.core.context import system_context
from app.core.db import check_database
from app.core.logging import get_logger
from app.core.types import utcnow
from app.workers.celery_app import celery_app

log = get_logger("worker.diagnostics")


@celery_app.task(name="diagnostics.ping")
def ping() -> dict[str, Any]:
    log.info("worker.ping")
    return {"status": "ok", "at": utcnow().isoformat()}


@celery_app.task(name="diagnostics.check_database")
def check_db() -> dict[str, Any]:
    import asyncio

    with system_context("diagnostics"):
        reachable = asyncio.run(check_database())
    log.info("worker.database_check", reachable=reachable)
    return {"database_reachable": reachable, "at": utcnow().isoformat()}
