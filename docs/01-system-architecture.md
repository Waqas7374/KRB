# 01 — System Architecture

## 1. Context diagram

```
+----------------+   +----------------+   +------------------+
|  ERP Web App   |   | Site Ledger    |   |  Scheduled jobs  |
|  React + Vite  |   | RN / Expo      |   |  (Celery beat)   |
|  desktop-first |   | mobile-first   |   |                  |
+-------+--------+   +-------+--------+   +--------+---------+
        | HTTPS/JSON         | HTTPS/JSON + sync    |
        | Bearer JWT         | Bearer JWT           |
        +----------+---------+----------------------+
                   v
        +----------------------------------+
        |   Nginx  (TLS, rate limit, gzip) |
        +--------------+-------------------+
                       v
        +--------------------------------------------------+
        |  FastAPI  (modular monolith, N uvicorn workers)   |
        |  +--------------------------------------------+  |
        |  | interface     routers / schemas / deps      |  |
        |  | application   services / use-cases / policy |  |
        |  | domain        entities / rules / machines   |  |
        |  | infrastructure repos / ORM / adapters       |  |
        |  +--------------------------------------------+  |
        +---+-----------+------------+----------+----------+
            v           v            v          v
     +-----------+ +---------+ +----------+ +------------+
     |PostgreSQL | |  Redis  | | S3 / R2  | |  SMTP      |
     | +PostGIS  | | cache,  | | objects  | |  email     |
     |           | | broker, | |          | |            |
     |           | | limiter | |          | |            |
     +-----------+ +----+----+ +----------+ +------------+
                        v
                 +-------------+
                 |Celery worker|  outbox drain, notifications,
                 |             |  report exports, reconciliations
                 +-------------+
```

Real-time (§2): a single **SSE** endpoint `/api/v1/events/stream` fed from Redis pub/sub. Used only
where it genuinely earns its place — the flagged-delivery review queue, the notification bell, and
long-running export completion. No WebSocket, because the traffic is server-to-client only.

---

## 2. Layering and the dependency rule

```
interface  -->  application  -->  domain
                     |
infrastructure ------+-->  domain      (implements ports declared by domain/application)
```

Enforced in CI by `import-linter` contracts (`backend/.importlinter`). Per module:

| Layer | Contains | May import |
|---|---|---|
| `api/` | FastAPI routers, request/response Pydantic schemas, dependency wiring | `services`, `schemas`, `core` |
| `services/` | Use-cases, orchestration, transaction boundary, authorisation, event emission | `repositories`, `domain`, `core`, other modules' **service interfaces only** |
| `domain/` | Entities, value objects, state machines, pure rules, invariants | stdlib + `core.types` only |
| `repositories/` | SQLAlchemy queries. No business logic | `models`, `core` |
| `models/` | SQLAlchemy ORM declarations | `core.db` |

**Cross-module calls go through the service layer only** — module A never imports module B's
repository or ORM model. Where that would be circular (inventory needs GL posting; GL needs nothing
from inventory), the dependency is inverted with a domain event.

A repository layer is used **only where it pays for itself** (§48, "where justified"): aggregates
with non-trivial queries — inventory, GL, approvals, deliveries, procurement. Simple master-data
modules (units, designations, categories) use a shared generic `CrudRepository[Model]` instead of
bespoke per-entity code.

---

## 3. Module map

```
app/modules/
  identity/        users, sessions, devices, password reset, MFA hooks
  access/          roles, permissions, scoped grants, permission resolution
  org/             companies, projects, sites (geofence), departments,
                   cost centres, project phases
  masterdata/      materials, categories, units, unit conversions,
                   truck types, warehouses
  vendors/         vendors, contacts, documents, categories, performance facts
  procurement/     purchase requests, RFQs, quotations, comparison, POs
  deliveries/      delivery capture, flags, review queue, GRN
  inventory/       stock ledger, balances, issues, transfers, adjustments
  finance/         chart of accounts, GL, AP, AR, payments, budgets,
                   commitments, accounting periods
  hr/              employees, designations, attendance, leave, payroll model
  workflow/        approval workflow engine (definitions + runtime)
  rules/           business-rule resolution (tonnage, geofence, tolerance,
                   approval limits, reorder levels)
  rating/          vendor rates, effective-dated history, rate resolution
  documents/       attachments, object-storage adapter, upload validation
  notifications/   channels, templates, preferences, in-app inbox
  reporting/       report definitions, query builder, Excel/PDF export
  sync/            mobile push/pull protocol, idempotency, device state
  audit/           audit log write path + query API
```

Cross-cutting platform code lives in `app/core/` and `app/platform/`:

| Package | Responsibility |
|---|---|
| `core/config.py` | Pydantic-Settings; one `Settings` object; fails fast on missing secrets |
| `core/db.py` | Async engine, session factory, `UnitOfWork`, transaction decorator |
| `core/security.py` | Argon2id hashing, JWT issue/verify, refresh rotation, lockout |
| `core/context.py` | Request `ContextVar`: user, company, ip, device, request_id |
| `core/errors.py` | Domain exception hierarchy to RFC 9457 `problem+json` |
| `core/pagination.py` | Cursor and offset pagination with one response envelope |
| `core/events.py` | Domain event base, outbox writer, handler registry |
| `platform/numbering.py` | Gapless document numbers per company / doc type / fiscal year |
| `platform/storage.py` | `ObjectStore` port; one S3-protocol adapter serves R2, B2, S3, MinIO |
| `modules/masterdata/services/conversion.py` | Unit-conversion resolution engine. It lives in the module that owns `units` and `unit_conversions`, because `platform` sits below `modules` in the layering |
| `platform/locking.py` | Postgres advisory locks for sequences and balance updates |

---

## 4. Cross-cutting mechanisms

### 4.1 Transaction boundary
One database transaction per use-case, opened in the service layer. Routers never commit. Every
write path that must be atomic across modules runs inside that single transaction. *Approve GRN*,
for example, writes in one transaction: stock ledger rows, balance update, commitment consumption,
GL journal entry and lines, outbox event, audit rows.

### 4.2 Domain events and the outbox
Services append to `outbox_events` **inside** the business transaction. A Celery beat task drains
the outbox roughly every second, dispatches to handlers, and marks rows processed with retry,
backoff and a dead-letter state. This is why a notification can never be sent for a transaction
that rolled back, and why a committed transaction can never silently fail to notify.

### 4.3 Idempotency
`POST` and `PATCH` accept an `Idempotency-Key` header. Table `idempotency_keys(company_id, key,
endpoint, request_hash, response_status, response_body, created_at)` with a unique index. A repeat
with the same key and the same body replays the stored response; the same key with a different body
returns `409`. Mobile sync uses the client-generated record UUID as the key, so a retry after a
dropped response is safe.

### 4.4 Audit
`core/context.py` holds the actor. The `UnitOfWork` inspects the session's new/dirty/deleted sets
before flush and writes `audit_logs` rows with a JSON diff of changed columns for any model marked
`__audited__ = True`. Sensitive columns are excluded via `__audit_exclude__` (password hashes,
tokens). Rate changes, approvals and rejections additionally write a business-level audit entry
with a human-readable summary — `Crush / Shree Stone: 48.0000 -> 52.0000 effective 12 Aug`.

### 4.5 Authorisation
Three checks, in order, on every protected endpoint:
1. **Authentication** — valid, non-revoked access token.
2. **Permission** — `require("procurement.approve")` dependency.
3. **Scope** — the resolved grant must cover the row's `company_id` / `project_id` / `site_id` /
   `department_id`. Enforced by injecting a `ScopeFilter` into every repository query, **not** by
   filtering results after the fact. See [03-rbac](03-rbac.md).

### 4.6 Error contract
All errors return RFC 9457 `application/problem+json`:

```json
{
  "type": "https://errors.krb-erp/validation",
  "title": "Validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/api/v1/purchase-orders",
  "request_id": "01J8ZV...",
  "errors": [{ "field": "items.0.quantity", "code": "greater_than", "message": "must be > 0" }]
}
```

---

## 5. Repository folder structure

```
KRB-ERP/
  README.md
  docs/                            this documentation set
  docker-compose.yml               local dev: db, redis, minio, api, worker, web
  docker-compose.prod.yml
  .env.example
  Makefile                         make up / migrate / seed / test / lint

  backend/
    Dockerfile
    pyproject.toml                 deps, ruff, mypy, pytest config
    .importlinter
    alembic.ini
    alembic/versions/
    app/
      main.py                      app factory, middleware, router mounting
      core/                        config, db, security, context, errors, events
      platform/                    numbering, storage, units, locking
      modules/<module>/
        api/routes.py
        schemas.py
        services/
        domain/
        repositories/
        models.py
        events.py
      workers/                     celery app, beat schedule, task modules
      seeds/                       idempotent demo-data loaders
    tests/
      unit/  integration/  api/  authz/  conftest.py

  web/
    Dockerfile
    vite.config.ts  tailwind.config.ts  tsconfig.json
    src/
      app/                         router, providers, layouts, error boundary
      features/<feature>/          api.ts, schemas.ts, hooks.ts, components/, routes/
      components/ui/               shadcn primitives
      components/data/             DataTable, FilterBar, SavedViews, Drawer
      lib/                         api client, auth, permissions, formatters
      types/api.d.ts               generated from OpenAPI
    tests/

  mobile/
    app.json  eas.json
    src/
      screens/                     Login, Home, NewDelivery, Queue, History
      db/                          SQLite schema, migrations, outbox DAO
      sync/                        engine, backoff, conflict handling
      api/                         generated client, auth interceptor
      components/
    tests/

  packages/
    api-types/                     OpenAPI-generated TS types shared by web + mobile

  e2e/                             Playwright specs + fixtures
  infra/
    nginx/  systemd/  backup/      pgBackRest config, restore-drill script
    terraform/                     optional, Stage 3+
  .github/workflows/               ci.yml, deploy.yml, migrations.yml
```

---

## 6. Non-functional targets (v1)

| Concern | Target |
|---|---|
| Read p95 (list endpoint, 50 rows) | < 250 ms server-side |
| Write p95 (PO create, GRN approve) | < 600 ms |
| Mobile delivery capture, online | <= 3 s save to confirmed |
| Mobile delivery capture, offline | <= 300 ms local commit |
| Concurrent users, Stage 1 | 150 active / 1 500 registered |
| Deliveries per day, Stage 1 | 5 000 |
| RPO / RTO | 5 min (WAL archiving) / 1 h |
| Audit retention | Indefinite; monthly partitions from year 2 |
