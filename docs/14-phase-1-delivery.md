# 14 — Phase 1 Delivery: Foundation

Backend completed 2026-09-24. Deliverable format per §49 of the brief, mirroring
[13-phase-0-delivery](13-phase-0-delivery.md).

**Status: backend scope complete and verified live. Frontend scope not started —
see "What is not done" below.** [10-roadmap](10-roadmap.md)'s Phase 1 "done when"
criterion ("creating a vendor in the browser...") is therefore not yet met; the
API-level equivalent (creating a vendor through the API, with an audit entry, an
Auditor who can read but not write it) is met and covered by
`tests/integration/test_vendors.py`.

---

## Backend — modules delivered

| Module | What it does | Key files |
|---|---|---|
| `org` | Companies, projects, project phases, sites (PostGIS geofence), departments, cost centres. Company-level sites allowed (no project required). | `modules/org/models.py`, `services/project_service.py`, `services/geo.py` |
| `identity` | Users, sessions, refresh-token rotation with reuse-detection (revokes the whole chain), lockout, password reset, phone/email login. | `modules/identity/models.py`, `services/auth.py`, `services/user_service.py` |
| `access` | 124-entry permission catalogue, 11 seeded roles, scoped grants (GLOBAL/COMPANY/PROJECT/SITE/DEPARTMENT), `AccessContext`/`ScopeSet` resolved once per request and cached in Redis against `permissions_version`. | `modules/access/domain/{permissions,roles}.py`, `services/{grants,resolver,role_service}.py`, `core/access.py`, `core/scoping.py` |
| `masterdata` | Units, unit conversions (append-only, effective-dated, 2-hop chaining), material categories, materials, truck types, warehouses, and the **weighbridge calibration workflow** (§N-6): admin logs raw readings, sees avg/median/min/max/spread, explicitly confirms a factor — never auto-averaged. | `modules/masterdata/models.py`, `services/{conversion,calibration}.py` |
| `vendors` | Vendor CRUD, contacts, bank accounts (masked in audit), approval/suspend/blacklist state machine. | `modules/vendors/models.py`, `services/vendor_service.py` |
| `documents` | Attachments on any entity: presigned direct-to-storage upload, HEAD-verified confirm (never trusts the client's claimed size/type), list, presigned download, soft-remove with reason. | `platform/storage.py` (S3ObjectStore/MinIO), `modules/documents/services/attachment_service.py` |
| `notifications` | Channel-agnostic send (IN_APP always, EMAIL unless opted out), inbox, unread count, mark-read/mark-all-read, per-type-per-channel preferences. | `modules/notifications/services/notification_service.py` |
| outbox worker | Drains `outbox_events` (transactional outbox pattern) into real side effects. Handlers today: `user.role_granted`, `vendor.approved`/`suspended`/`blacklisted` → notifications. Most event types have no handler yet — deliberate, not a gap (see the module's own docstring). | `app/workers/tasks/outbox.py` |
| audit | Append-only audit log, DB-trigger-enforced against UPDATE/DELETE, before-flush diff hook. | `modules/audit/` |

Cross-cutting additions this phase: `__scope_self__` on Project/Site/Department so
scope-matching works without a separate dimension column; `parent_project_ids` on
`ScopeSet` so a site-scoped grant sees its parent project without leaking sibling
sites; `ScopedRepository[T]` generic CRUD; RFC 9457 error contract in active use
by every module.

## Database

Six migrations, one head, `alembic check` clean:

1. `da8b9fdc2b5a` — PostgreSQL extensions (postgis, pg_trgm, btree_gist, citext, ltree, pgcrypto).
2. `430307cd3075` — org, identity, access tables.
3. `2c50c3af7621` — platform tables: audit, attachments, notifications, outbox.
4. `323a90124451` — master data and vendors.
5. `1df44acb8972` — unit conversion calibrations.
6. `abdbd88f57ce` — `version` column added to `users` and `roles` (VersionMixin was missing entirely; caught by mypy strict, not by a functional test).

## Testing

315 tests passing, 80.11% coverage (gate is 80%), `ruff`/`mypy --strict`/
`lint-imports` (4 contracts kept, 0 broken)/`alembic check` all clean.

`tests/integration/`: `test_org_scoping.py`, `test_masterdata.py`,
`test_user_admin.py`, `test_vendors.py`, `test_auth_flow.py`,
`test_unit_conversion.py`, `test_attachments.py`, `test_notifications.py`,
`test_outbox_worker.py`. `tests/unit/`: `test_architecture.py` (the module-boundary
AST check), `test_core_types.py`, `test_pagination.py`, `test_security.py`.

## Bugs found and fixed via live exercise of the running stack

Every one of these was found by actually running the Docker stack (curl, psql,
real Celery worker logs) — none of them showed up from writing tests in
isolation first. Full detail in the `d77edc8` and `8837b55` commit messages;
summarised here as the record a new session should check before assuming any of
this code is untested:

- **PostGIS FOR UPDATE + `lazy="joined"`** — locking a row with an eager-loaded
  nullable-side join is rejected by Postgres. Fixed with `.options(noload("*"))`
  on every locking SELECT.
- **Outbox tasks silently never ran** — routed to a Redis queue (`outbox`) the
  worker container never listened on. Fixed in `docker-compose.yml`'s worker
  command (`-Q default,outbox,notifications,reports`).
- **Cross-event-loop asyncpg crash** — the module-level SQLAlchemy engine's
  pooled connections get bound to whichever event loop first used them, but
  Celery's `drain()`/`retry_failed()`/`check_database` call `asyncio.run()` on
  every invocation — a new loop each time. Fixed by disposing the engine's pool
  at the end of each task, inside the loop that owns the connections.
- **Worker process never imported all ORM models** — `app.main` calls
  `import_all_models()` at API startup; the worker had no equivalent, so a
  cross-module foreign key (`Notification.company_id -> Company`) raised
  `NoReferencedTableError` the first time it was touched in a fresh worker
  process. Fixed by calling it at `celery_app` import time.
- **Retry/backoff was completely broken** — a failing handler's `attempts`
  increment was flushed but not committed; the `except` branch's
  `session.rollback()` silently undid it along with the handler's own writes,
  so `_record_failure` always read `attempts=0` and `next_attempt_delay(0)`
  returns `None` ("exhausted") — every failure went straight to `DEAD` on the
  first try. Fixed by committing the bookkeeping write before the handler runs.
- **RBAC gaps**, each found by attempting the exact action as the exact seeded
  role and getting an unexpected 403: `PROCUREMENT_MANAGER` was missing
  `units.manage`/`units.manage_conversions`/`materials.deactivate`; **no role
  at all** had `attachments.delete`, so a wrongly-attached document could never
  be corrected by anyone but `SUPER_ADMIN`. Added to the relevant managerial
  roles; deliberately withheld from `SITE_STAFF`, who cannot approve or delete
  anything including their own entries.
- Several `MissingGreenlet`/autoflush/`.tuples()` bugs in service code —
  see commit `8837b55` for the full list; all have regression tests now.

## What is not done

- **Frontend**: still only the Phase-0 `SystemStatusPage`. No CRUD screens exist
  yet for projects, sites, users/roles, materials/units, or vendors, despite
  these being in Phase 1's roadmap scope. This is the largest gap against the
  roadmap's own Phase 1 definition and should be the next thing picked up if the
  priority is a usable browser UI rather than continuing backend phases.
- **CSV import for master data** — explicitly deferred by the user ("N-3:
  alright") until real master data is supplied. No action pending on this side.
- Document numbering (`platform/numbering.py`) exists and is tested but has no
  consumer yet — the first real user is Phase 2's purchase orders.

## Way forward

Backend-first continuation goes to **Phase 2 — Procurement & the approval
engine** ([10-roadmap](10-roadmap.md)): the approval engine (definitions,
condition evaluator, runtime, snapshotting, inbox, escalation) first, since PRs/
RFQs/POs all route through it, then PR → RFQ → Quotation → PO.

Alternatively, if a usable browser UI matters more right now than further
backend phases, the next step is the Phase 1 frontend: shell, auth, DataTable/
FilterBar, and CRUD for projects, sites, users/roles, materials/units, vendors —
all backed by APIs that already exist and are tested.
