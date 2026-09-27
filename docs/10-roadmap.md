# 10 — Phased Implementation Roadmap

Sequenced so that **something real is usable at the end of every phase**, and so the highest-value,
highest-risk component (mobile delivery capture) is proven early rather than last.

Durations assume one focused implementer working continuously. They are sizing, not commitments.

---

## Phase 0 — Scaffold (≈3 days)

Repository skeleton · Docker Compose (Postgres+PostGIS, Redis, MinIO, Mailpit) · FastAPI app
factory with middleware, error contract, request context · Alembic wired · `core/` (config, db,
security, context, errors, pagination, events) · Vite + React + Tailwind + shadcn shell · CI
pipeline green on an empty test suite · `/health`, `/ready`, `/version`.

**Done when:** `make up && make migrate && make test` works from a clean clone on Windows and Linux.

---

## Phase 1 — Foundation (≈2.5 weeks)

**Done** (2026-09-24). Backend: [14-phase-1-delivery](14-phase-1-delivery.md).
Frontend, browser E2E and the bugs they surfaced:
[15-phase-1-frontend-delivery](15-phase-1-frontend-delivery.md).

Companies, projects, project phases, sites (PostGIS geofence), departments, cost centres ·
users, sessions, refresh rotation, lockout, password reset · permissions, roles, scoped grants,
`AccessContext` + `scope_filter` · materials, categories, units, **unit conversions** · vendors,
contacts, bank accounts · audit log write path · attachments + object storage · notifications
(in-app + email) · outbox + Celery · document numbering · seed data.

Frontend: shell, navigation, auth, DataTable, FilterBar, saved views, and full CRUD for projects,
sites, users/roles, materials, units/conversions, vendors.

**Done when:** creating a vendor in the browser creates a row in PostgreSQL with an audit entry, an
Auditor role can read it and cannot write it, and the authorisation test matrix passes. **Met** —
`e2e/tests/phase1-done-when.spec.ts`.

---

## Phase 2 — Procurement & the approval engine (≈3 weeks)

Approval engine: definitions, condition evaluator, runtime, snapshotting, inbox, SLA reminders and
escalation · purchase requests · RFQs and vendor invitation · quotations and the side-by-side
comparison with mandatory selection reason · purchase orders, amendments, PDF · PO → PR sourcing
loop · budget commitments created on PO approval (budget tables land here, the rest of finance
comes in Phase 4).

Frontend: PR/RFQ/quotation/PO screens, comparison grid, approval inbox, approval trail component,
workflow configuration UI.

**Progress (2026-09-25): parts 1 and 2 done** — approval engine, purchase requests, inbox, trail, workflow
configuration and the §25 tiers ([16](16-phase-2-delivery-part-1.md)); RFQs, quotations with the comparison
grid and mandatory selection reason, purchase orders, amendments and the PO → PR sourcing loop
([17](17-phase-2-delivery-part-2.md)). Purchase-order PDF and budget commitments remain.

**Done when:** the §25 three-tier PR example routes correctly at 50 000 / 500 000 / 5 000 000, and
editing a workflow does not change an in-flight request.

---

## Phase 3 — Material tracking: the core (≈4 weeks)

**This is the phase that justifies the product.**

Backend: business-rules store and resolver · vendor rates (effective-dated, append-only) + history ·
rate resolution · geofence evaluation · tonnage/tolerance/duplicate/clock-skew validation ·
deliveries, items, flags, reviews · review queue and decisions · GRN with 3-way-match fields ·
inventory ledger, balances, issues, transfers, adjustments · mobile sync push/pull, idempotency,
device registry.

Mobile: rebuilt Expo app — login by phone with role routing, site home, new-delivery screen, local
SQLite + outbox, sync engine with backoff, queue screen, history, photo capture, head-office review
queue.

Web: delivery list and detail, flagged-review screen (§18), vendor-rate management with history
(§19), GRN screens, inventory ledger and balances, delivery dashboard (§23).

**Progress (2026-09-27): backend, web and the phone's sync logic done; the app not yet run on a device** — business-rules store and approval
limits, vendor rates, deliveries with every check, the review queue, GRN, the append-only stock ledger with
weighted-average costing, stock issues / transfers / adjustments, and the mobile sync API (push / pull,
idempotent, per-operation outcomes) and the phone app with its offline outbox; see
[18-phase-3-delivery](18-phase-3-delivery.md). Open: running the app on a device, with its Detox /
airplane-mode test and the 72-hour field soak.

**Done when:** 20 deliveries captured in airplane mode land exactly once on the server after
reconnection, priced by server-resolved rates, with geofence and tonnage flags raised, reviewed at
head office, converted to a GRN, and reflected in the stock ledger — verified end-to-end by
Playwright + Detox, not by hand.

---

## Phase 4 — Finance (≈3.5 weeks)

Chart of accounts · accounting periods · journal entries with the full dimension set and the
balanced-entry constraint · posting rules (sub-ledger → GL as configuration) · GRN and inventory
auto-posting · vendor invoices and the 3-way match with tolerance · payment requests, payments,
allocations · AP ageing · budgets, budget lines, commitment consumption, budget vs actual ·
project costing · minimal AR (customers, invoices, receipts).

**Done when:** approving a GRN produces a balanced journal entry, consumes the PO commitment,
increments budget actuals and moves stock — all in one transaction, and the trial balance balances
after the seeded dataset is fully processed.

*Depends on open decisions Q1 (jurisdiction) and Q2 (book of record).*

---

## Phase 5 — HR (≈2.5 weeks)

Employees, documents with expiry alerts, designations, reporting hierarchy · departments ·
attendance with shifts, holidays, late/overtime computation · leave types, balances, requests
through the approval engine · payroll data model: components, effective-dated employee components,
periods, payslips, GL posting. **No country tax engine** — the component model is the hook.

*Depends on Q1 for statutory ID labelling and deduction components.*

---

## Phase 6 — Reporting & dashboards (≈2.5 weeks)

Report infrastructure: definitions, parameterised queries, permission-aware execution, async jobs
for large runs, XLSX and PDF export · the report catalogue in §29 · role dashboards in §30 ·
vendor performance facts · SSE for live review queue and notifications.

---

## Phase 7 — Hardening & launch (≈2 weeks)

Load test against seeded volume (100 k deliveries, 1 M ledger rows) · index review from
`pg_stat_statements` · penetration pass on auth, RBAC, file upload and IDOR · backup **restore
drill** · runbooks (deploy, rollback, restore, incident) · admin and site-staff user guides ·
pilot with one project and one site before full rollout.

---

## Phase 8 — Subcontractor billing (≈3 weeks)

Confirmed in scope, sequenced after v1. BOQ, measurement books, running-account
bills, retention, mobilisation recovery, variations. Outline in
[12-confirmed-decisions](12-confirmed-decisions.md).

---

## Phase 9 — Plot sales & instalments (≈3 weeks)

Confirmed in scope, sequenced after v1. Plot inventory, bookings, allotment,
instalment plans, surcharges, transfers, possession. Outline in
[12-confirmed-decisions](12-confirmed-decisions.md).

---

## Deferred backlog (post-v1, designed for)

Country tax/payroll packs · push notifications and SMS · vendor self-service portal (the token
column already exists) · subcontractor BOQ and running-account bills · plot sales and customer
instalments · challan OCR · barcode/QR · weighbridge integration · GPS attendance · equipment and
fleet management · document management with versioning · BI export to a warehouse · multi-company
activation · multi-currency activation.

---

## Sequencing risks

| Risk | Mitigation |
|---|---|
| Phase 3 is the largest and most novel | It is scheduled third, not last, so field learnings still have time to change the design |
| Finance depends on unanswered business questions | Phases 1–3 are independent of Q1/Q2; if answers are slow, Phase 3 proceeds and Phase 4 slips without blocking anything |
| Mobile field conditions are unknown until real use | A pilot at one site is scheduled inside Phase 3, before the rest of the app is built on assumptions |
| Approval configuration proves too rigid | The engine ships in Phase 2 with real workflows exercised in Phases 3–5, so gaps appear early |
