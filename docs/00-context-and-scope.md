# 00 — Context, Scope & Decision Register

**Product:** KRB ERP — a multi-project ERP for a land-development company.
**Site-operations sub-product:** *Site Ledger* (mobile material-delivery capture), continuing the
earlier prototype, now `mobile/` in this repository.

---

## 1. What already existed (audited 2026-09-21)

> **Update 2026-09-26.** The prototype folder `Site Ledger` was merged into this repository: its Expo app is
> now `mobile/`, and its stub backend was retired — the mobile and web apps share the **one** backend in
> `backend/`. The stub is still in the parent folder's git history (`D:\Projects\KRB`, first commit `e51c8b4`:
> `git show e51c8b4:"Site Ledger/backend/main.py"`). The rows
> below describe it as audited, and where each part went.

| Artefact | State | Verdict |
|---|---|---|
| `Site Ledger/mobile` (Expo 51, RN 0.74, expo-location/sqlite/camera) → now `mobile/` | Two screens, UI only. No auth call, no SQLite queue, no sync. | **Reuse the UX; rewrite the code** into the new mobile workspace. |
| `Site Ledger/backend/models.py` (retired; in git history) | `Site` (PostGIS polygon + radius), `Vendor`, `RateVersion` (append-only, effective-dated), `Threshold`, `DeliveryBatch`, `VendorOrder`, `TruckEntry` (client-set UUID, `status`, `flag_reason`, `rate_version_id`). | **Design is sound.** Carried forward and generalised — see [02-data-model](02-data-model.md). |
| `Site Ledger/backend/main.py` (retired; in git history) | `/sync/truck-entries` stub with a correct docstring: upsert by client id, resolve rate server-side, run threshold checks, flag rather than reject. | **Was the spec** for the real sync endpoint, which is now built (`/sync/push`, `/sync/pull`). |
| `Site Ledger/backend/docker-compose.yml` (retired) | `postgis/postgis:16-3.4`. | Kept in the root `docker-compose.yml`. PostGIS is a hard requirement for geofencing. |

Three decisions in that prototype are load-bearing and are preserved verbatim in this design:

1. **Client-generated record IDs.** Offline-created rows never collide, and the ID doubles as the
   idempotency key. (§34 of the brief.)
2. **Never trust a rate cached on-device.** The server resolves the applicable rate from
   `captured_at` against effective-dated rate rows at ingest time.
3. **Flag, never auto-reject.** Anomalies (tonnage, geofence) route to review, they do not delete
   or discard data. (§15, §16.)

---

## 2. Scope boundaries

### In scope, v1 (Phases 1–6, see [10-roadmap](10-roadmap.md))
Auth/RBAC · Company→Project→Site→Department hierarchy · Vendors · Material master · Units &
conversions · Purchase Requests → RFQ → Quotations → PO · Configurable approval engine ·
Mobile delivery capture with offline queue · GPS/geofence · Tonnage & tolerance rules · Vendor
rates with history · Flagged-delivery review · GRN · Inventory ledger · Chart of accounts · GL ·
AP (3-way match) · Budgets & commitments · Employees/Departments/Attendance/Leave · Payroll data
model (no country tax engine) · Notifications · Audit log · Attachments · Reports & dashboards.

### Explicitly deferred (designed for, not built in v1)
- Country-specific **tax & payroll calculation engines** (the data model has the hooks; the rule
  packs are a Phase 7 plug-in).
- **Push notifications and SMS** (the notification channel abstraction ships in v1 with in-app +
  email adapters only).
- **OCR of delivery challans, barcode/QR scanning, digital signature** (§37) — the attachment and
  capture pipeline is built so these are additive.
- **Multi-company operation.** Every table carries `company_id` from day one and all queries are
  company-scoped, but v1 runs one company and does not ship tenant provisioning or row-level
  security policies. Turning it on later is a migration, not a rewrite.
- **Kubernetes** (§40 — explicitly Stage 5).

### Open — requires a business decision before Phase 4/5 (see [11-risks-and-open-decisions](11-risks-and-open-decisions.md))
- Jurisdiction (tax regime, statutory IDs, payroll deductions, fiscal year, currency).
- Whether this system is the **book of record** for accounting, or an ancillary system feeding an
  incumbent package.
- Whether deliveries may be recorded **without a PO** against a standing vendor rate.
- Whether **subcontractor BOQ / running-account billing** and **plot sales & customer
  instalments** are v1 scope.

---

## 3. Decision register

Decisions taken by me under standard engineering practice, recorded so they can be challenged.

| # | Decision | Rationale |
|---|---|---|
| D-01 | **Modular monolith**, one FastAPI process, module boundaries enforced by an import-linter rule. | §2. Cross-module transactional integrity (GRN→inventory→GL) is the dominant requirement; distributed transactions would be self-harm at this scale. |
| D-02 | **PostgreSQL 16 + PostGIS**. Single database, schema-per-concern rejected in favour of one schema with table prefixes. | Geofencing needs real geospatial distance. One schema keeps FKs and migrations simple. |
| D-03 | **UUIDv7 primary keys**, generated application-side. | Globally unique (offline capture), time-ordered so B-tree index locality doesn't degrade like UUIDv4. |
| D-04 | **All money and quantity columns are `NUMERIC(18,4)`**. Floats are banned in the financial and inventory paths. | The prototype used `Float` for `tonnage` and `rate`; that is a defect for money. |
| D-05 | **Async SQLAlchemy 2.0 + asyncpg** throughout; one `AsyncSession` per request, explicit unit of work. | §2, §48. |
| D-06 | **Soft delete on master data only** (`deleted_at`). Transactional records are never deleted — they are cancelled or reversed. | §17, §31. |
| D-07 | **Transactional outbox** for all domain events; Celery workers consume it. | Guarantees "GRN approved" reliably produces notification + audit + ledger posting without 2PC. |
| D-08 | **Append-only ledgers** for inventory and GL. Balances are cached projections, rebuildable from the ledger. | §22: "Never simply update a current stock field." |
| D-09 | **Structured (JSON-Logic style) conditions** in the approval and rules engines, not evaluated Python. | Admin-configurable without arbitrary code execution. |
| D-10 | **React Native (Expo, dev-client)** for mobile, sharing generated TypeScript API types with the web app. | §33, and the existing prototype is already Expo. |
| D-11 | **Hetzner + Cloudflare (Option A)** for Stage 1, with a documented lift to AWS. | §38–§40. See [09-deployment](09-deployment-and-infrastructure.md). |
| D-12 | **English-only UI in v1**, but all user-facing strings go through `i18next` from the first commit. | Retrofitting i18n is expensive; using it is nearly free. |
| D-13 | **No vendor scoring in v1** — only measured facts (on-time %, rejection %, quantity variance). | §11 explicitly forbids subjective scores unless the methodology is configurable. |

---

## 4. Glossary

| Term | Meaning here |
|---|---|
| **PR** | Purchase Request — internal demand, pre-approval. |
| **RFQ** | Request for Quotation sent to invited vendors. |
| **PO** | Purchase Order — the commitment document; creates a budget commitment. |
| **Delivery** | A truck arriving at a site; captured on mobile. Not yet financial. |
| **GRN** | Goods Received Note — the accounting event. Moves inventory and creates a liability accrual. |
| **Commitment** | Budget consumed by an approved PO but not yet spent. |
| **Actual** | Budget consumed by a posted GRN/invoice. |
| **Cost centre** | A charge bucket independent of project (e.g. Head Office Admin). |
| **Phase** | Project work breakdown: Roads, Sewerage, Water, Electricity, Landscaping, Buildings, Earthworks. |
| **Flag** | A rule violation attached to a document that routes it to review without blocking capture. |
