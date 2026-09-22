# 11 — Risks, Assumptions & Open Business Decisions

> **All four questions in Part A were answered on 2026-09-21.** The answers are recorded in
> [12 — Confirmed Decisions](12-confirmed-decisions.md), which is the authoritative record.
> Part A is kept below as the reasoning behind each question.

---

## Part A — Questions that genuinely need a business decision *(RESOLVED)*

| | Question | Answer |
|---|---|---|
| Q1 | Jurisdiction | **Pakistan** — PKR, fiscal year Jul–Jun, CNIC/NTN/STRN, FBR sales tax, EOBI |
| Q2 | Book of record | **Full double-entry GL in this system** |
| Q3 | PO required for deliveries | **PO-optional**, `NO_PO` flag routes to head-office review |
| Q4 | Subcontractor billing / plot sales | **Both in scope, sequenced after the v1 core** (Phases 8 and 9) |

Each one changes the schema or the workflow, and no engineering convention settles it. Each had a
**default** recorded so nothing would block indefinitely.

---

### Q1. Jurisdiction, currency and fiscal year

**Why it matters.** The supplied material is internally inconsistent: the truck plates are Indian
(`RJ14 GB 4021`, `MP09 HT 1187`) and vendors are Indian (UltraTech, Yamuna Sand, Shree Stone), but
the HR spec asks for **CNIC**, which is Pakistani. These are different countries with different tax
regimes, statutory IDs, payroll deductions, fiscal years and currency minor units.

**What it determines.**

| | Pakistan | India |
|---|---|---|
| Currency | PKR, 2 dp | INR, 2 dp |
| Fiscal year | 1 Jul – 30 Jun | 1 Apr – 31 Mar |
| Statutory ID | CNIC (13 digits) | Aadhaar / PAN |
| Sales tax | FBR sales tax, provincial services tax | GST with CGST/SGST/IGST split — **three tax columns, not one** |
| Withholding | Income-tax withholding on supplies/services | TDS with section codes |
| Payroll | EOBI, provincial social security | PF, ESI, professional tax |
| Vendor tax fields | NTN / STRN | GSTIN, PAN |
| Number formatting | lakh/crore grouping | lakh/crore grouping |

GST in particular is not a cosmetic difference — it changes the tax tables on every invoice line
and the entire input-credit reconciliation. It is far cheaper to know now than to retrofit.

**Default if unanswered:** Pakistan, PKR, fiscal year July–June, single sales-tax rate per line,
statutory ID labelled "CNIC". Tax structures are built behind an interface so a GST pack can be
added, but the v1 tables will assume a single tax component per line.

---

### Q2. Is this system the book of record for accounting?

**Why it matters.** There is a large difference between:

- **(a) Full book of record** — this system holds the chart of accounts, posts every transaction to
  a real double-entry GL, closes periods, and produces the trial balance and statements. Phase 4 is
  ~3.5 weeks and the accounting team must be trained and must migrate opening balances.
- **(b) Operational costing only** — the ERP owns procurement, delivery, inventory and project
  cost, produces a periodic summarised export, and an existing package (Tally / QuickBooks / SAP /
  a manual system) stays the statutory book. Phase 4 shrinks to ~1.5 weeks: budgets, commitments,
  project costing and an export, with no period close and no statutory reporting.

Building (a) when the finance team will keep using their existing package produces an expensive,
half-maintained second set of books — the single most common way an ERP loses credibility.

**Also needed:** if (a), do you have an existing chart of accounts to import, and what is the
cut-over date for opening balances?

**Default if unanswered:** build (a) — full double-entry GL — because the data model supports it
either way and (b) is a strict subset. But this is the decision I would most like to hear from you
on, because it is ~2 weeks of work and a lot of change management.

---

### Q3. May a delivery be recorded without a purchase order?

**Why it matters.** The brief specifies a clean `PO → GRN → Invoice → Payment` chain (§6), but the
mobile mockup captures material, truck, tonnage and *vendor rate* with no PO anywhere in the flow.
Both are realistic, and they imply different systems:

- **PO-mandatory** — every truck must be attributable to an approved PO line. Strong control,
  perfect 3-way matching, budget commitments always accurate. But site staff cannot record a truck
  that arrives against a phone call, and in practice they will either record it against the wrong
  PO or not at all — which is worse than no control.
- **PO-optional** — deliveries may be captured against a standing vendor rate with a `NO_PO` flag
  routing them to head-office review, and may be attached to a PO later or rolled into a monthly
  reconciliation. Capture is complete; control is exercised at review rather than at the gate.

This determines whether `deliveries.purchase_order_id` is nullable, whether the mobile form can
skip the PO step, how budget commitments behave, and how AP invoices match.

**Default if unanswered:** PO-optional with a `NO_PO` flag and mandatory head-office review, and a
per-project setting to enforce PO-mandatory where you want the tighter control. This is designed to
be a configuration, not a fork.

---

### Q4. Which of these two large domains are in v1?

Both are standard in land development and both are absent from the brief's module list, so I need
to know whether they are out of scope or simply unstated.

**(a) Subcontractor / civil-contractor billing.** Contractors appear in §4 and §11 but there is no
module for how they are *paid*: BOQ items, measurement books, running-account bills, retention
money, mobilisation advance and its recovery, price escalation, work-order variations, and tax
deduction at source. For a company building roads and sewerage, contractor payments are usually a
**larger** cost line than material purchases. Roughly +3 weeks.

**(b) Plot sales and customer instalments.** §6 asks for accounts receivable, but for a housing
scheme AR normally means plot inventory, bookings, allotment, instalment plans, surcharges on late
payment, transfers and possession — not generic sales invoices. Roughly +3 weeks. If plots are sold
through a separate system or not at all yet, generic AR is correct and sufficient.

**Default if unanswered:** both deferred to post-v1. AR ships in generic form (customers, invoices,
receipts, ageing); contractors exist as a vendor type that can receive POs and invoices, without
BOQ or running bills.

---

## Part B — Decisions I have taken, stated so you can overrule them

These are answerable by convention. Listed because they are business-visible.

| # | Decision | If you disagree |
|---|---|---|
| A-01 | **Billing basis is configurable per material** (weight, volume or truck-load), with the unit-conversion engine handling tonne ↔ cft ↔ brass ↔ bag. Default basis: weight for aggregates, bags for cement, weight for steel. | Tell me the contractual basis per material category |
| A-02 | **Unflagged deliveries auto-approve** at the site; only flagged ones reach head-office review. Otherwise the review queue fills with routine traffic and reviewers stop reading it. | Say if every delivery must be reviewed |
| A-03 | **Site staff cannot edit a submitted delivery.** Head office must *request correction*, which returns it to the device with a note. All versions retained. | — |
| A-04 | **Inventory valuation is weighted average.** FIFO is designed for but not built. | Say if FIFO is required by your auditor |
| A-05 | **Material is issued to a project phase or cost centre**, not against a BOQ line. BOQ-level consumption tracking is part of Q4(a). | — |
| A-06 | **Attendance is per named employee.** Daily-wage gangs recorded as individuals, not as a headcount, because payroll and site-cost allocation both need names. | Say if you use gang/labour-contractor headcounts instead |
| A-07 | **English-only UI in v1**, with i18n plumbing from the first commit so Urdu/Hindi is a translation file, not a rewrite. | — |
| A-08 | **Single company, single currency in v1**; every table carries `company_id` and every money column has a currency and FX pair, so both activate later without a data migration. | — |
| A-09 | **No vendor scoring.** Measured facts only: on-time %, rejection %, quantity variance, price history. | Give me a scoring formula and I will implement it as configuration |
| A-10 | **Sizing assumption:** 3–5 projects, 8–12 sites, ~150 active users, ~200 deliveries/day, ~2 000 employees. Infrastructure and index choices follow from this. | Correct the numbers if they are an order of magnitude off |
| A-11 | **Site timezone** drives all date boundaries ("today's deliveries"), not server or user timezone. | — |
| A-12 | **Master data migration** is assumed to be CSV import of vendors, materials, employees and (if Q2=a) the chart of accounts, built in Phase 1. | Tell me if data must come from a specific existing system |

---

## Part C — Technical and delivery risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-01 | **Site staff do not adopt the mobile app** — the entire material-tracking value collapses if trucks are still logged on paper. | Medium | Critical | 20–30 s capture target enforced by stopwatch testing; works fully offline; pilot at one site inside Phase 3; measure adoption as a first-class metric; the review queue shows head office *what is not being captured*, not just what is. |
| R-02 | **GPS is unreliable** on open sites, under sheds, on cheap devices — producing geofence flags on legitimate deliveries until reviewers ignore all flags. | High | High | Accuracy-aware evaluation (a fix with ±120 m accuracy cannot prove a 640 m violation); severity bands; per-site radius tuning; a weekly false-positive report; flags never block capture. |
| R-03 | **Flag fatigue** — thresholds set too tight, everything is flagged, review becomes rubber-stamping. | High | High | Rule simulator before activation; per-rule flag-rate dashboard; an explicit target that under 10% of deliveries are flagged; flags carry severity so WARNING and CRITICAL are triaged differently. |
| R-04 | **Rate data is wrong or missing** at capture time, so deliveries price to nothing. | Medium | High | `RATE_MISSING` flag with capture-now-price-later; rate coverage report per vendor/material/site; rates required before a site goes live. |
| R-05 | **Scope growth.** The brief is roughly three products (ERP, field app, accounting system). | High | High | Hard phase gates; Q4 kept explicitly out of v1; the deferred backlog is written down rather than absorbed silently. |
| R-06 | **Offline sync data loss** — the failure that destroys trust permanently. | Low | Critical | Client-generated UUIDs; durable SQLite outbox; idempotent server; per-op results; ops never auto-discarded (DEAD after 72 h and surfaced, not deleted); queue survives sign-out; 72-hour airplane-mode soak test before rollout. |
| R-07 | **Approval bottlenecks** — a single named approver on leave stalls procurement. | Medium | Medium | Role-based rather than person-based steps wherever possible; SLA escalation; delegation; an ageing report on pending approvals. |
| R-08 | **Inventory balance drifts** from the ledger under concurrency. | Low | High | Row-level `FOR UPDATE` on the balance within the ledger transaction; nightly recompute-and-compare with an alarm; balances always rebuildable from the append-only ledger. |
| R-09 | **Latency to EU-hosted infrastructure** from South Asian sites. | Medium | Medium | Offline-first mobile; CDN-fronted web; Option C (Bangalore) available without an application change if measured latency is unacceptable. |
| R-10 | **Single-person bus factor** on a system this size. | High | High | Documentation-first (this set); conventional stack with no exotic dependencies; high test coverage on the money and stock paths; no clever code. |
| R-11 | **Period-close correctness** — the first month-end will expose every posting-rule error at once. | Medium | High | Posting rules are data and testable in isolation; a full seeded dataset is posted and a trial balance asserted in CI; a parallel run against the existing books for the first two months. |
| R-12 | **Photo storage growth.** 200 deliveries/day × 200 KB ≈ 15 GB/year, more with multiple photos. | Low | Low | R2's zero egress; lifecycle rules moving photos older than 2 years to cold storage; photos are optional and compressed on device. |
| R-13 | **Windows development host, Linux production.** | Medium | Low | Everything runs in Docker; CI runs on Linux; line endings pinned via `.gitattributes`. |
| R-14 | **Permission model gaps** discovered after rollout, when correcting them means revoking access people already have. | Medium | Medium | The authorisation test matrix is written alongside each endpoint; the Auditor role is validated as genuinely read-only; scope filtering is in SQL so it cannot be bypassed by a missed check. |

---

## Part D — What I will build first regardless of the answers

Phases 0–3 do not depend on Q1–Q4 in any load-bearing way:

- Q1 affects tax and payroll (Phases 4–5), plus a label and a fiscal-year setting.
- Q2 affects Phase 4 only.
- Q3 changes one nullable column and one mobile form step, both already designed to flex.
- Q4 is additive scope, entirely post-Phase-3.

So the foundation, procurement and the whole material-delivery core can proceed immediately, and
your answers are needed before Phase 4 begins — roughly nine weeks of work from now.
