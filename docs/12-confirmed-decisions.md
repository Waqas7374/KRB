# 12 — Confirmed Business Decisions

Answered 2026-09-21. This document is authoritative; where it conflicts with docs 00–11, this wins.

---

## Q1 — Jurisdiction: **Pakistan**

| Setting | Value |
|---|---|
| Base currency | **PKR**, 2 decimal places, symbol `Rs`, lakh/crore digit grouping |
| Fiscal year | **1 July – 30 June**. `companies.fiscal_year_start_month = 7` |
| Fiscal year label | `FY2026-27` (spanning) |
| Personal statutory ID | **CNIC**, 13 digits, format `#####-#######-#`, checksum-validated, unique per employee |
| Company tax IDs | **NTN** (National Tax Number) and **STRN** (Sales Tax Registration Number) on vendors, customers and the company |
| Sales tax | **Single tax component per document line** (`tax_codes.rate_pct`), FBR sales tax on goods; provincial services tax handled as additional tax codes |
| Withholding | Income-tax withholding on supplier payments, by section. `vendor_invoices.withholding_amount` and `payments.withholding_amount` are live from Phase 4; withholding rates are `tax_codes` rows with `tax_type = 'WITHHOLDING'` and a `section_code` |
| Payroll statutory | **EOBI** (employee + employer contribution) and provincial social security (PESSI/SESSI) as seeded `salary_components`. No income-tax slab engine in v1 — slabs are a Phase 7 rule pack |
| Timezone | `Asia/Karachi` as the company default; each site may override |
| Phone format | `+92` / local `03xx-xxxxxxx`, normalised to E.164 on store |
| Locale | `en-PK` number and date formatting; UI text English (assumption A-07 unchanged) |

**Schema consequences applied:**
- `employees.national_id` is labelled *CNIC* throughout the UI; validation is a Pakistan CNIC
  checksum rule in `core/validators.py`, swappable by company setting.
- `vendors` / `customers` / `companies` carry `ntn` and `strn` columns rather than a single
  `tax_id`.
- One `tax_amount` per line. The GST three-way split is **not** built; if the company ever operates
  in India, that is a new tax-component table, not a rework of existing columns.
- `tax_codes` gains `section_code` and `applies_to` (GOODS/SERVICES/PAYMENT).

---

## Q2 — Book of record: **Full double-entry GL in this system**

Phase 4 is built in full as specified in [02-data-model §8](02-data-model.md) and
[10-roadmap](10-roadmap.md):

- Chart of accounts with ASSET / LIABILITY / EQUITY / REVENUE / EXPENSE / COGS_DEV_COST,
  seeded with a land-development-appropriate structure.
- Journal entries with the full dimension set and a deferred balanced-entry constraint.
- `posting_rules` mapping every sub-ledger event to accounts as configuration.
- Accounting periods with open/closed/locked status; posting into a non-open period is refused.
- Trial balance, general ledger, AP ageing, AR ageing, budget vs actual.
- Period close and year-end roll-forward.

**Additional work this pulls in, added to Phase 4:**

| Item | Note |
|---|---|
| Chart-of-accounts import | CSV import in Phase 1, so an existing CoA can be loaded rather than retyped |
| Opening-balance entry | A dedicated `OPENING_BALANCE` journal source type, posted into a designated period, reconciled to a signed trial balance |
| Cut-over plan | **Still needed from you:** the go-live date and whether you will parallel-run against the existing books. R-11 assumes a two-month parallel run |
| Finance training | Not a code deliverable, but a launch dependency — noted in Phase 7 |

---

## Q3 — Purchase orders: **optional, flagged for review**

- `deliveries.purchase_order_id` and `delivery_items.po_item_id` are **nullable**.
- A delivery captured without a PO raises flag `NO_PO` (severity WARNING) and enters
  `UNDER_REVIEW`. It is never rejected at capture.
- The mobile New Delivery screen makes the PO step **skippable**: it auto-selects when exactly one
  open PO matches (vendor + material + site), offers a picker when several do, and shows
  *"No purchase order — will be reviewed at head office"* when skipped.
- Pricing falls back to the standing `vendor_rates` row resolved at `captured_at`, exactly as for
  PO-backed deliveries.
- Head-office review can **attach a PO retrospectively**; doing so re-runs PO-balance validation
  and records the attachment in `delivery_reviews`.
- A PO-less delivery may still become a GRN. Its GRN has `purchase_order_id = NULL`, matching
  degrades from 3-way to **2-way** (GRN ↔ invoice), and `vendor_invoice_matches.match_type`
  records which was used.
- Budget impact: a PO-less delivery creates **no commitment**; on GRN posting it books the actual
  directly against the budget line. Budget-vs-actual therefore stays correct; only the commitment
  column is unaffected.
- **Per-project override retained:** `projects.settings.require_po_for_delivery` (default `false`)
  turns the gate on for a project where tighter control is wanted, making the flag a hard block at
  capture instead. This was option 3 in the question and costs nothing to keep.

New reports this implies: *PO-less deliveries by vendor and project* (a control report finance will
want monthly), and *deliveries awaiting PO attachment*.

---

## Q4 — Subcontractor billing and plot sales: **both in scope, after the v1 core**

The selection was ambiguous — "defer both" and both modules were chosen. Interpreted as:
**committed and designed, but sequenced after v1 ships**, so the core ERP is not delayed by six
weeks of additional domain work.

Revised phase plan:

| Phase | Content | Change |
|---|---|---|
| 0–7 | As in [10-roadmap](10-roadmap.md) | unchanged — v1 ships here |
| **8** | **Subcontractor / civil-contractor billing** (~3 weeks) | new |
| **9** | **Plot sales & customer instalments** (~3 weeks) | new |

### Phase 8 — Subcontractor billing (outline)

Tables: `contracts`, `contract_boq_items`, `work_orders`, `measurement_books`, `mb_entries`,
`running_bills`, `running_bill_items`, `contract_deductions` (retention, mobilisation recovery,
withholding, penalties), `contract_variations`, `retention_releases`.

Behaviour: BOQ with rates and quantities → measurements recorded against BOQ items (optionally from
site, with GPS, reusing the delivery-capture pattern) → running-account bill computed as
*cumulative measured − previously billed* → deductions applied in configured order → approval
through the existing engine → GL posting and payment through existing AP. Retention accrues to a
liability account and releases on a schedule or on defect-liability expiry.

Reuses without modification: approval engine, business rules, attachments, audit, budget
commitments (a contract commits budget exactly as a PO does), payments and AP.

### Phase 9 — Plot sales & instalments (outline)

Tables: `plots` (with PostGIS boundary, block, street, size, category, corner/park-facing premia),
`plot_price_lists`, `customers` (extended), `bookings`, `allotments`, `instalment_plans`,
`instalment_schedule_lines`, `customer_receipts`, `surcharges`, `plot_transfers`,
`possession_records`, `cancellations_and_refunds`.

Behaviour: plot inventory with status lifecycle (AVAILABLE → BOOKED → ALLOTTED → TRANSFERRED →
POSSESSED, plus CANCELLED/BLOCKED) → booking with down payment → generated instalment schedule →
receipts allocated oldest-first → late-payment surcharge accrual → transfer with fee → possession.
AR in Phase 4 stays generic and is **extended**, not replaced.

Reuses without modification: AR receipts and allocation, approval engine (transfers, cancellations,
discounts), audit, documents, notifications.

**Both are additive.** Nothing in Phases 0–7 needs to change to accommodate them, which is why they
can be sequenced later without rework.

---

## Still needed from you (not blocking)

| # | Question | Needed by |
|---|---|---|
| N-1 | Go-live date and whether finance will parallel-run against the existing books | Phase 4 |
| N-2 | Existing chart of accounts (a CSV or an export) to import, plus the opening-balance cut-off date | Phase 4 |
| N-3 | Real master data: vendor list, material list with units, employee list, site coordinates | Phase 1 seed/import |
| N-4 | Actual truck types in use and their real maximum tonnages | Phase 3 rule seeding |
| N-5 | Withholding-tax sections and rates you currently apply to suppliers | Phase 4 |
| N-6 | Confirmation of the contractual billing basis per material (weight vs volume vs truck-load) | Phase 3 |
