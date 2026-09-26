# 18 — Phase 3: material tracking (delivery log)

Phase 3 is the product's core and the largest phase, so it is built in **slices,
each additive**: a slice adds tables, services and screens and never changes what
an earlier slice does. Each is committed with its tests before the next starts.
This document is updated per slice; the roadmap ([10-roadmap](10-roadmap.md)) says
what remains.

## Order, and why

| # | Slice | Depends on | Status |
|---|---|---|---|
| 3a | Business-rules store and resolver; **approval limits** (closes the last Phase 2 gap) | — | done |
| 3b | Vendor rates: effective-dated, append-only, approval-gated, resolved by scope | 3a's approval engine wiring | done |
| 3c | Deliveries: ingest, geofence, quantity checks, rate and conversion snapshots, flags | 3a, 3b, unit conversion (Phase 1) | next |
| 3d | Review queue and decisions | 3c | |
| 3e | GRN and the inventory ledger | 3c, 3d | |
| 3f | Mobile sync API (push / pull, idempotency, devices) | 3c | |
| 3g | Web: delivery, review, GRN, inventory screens | 3c–3e | |

The order follows what each slice *reads*: deliveries read rules and rates, so they
come after both; the GRN reads an approved delivery; the sync API is a thin
per-operation wrapper over the same ingest service the web uses, so it can come
late without the web and mobile disagreeing about what a delivery is.

---

## 3a — Business rules and approval limits

**What it is.** One table, `business_rules`, resolves every configurable threshold
(docs/05 §1): a typed value, a *scope* (which project, site, material, truck type,
vendor, role, document type it applies to), an optional condition, a priority and
an effective period. `resolve(rule_type, context, date)` picks the most specific
active rule; ties break on priority, then later start, then newest.

| Piece | Behaviour | Where |
|---|---|---|
| Rule types | Eleven types, each with a validated value shape and the scope keys it may use. A daily cap with neither limit set, or a geofence scoped by truck type, is refused at save time. | `rules/domain/rule_types.py` |
| Resolution | Pure functions, no database: scope match (a site rule never applies to a document that names no site), inclusive dates, condition (the *same* evaluator as approval workflows, moved to `core/conditions.py`; the old import path still works), deterministic tie-break. | `rules/domain/resolution.py` |
| Snapshot | A resolved rule yields `snapshot()`; a flag stores it, so tightening a threshold next month never rewrites why last month's delivery was flagged. | `RuleView.snapshot` |
| Immutability | A rule's **type, scope and start date are fixed** at creation. Value, condition, priority, end date and active flag may change (audited, versioned). To change what a rule applies to, end it and create another. | `rules/services/rule_service.py` |
| Conflicts | Two active rules with the same type, scope, priority and condition over overlapping dates would tie arbitrarily; the second is refused with a message naming the first. | `_assert_no_conflict` |
| Explain | `POST /business-rules/resolve` returns the winner and a reason for every other rule ("scope does not match", "ended 2026-03-31", "inactive"). The screen's *Which rule applies?* panel uses it. | `rules/api/routes.py` |
| Approval limits | `APPROVAL_LIMIT` rules scoped by role (optionally document type, project, site) cap how much a role may approve. A person's limit is the largest of their approving roles' limits, and **a role with no rule is unlimited** — so introducing a limit for one role never quietly restricts another, and with no limit rules nothing changes at all. Enforced in the engine at decision time; the request also carries `decision_blocked_reason` so the screen explains instead of offering *Approve*. An over-limit approver may still reject or return the request. | `rules/services/approval_limit.py`, `approvals/services/engine.py` |
| Screen | Business rules list, create / edit drawer (fields generated from the type catalogue), and *Which rule applies?*. | `web/src/features/rules/` |
| Seeds | Company defaults: geofence 500 m, quantity tolerance 2 % or 0.5, price tolerance 0 %, clock skew 900 s, duplicate window 20 min, late submission 2 days. Only seeded when a type has no company-wide rule. Tonnage limits are deliberately not seeded — the default lives on the truck type. | `seeds/rules.py` |

**Deliberately not done: a Redis cache** for resolution (docs/05 mentions one). The
table is small and read per delivery, so a query is fine today; the cache can be put
behind `resolver.resolve` later without touching a caller, and a stale cache during
tests that roll back would cost more than it saves now.

## 3b — Vendor rates

**The one rule that shapes it (docs/05 §4): a rate is never edited.** A change is a
new period, proposed, approved, and only then in force — at which point the period it
replaces is closed the day before, in the same transaction.

| Piece | Behaviour | Where |
|---|---|---|
| Model | `vendor_rates` (vendor × material × unit × scope × period). Scope is company-wide, a project, or a site (a site's project is filled in). | `rates/models.py` |
| Database guarantees | An **exclusion constraint** forbids two approved periods for the same vendor, material, unit and scope from overlapping (`ex_vendor_rates_no_overlap`, GiST + `btree_gist`). `vendor_rate_history` is append-only by trigger — not even a direct `UPDATE`/`DELETE` rewrites why a price changed. | migration `b4cfe34224c7` |
| Forward-only | A new period must start after the start of any existing one in the same scope. Restating the current rate is refused. One change may be pending per vendor, material and scope. | `rate_service.propose` |
| Approval | Every proposal goes through the approval engine (`vendor_rate`). Seeded default: a **first rate, or a change within ±10 %, approves itself** (a recorded zero-step approval); anything larger goes to finance (escalating to the executive if finance proposed it). Editable at `/approval-workflows`. Context variables: `change_pct`, `new_rate`, `previous_rate`, `is_first_rate`, `is_increase`, `vendor.*`, `material.*`, `project.*`, `site.*`. | `seeds/approvals.py` |
| Resolution | Most specific scope wins (site › project › company), then the later start; the period must contain the date. `unit_id` optional — without it the vendor's own pricing unit is returned and the caller converts. Only approved rates count. | `rates/domain/resolution.py`, `services/resolver.py` |
| History | Old rate, new rate, change %, who, why, when — newest first; needs `rates.view_history` **company-wide** (a site-scoped grant must not reveal what other sites pay). | `GET /vendor-rates/history` |
| API | `POST /vendor-rates` proposes; `PATCH` changes **notes only** (the value has no update schema at all); `POST …/withdraw` recalls a pending proposal; `GET /vendor-rates/resolve`. There is no `PUT`. | `rates/api/routes.py` |
| Screen | Rates list (current / pending / rejected, change %, scope, period), *New rate* drawer, per-row history drawer, withdraw. | `web/src/features/rates/` |

Roles: `FINANCE_MANAGER` and `EXECUTIVE` gain `rates.approve` (the seeded workflow
names them; the engine refuses a role that cannot approve the document).

## Found by testing

- **The approver of a rate change had nowhere to decide it.** The inbox row linked to
  the rates list, which hosts no decision buttons. The browser test — finance opening
  the inbox entry and looking for *Approve* — is what exposed it. Fixed with a generic
  approval page (`/approvals/document/:type/:id`: what is asked, the chain, the
  buttons), used for any document without a page of its own.
- `resolve_place` returned no project for a document naming only a site, so a
  site-scoped rate was stored with no project and resolved wrongly for project-level
  lookups. It now reports the site's project.
- A withdraw returned the pre-withdrawal status because the response was read before
  the change was flushed. Routes now flush before re-reading.

## Testing (slices 3a–3b)

| Suite | Result |
|---|---|
| Backend | 518 passed, coverage 91 % (gate 80 %); ruff, mypy --strict (194 files), import contracts 4/4, `alembic check` clean; both migrations round-trip |
| New backend | 27 unit (resolution, value validation) · 15 unit (rate resolution) · 42 integration (rules API, resolver, approval limits through the engine) · 28 integration (rate proposal, approval, supersession, history append-only, exclusion constraint, scope resolution) |
| Web | 60 unit; typecheck, eslint, prettier, build clean |
| Browser E2E | 17 passed (adds business-rules ×2 and vendor-rates ×1; the smoke test now opens every list and form screen) |

## Decisions to confirm

- **The ±10 % auto-approval threshold** and "first rate approves itself" are defaults
  chosen to keep routine renegotiation from queueing at finance. Change them at
  `/approval-workflows` if KRB wants every price change reviewed.
- **No approval limits are seeded.** Until someone defines them, nothing is limited.
  A limit on a role applies to that role only.
