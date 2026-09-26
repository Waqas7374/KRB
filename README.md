# KRB ERP

A multi-project ERP for a land-development company: head office + multiple projects/sites +
centralised ERP + mobile site operations.

**Status:** Phase 0 (scaffold) complete and running. Phase 1 (foundation) is next —
see [the roadmap](docs/10-roadmap.md).

---

## Documentation

| Doc | Contents |
|---|---|
| [00 — Context & Scope](docs/00-context-and-scope.md) | What exists, scope boundaries, decision register, glossary |
| [01 — System Architecture](docs/01-system-architecture.md) | Modular monolith, layering, module map, cross-cutting mechanisms, folder structure |
| [02 — Data Model & ERD](docs/02-data-model.md) | ~78 tables by module, ER diagrams, indexes, database-enforced invariants |
| [03 — RBAC Model](docs/03-rbac.md) | Permissions, roles, scoped grants, SQL-level scope filtering |
| [04 — Approval Engine](docs/04-approval-engine.md) | Configurable workflows, condition language, runtime, snapshotting |
| [05 — Rules, Rates & Units](docs/05-rules-rates-and-units.md) | Business-rule store, geofencing, tonnage validation, effective-dated rates, unit conversion |
| [06 — Mobile Architecture](docs/06-mobile-architecture.md) | Site Ledger app (`mobile/`), local SQLite outbox, sync protocol, conflict policy |
| [07 — API Specification](docs/07-api-specification.md) | Conventions, full endpoint map, worked GRN example, rate limits |
| [08 — Frontend & Design System](docs/08-frontend-and-design-system.md) | Feature-sliced React, shell, DataTable, tokens, dashboards |
| [09 — Deployment & Infrastructure](docs/09-deployment-and-infrastructure.md) | Hetzner vs AWS vs DigitalOcean, stage evolution, CI/CD, backups, observability |
| [10 — Roadmap](docs/10-roadmap.md) | Phases 0–7 with exit criteria |
| [11 — Risks & Open Decisions](docs/11-risks-and-open-decisions.md) | The four blocking business questions, assumptions taken, risk register |
| [12 — Confirmed Decisions](docs/12-confirmed-decisions.md) | **Authoritative.** Pakistan/PKR, full GL, PO-optional deliveries, Phases 8–9 |
| [13 — Phase 0 Delivery](docs/13-phase-0-delivery.md) | What was built, how it was verified |

## Stack

**Backend** Python 3.12 · FastAPI · PostgreSQL 16 + PostGIS · SQLAlchemy 2 (async) · Alembic ·
Pydantic v2 · Redis · Celery · JWT

**Web** React · TypeScript · Vite · React Router · TanStack Query · Zustand · Tailwind ·
shadcn/ui · Lucide · React Hook Form · Zod · Recharts

**Mobile** React Native (Expo) · TypeScript · expo-sqlite · expo-location · expo-camera

**Infra** Docker · Nginx · Cloudflare R2 · GitHub Actions · Sentry · Prometheus/Grafana

## Getting started

Requires Docker Desktop. Nothing else — no local Python or Node needed.

```bash
cp .env.example .env
# Generate a real secret:
python -c "import secrets; print(secrets.token_urlsafe(64))"   # paste into SECRET_KEY

make up        # postgres+postgis, redis, minio, mailpit, api, worker, web
make migrate   # alembic upgrade head
make check     # lint + typecheck + architecture contracts + tests
```

| Service | URL |
|---|---|
| API docs | http://localhost:8000/docs |
| Readiness report | http://localhost:8000/ready |
| Web app | http://localhost:5173 |
| MinIO console | http://localhost:9001 (`minioadmin` / `minioadmin`) |
| Mailpit inbox | http://localhost:8025 |

If port 5432 is already taken on your machine, set `POSTGRES_HOST_PORT` in `.env`
(the containers always reach the database at `db:5432` regardless).

Mobile (Phase 3): `cd mobile && npm install && npx expo start`. The Android
emulator reaches the host API at `http://10.0.2.2:8000`, not `localhost`.

## Principles

Correct business workflows > data integrity > auditability > security > usability > scalability >
visual polish.

Concretely, in this codebase that means: append-only ledgers for stock and money; effective-dated
rates and conversion factors, snapshotted onto every transaction; approvals frozen at submission
time; nothing important ever physically deleted; and every state change attributable to a person,
a device and a timestamp.
