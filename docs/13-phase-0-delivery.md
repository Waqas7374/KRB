# 13 — Phase 0 Delivery: Scaffold

Completed 2026-09-22. Deliverable format per §49 of the brief.

---

## Architecture — what was implemented and why

The skeleton of the modular monolith described in
[01-system-architecture](01-system-architecture.md), with every cross-cutting
mechanism in place **before** any business module, so no module has to invent
its own error format, transaction handling or audit hook.

| Piece | File | Why it exists now rather than later |
|---|---|---|
| Settings with fail-fast validation | `backend/app/core/config.py` | The process refuses to boot on a missing secret, a sync driver in `DATABASE_URL`, or production-grade env with a placeholder key. Cheaper than discovering it at 3 a.m. |
| Request context | `backend/app/core/context.py` | The audit writer and the logger need the acting user without threading it through every signature. `system_context()` marks machine-originated writes so they are never mistaken for human ones. |
| Error hierarchy → RFC 9457 | `core/errors.py`, `core/exception_handlers.py` | One wire contract, defined once. Services raise domain exceptions and never build HTTP responses; the same exception works from a Celery task. |
| Declarative base, mixins, unit of work | `core/db.py` | The naming convention is set before the first migration — retrofitting it renames every constraint in the database. `UnitOfWork` establishes the one-transaction-per-use-case rule. |
| Money and UUID primitives | `core/types.py` | `NUMERIC(18,4)` and UUIDv7 are decided once, in one place, so no model can accidentally use a float for a rate. |
| Security | `core/security.py` | Argon2id, HMAC-hashed opaque refresh tokens, JWTs that deliberately carry **no** permissions, exponential lockout, NIST-style password policy. |
| Pagination and sorting | `core/pagination.py` | One collection envelope; unknown sort fields are a 422 rather than a silent no-op. |
| Middleware | `core/middleware.py` | Request id on every response, one structured access-log line per request, baseline security headers. |
| Structured logging | `core/logging.py` | Every line carries `request_id`, `user_id`, `company_id`. |
| Celery app | `app/workers/` | Broker wiring and the queue layout. Only tasks whose feature exists are registered; the planned schedule is data in `PLANNED_SCHEDULE`, so nothing is scheduled that would do nothing. |
| Model registry | `app/models_registry.py` | Alembic autogenerate emits `DROP TABLE` for models it cannot see. One import site makes that failure impossible. |
| Architecture contracts | `backend/.importlinter` | The layering and "modules expose services only" rules are enforced by CI from commit one, not by memory. |

Two decisions were changed from the design documents during implementation, both
recorded in code comments:

1. **`NoDecode` on list settings.** pydantic-settings JSON-decodes complex types
   before field validators run, so a comma-separated `CORS_ORIGINS` was a hard
   startup error. The `NoDecode` annotation defers to our own parser.
2. **Actor stamping listens on `Session`, not `AsyncSession`.** ORM flush events
   are emitted by the synchronous session that `AsyncSession` drives; a listener
   registered on `AsyncSession` never fires.

## Database

- PostgreSQL 16 + PostGIS via `postgis/postgis:16-3.4`.
- Alembic configured with a single head, UTC timestamped revision filenames,
  `compare_type`/`compare_server_default` on, PostGIS's own tables excluded from
  autogenerate, and ruff as a post-write hook (`exec` type — ruff is a native
  binary, not a Python entry point).
- Constraint naming convention set in `MetaData`, so every constraint is
  droppable on downgrade and index names are identical on every machine.
- `script.py.mako` carries a review checklist (no hot-table rewrites,
  `CREATE INDEX CONCURRENTLY`, expand→contract, implement `downgrade`).
- **One migration:** `da8b9fdc2b5a` enables `postgis`, `pg_trgm`, `btree_gist`,
  `citext`, `ltree`, `pgcrypto`. Not reversed on downgrade — `DROP EXTENSION
  postgis CASCADE` would silently delete every geography column.

No business tables yet; those land in Phase 1.

## Backend — endpoints

| Endpoint | Behaviour |
|---|---|
| `GET /health` | Liveness. Touches nothing — a health check that queries the database turns a slow query into a restart loop. |
| `GET /ready` | Readiness. Probes PostgreSQL and Redis, returns a per-component report, and answers `503` while still returning the full body. |
| `GET /version` | App version, git sha, environment, migration head. |
| `GET /metrics` | Prometheus, excluded from OpenAPI. |
| `GET /docs`, `/redoc`, `/api/v1/openapi.json` | Disabled in production. |

`/api/v1` is mounted and empty, awaiting Phase 1 routers.

## Frontend — screens and components

| Piece | Detail |
|---|---|
| Stack | React 18, TypeScript strict (incl. `noUncheckedIndexedAccess`), Vite 6, React Router 6, TanStack Query 5, Tailwind 4, Vitest 3 |
| Design tokens | `src/styles/index.css` — light and dark, 14 px body, tabular numerics for amount columns, `prefers-reduced-motion` respected |
| API client | `src/lib/api.ts` — one place for base URL, bearer token, `Idempotency-Key`, `If-Match`, array filters as repeated params, and the RFC 9457 contract parsed into `ApiError.fieldErrors` |
| Query defaults | `src/app/query-client.ts` — no retry on definitively-rejected requests, **no automatic mutation retry** (a retried POST is how duplicate purchase orders happen) |
| Error boundary | `src/components/error-boundary.tsx` — route-level and global, so one broken panel does not blank the app |
| System status page | `src/features/system/SystemStatusPage.tsx` — live dependency report, auto-refreshing. Every value comes from a real request; nothing is mocked. It stays on as an operator diagnostics screen. |
| Formatters | `src/lib/utils.ts` — amounts arrive as decimal strings and are converted to a JS number only for display, in one function |

Bundle: **83 KB gzipped** total across four chunks (target was under 200 KB).

## Mobile

Not started in Phase 0. The earlier prototype is now `mobile/` (its stub backend was retired; the app
uses the one ERP backend) and is audited in [00-context-and-scope](00-context-and-scope.md).

## Testing

**62 backend tests, 22 web tests, all passing. Backend coverage 80.8%** against
an 80% gate.

| Suite | Covers |
|---|---|
| `tests/unit/test_core_types.py` | UUIDv7 version, uniqueness and **time-ordering** (the property UUIDv4 lacks); money half-up rounding; string-decimal arithmetic; PKR minor units |
| `tests/unit/test_security.py` | Argon2id hashing and salting; unknown-user verification burning equal CPU; opaque-token hashing; JWT round-trip; **permissions absent from the token**; tampered and expired tokens; refresh token rejected as access; exponential capped lockout; password policy |
| `tests/unit/test_pagination.py` | Limit and deep-offset caps; sort-injection rejection; ORDER BY generation; stable tiebreaker; unknown sort field → 422; cursor round-trip and URL safety; envelope `has_more` in both modes |
| `tests/api/test_system_endpoints.py` | Health without infrastructure; request-id generation and echo; security headers; problem-document shape for not-found, business-rule, unhandled and validation errors |
| `web/src/lib/api.test.ts` | Bearer and idempotency headers; array and empty query handling; field errors; 401 handler; retryability by status; transport errors; 204 |
| `web/src/lib/utils.test.ts` | Class merging; money/quantity/date formatting incl. missing and unparseable input, and timezone day boundaries |

Coverage omits bootstrap-only modules (`main.py`, logging config, celery app,
model registry) — asserting that configuration equals itself is not a test.

## Deployment

- `docker-compose.yml`: db (PostGIS), redis, minio + bucket init, mailpit, api,
  worker, web. Health checks and dependency conditions wired so `make up`
  produces a working stack in one command.
- `backend/Dockerfile`: multi-stage — builder compiles into a venv, `development`
  adds dev tooling, `production` runs as non-root with no build toolchain and a
  gunicorn/uvicorn worker pool.
- `web/Dockerfile`: multi-stage — `development` runs Vite, `production` serves
  the built assets from nginx with immutable asset caching and SPA fallback.
- `Makefile`: `up`, `down`, `clean`, `migrate`, `revision`, `seed`, `reset-db`,
  `lint`, `format`, `typecheck`, `arch`, `test`, `test-cov`, `e2e`, `check`.
- `.github/workflows/ci.yml`: four jobs — backend (lint, format, mypy strict,
  import contracts, migrations apply, **migration drift check**, tests with
  coverage gate, dependency audit), web (lint, format, typecheck, tests, build),
  security (gitleaks), and a production Docker image build for both.

### Verified on this machine

```
GET /health                  200  {"status":"ok",...}
GET /ready                   200  database reachable, redis reachable
Celery diagnostics.ping      {"status":"ok"}
alembic upgrade head         da8b9fdc2b5a (head)
pg_extension                 btree_gist citext ltree pg_trgm pgcrypto postgis
ruff check / format          pass
mypy --strict app            pass (24 files)
lint-imports                 3 contracts kept, 0 broken
alembic check                no drift
pytest                       62 passed, 80.8% coverage
web: build / lint / test     pass, 22 tests
CORS preflight from :5173    allowed
http://localhost:5173/status 200
```

## Environment variables

`.env.example` is committed and exhaustive (85 lines): application, logging,
security and Argon2 parameters, CORS, database and pool, Redis and Celery,
S3-protocol object storage with upload limits and an allow-list of content
types, SMTP, rate limits, Pakistan company defaults (PKR, `Asia/Karachi`,
fiscal year starting month 7), and observability. `web/.env.example` carries
the five `VITE_*` values.

`POSTGRES_HOST_PORT` and `REDIS_HOST_PORT` were added during this phase because
port 5432 on this machine was held by the Site Ledger prototype's database (since retired); the
containers always reach the database at `db:5432`, so only the published host
port changes.

---

## Next: Phase 1 — Foundation

Companies, projects, phases, sites (PostGIS geofence), departments, cost
centres · users, sessions, refresh rotation, lockout, password reset ·
permissions, roles, scoped grants, `AccessContext` and SQL-level scope
filtering · materials, categories, units and the unit-conversion engine ·
vendors with contacts and bank accounts · audit log write path · attachments on
object storage · notifications · outbox and its Celery drain · document
numbering · CSV import for master data · seed data · full CRUD screens in the
web app.

Exit criterion: creating a vendor in the browser writes a row to PostgreSQL with
an audit entry, an Auditor can read it and cannot write it, and the
authorisation test matrix passes.
