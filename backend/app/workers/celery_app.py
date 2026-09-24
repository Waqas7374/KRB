"""Celery application.

Workers drain the transactional outbox, deliver notifications, run scheduled
reconciliations and build large report exports. Tasks never contain business
rules — they call the same services the API calls, inside a system context so
their writes are attributable.

Only tasks whose feature actually exists are registered and scheduled. The
planned schedule is kept in `PLANNED_SCHEDULE` below so the intent is visible
without scheduling jobs that would do nothing.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.schedules import schedule

from app.core.config import settings
from app.core.logging import configure_logging
from app.models_registry import import_all_models

# Every task module here is imported at process start (via `include` below),
# and SQLAlchemy only resolves a model's string-based ForeignKey targets once
# all referenced tables exist in Base.metadata. The API populates that
# metadata via import_all_models() in app.main; the worker has no equivalent
# entrypoint, so without this call the first flush that touches a
# cross-module FK (e.g. Notification.company_id -> Company) blows up with
# NoReferencedTableError the first time it runs in a fresh worker process.
import_all_models()

celery_app = Celery(
    "krb_erp",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks.diagnostics", "app.workers.tasks.outbox"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=900,
    task_soft_time_limit=840,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    task_default_queue="default",
    task_routes={
        "outbox.*": {"queue": "outbox"},
        "notifications.*": {"queue": "notifications"},
        "reports.*": {"queue": "reports"},
    },
)

celery_app.conf.beat_schedule = {
    # The outbox is the reliability backbone: a committed transaction always
    # produces its events, a rolled-back one never does. Drained close to
    # real time; the slower sweep is a safety net for ticks beat itself
    # might miss.
    "outbox.drain": {
        "task": "outbox.drain",
        "schedule": schedule(run_every=1.0),
        "options": {"queue": "outbox"},
    },
    "outbox.retry_failed": {
        "task": "outbox.retry_failed",
        "schedule": schedule(run_every=60.0),
        "options": {"queue": "outbox"},
    },
}

# Added to beat_schedule as each feature lands, in the phase noted.
PLANNED_SCHEDULE: dict[str, dict[str, Any]] = {
    "maintenance.expire_sessions": {"cron": "30 3 * * *", "phase": 1},
    # Phase 2
    "approvals.remind": {"cron": "0 * * * *", "phase": 2},
    "approvals.escalate": {"cron": "5 * * * *", "phase": 2},
    "approvals.reconcile": {"cron": "15 2 * * *", "phase": 2},
    # Phase 3 — balances are a cached projection of the append-only ledger;
    # this job proves they still agree and alarms if they do not.
    "inventory.reconcile_balances": {"cron": "0 2 * * *", "phase": 3},
    "inventory.low_stock_alerts": {"cron": "0 6 * * *", "phase": 3},
    "vendors.rebuild_performance_facts": {"cron": "0 3 * * *", "phase": 3},
    # Phase 6
    "reports.refresh_dashboard_aggregates": {"every_seconds": 300, "phase": 6},
}


@celery_app.on_after_configure.connect
def _setup_logging(sender: object, **_kwargs: object) -> None:
    configure_logging()
