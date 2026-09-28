# 19 — Phase 4: Finance

Built in the same additive, slice-by-slice discipline as Phase 3: each slice adds tables, services
and screens and never changes what an earlier slice does, and is committed with its tests before
the next starts. This document is updated per slice; the roadmap ([10-roadmap](10-roadmap.md)) says
what remains.

## Order, and why

| # | Slice | Depends on | Status |
|---|---|---|---|
| 4a | Chart of accounts, accounting periods, manual journal entries, trial balance, general ledger | Phase 2's approval engine | done |
| 4b | `posting_rules` configuration; GRN and inventory (issues, adjustments) auto-posting to the GL; budgets, budget lines, commitment consumption tied to PO approval and GRN posting | 4a | done |
| 4c | Vendor invoices, the 3-way match with tolerance, AP ageing | 4a, 4b | not built |
| 4d | Payment requests, payments, allocations | 4c | not built |
| 4e | Minimal AR: customers, customer invoices, receipts | 4a | not built |

4a comes first because every later slice posts through it: a GRN's auto-entry, a payment's
allocation and a customer receipt all end up as rows in the same `journal_entries` /
`journal_entry_lines` tables this slice creates, checked by the same deferred trigger.

---

## 4a — Chart of accounts, periods, manual journal entries

**The one rule the database itself enforces (docs/02 §8, docs/12 Q2): a journal entry's debits and
credits are equal, or it does not exist.** Not a application-level check that a bug could skip —
a **deferred constraint trigger** on `journal_entry_lines`, checked once at transaction commit
rather than after every line insert, so a multi-line entry can be built row by row and only has to
balance at the end.

| Piece | Behaviour | Where |
|---|---|---|
| Chart of accounts | `AccountType` (ASSET / LIABILITY / EQUITY / REVENUE / EXPENSE / COGS_DEV_COST, land-development cost as WIP rather than an immediate expense) with a derived normal balance. Hierarchical via an **id-based materialised path** (`path`), so renumbering a code never rewrites it. Attaching a child under a postable account flips it to a group automatically; an account with existing postings refuses to become one. | `finance/domain/enums.py`, `finance/services/accounts.py` |
| Accounting periods | Twelve generated at once from the company's `fiscal_year_start_month` (`python-dateutil`'s `relativedelta`), idempotent to call again. **OPEN → CLOSED → LOCKED, one-way past LOCKED** — nothing reopens a locked period. Posting checks the period twice: once when a draft is built, again the instant before it posts (a manual entry can sit in approval for days). | `finance/services/periods.py` |
| The ledger | `ledger.py` is the **one door** every posting goes through, manual or system. `build_draft` / `replace_draft_lines` for a person's entry; `post_system_entry` (build and post in the same transaction) for a subledger event that is already its own approved document — the shape GRN and invoice posting will use from 4b. `reverse` swaps every line's debit and credit and posts a new entry, marking the original `REVERSED`; a partial unique index allows at most one reversal per entry. | `finance/services/ledger.py` |
| Manual journal entries | The one kind of posting that is not the automatic result of an already-approved document, so it is the one kind that goes through the approval engine itself — approval and posting are the same moment, exactly like a stock adjustment. Seeded workflow: **below Rs 100,000, finance alone; at or above it, finance then the executive.** When finance itself raises the entry, finance's own step has nobody eligible (an initiator never approves their own step) and escalates to the executive on its SLA. | `finance/services/journal_entries.py`, `seeds/approvals.py` |
| Reports | Trial balance: every account with a non-zero posted movement, net onto whichever side the balance actually falls (a payable that has swung into debit still shows as a small debit — the sign of the net movement decides the column, never the account's own normal side). General ledger: one account's posted movements in date order, opening balance carried from everything strictly before the range, running balance through it. Both read **posted entries only** — a draft or a pending entry has not happened yet, and a reversed entry's lines are cancelled out by its reversal's lines rather than excluded, so the ledger stays a complete record of everything ever posted. | `finance/services/reports.py` |
| Dimensions | Every line carries the full dimension set (project, phase, site, department, cost centre, vendor, customer, material — customer and employee columns carry no foreign key yet, validated only for shape until AR and HR arrive) but nothing requires all of them; an account's own `requires_project` / `requires_cost_center` decides what a line posting to it must carry. | `finance/models.py` |
| Screens | Chart of accounts (tree, create/edit drawer), accounting periods (per-year table, generate / close / reopen / lock), journal entries (list, new/edit form with a live balance indicator, detail with the approval chain and submit/withdraw/delete/reverse), trial balance, general ledger. | `web/src/features/finance/` |
| Seeds | A land-development chart of accounts (cash and bank, receivables, advances, materials inventory; payables, GRNI, withholding and sales tax payable; share capital and retained earnings; plot sales and other income; administrative and selling expense; materials consumed, contractor and labour cost, site overheads as `COGS_DEV_COST`) and the current plus next fiscal year's periods. | `seeds/finance.py` |

### Found by testing

- **A submitted entry silently reverted to draft on approval, even though the decision returned
  200.** Root cause: the approval engine's document-hash consistency check (docs/04 §2 — a document
  edited while pending is recalled rather than approved) compares the hash computed at submit time
  against the hash recomputed at decide time. The entry's locking fetch (`get_for_update`, which
  loads with `noload("*")` so a `FOR UPDATE` query never turns into a forbidden outer join) returned
  an object whose `lines` relationship had not been loaded in that request; hashing it saw an empty
  line list, submitted a hash for an "empty" entry, and the real hash recomputed at decide time
  never matched — an auto-recall on every single approval, reported as a 200 with the entry quietly
  back in `DRAFT`. Fixed by explicitly loading `lines` right after the locking fetch, in both
  `submit` and `update` (the same gap made `update` drop every existing line without deleting the
  old rows first, failing on the `line_no` unique constraint on a second edit).
- **The trial balance had every credit-normal account's balance on the wrong side.** The first
  attempt branched on the account's own normal balance to decide which column to place a balance
  in; the branch for a credit-normal account had debit and credit swapped. The correct rule needs no
  branch at all — the sign of the net movement (debit minus credit) alone decides the column, which
  is also what "a payable that has swung into debit still shows as a small debit" already implied.
- **A manual entry's approval workflow reused an existing rule name** ("Under 100,000", already the
  first tier of the purchase-request workflow), which made the workflow-administration screen's text
  lookup ambiguous. Renamed to "Below 100,000" / "100,000 or more" — a display-only fix, no routing
  change.

## Testing (state after slice 4a)

| Suite | Result |
|---|---|
| Backend | Full suite passed, coverage 93 % (gate 80 %); ruff, mypy (strict via config), import contracts 4/4, `alembic check` clean; the balance trigger's migration round-trips |
| New this slice | 29 tests: chart of accounts (tree shape, group auto-flip, duplicate code, `requires_project`), periods (generation, idempotent regeneration, OPEN→CLOSED→LOCKED, posting into a closed period refused), journal entries (draft not in the ledger, unbalanced refused, one-sided line refused by schema, posting to a group account refused, small entry signed by finance alone, large entry needs finance then the executive, rejected entry becomes a draft again and is resubmittable, withdraw before any decision, draft deletable but a posted entry is not, reversal swaps every line and marks the original, only `finance.gl.reverse` may reverse), reports (trial balance only shows posted entries and it balances, general ledger's opening/running/closing balance, permission checks) |
| Web | Typecheck, eslint (no warnings), prettier, build clean; existing unit suite unaffected |
| Browser E2E | `finance.spec.ts`: draft → submit → approve → post through the real browser and API, asserted against PostgreSQL at each step, plus the trial balance carrying the movement. The preparer is `admin` rather than the seeded accounts officer, deliberately — `admin` holds no Finance Manager role so it is never listed as an eligible approver on its own entry, and unlike the accounts officer its password is never rotated by another spec's forgot-password test |

---

## 4b — Posting rules, GRN and inventory auto-posting, budgets and commitments

**The done-when this slice exists for (docs/10): approving a purchase order commits a budget line;
posting the GRN against it releases the commitment, books the actual, and posts a balanced journal
entry — all in the same transaction as the stock movement, through the same `posting_rules` lookup
for both the GL accounts and the budget line.** Every other way stock moves without a receipt behind
it — an issue, a count correction — posts through the same door.

| Piece | Behaviour | Where |
|---|---|---|
| Posting rules | `source_type` + `event` (`GRN`/`RECEIPT`, `INVENTORY`/`ISSUE`, `INVENTORY`/`ADJUSTMENT` today; 4c-4d add invoice and payment events) resolve, by the same condition language as approval workflows and business rules, to a debit and a credit account. A GRN receipt's condition sees `is_stockable`, `is_po_backed` and `material_category_id`; an adjustment's sees `direction`. Highest active priority wins; a condition is validated against every event's known variables when the rule is saved, the same discipline a workflow's condition already gets. | `finance/services/posting_rules.py` |
| Seeded rules | **Receipt** (four): stockable/non-stockable × with/without a purchase order behind it. A PO-backed receipt accrues into **2110 Goods Received Not Invoiced** (cleared once 4c matches the vendor's invoice); a counter purchase — already its own bill — credits **2100 Accounts Payable** directly. Stockable debits **1400 Inventory — Materials**; non-stockable debits **6200 Contractor and Labour Cost** straight away. **Issue** (one): material leaving a store debits **6100 Materials Consumed**, credits 1400 — it was development cost the moment it left. **Adjustment** (two, by direction): a correction is charged or credited to **6300 Site Overheads**, never to inventory's own value — inventory only ever holds what a receipt or an issue actually moved; the difference between the books and the shelf is the site's, not the material's. | `seeds/finance.py` |
| Budgets | `Budget` (one per project and fiscal year) → `BudgetLine` (phase and/or cost centre, an account, a budgeted amount) → `BudgetCommitment` (append-only; written by an approved PO, released by a posted GRN). Approving a budget is a **single permission check** (`finance.budget.approve`), not a routed workflow — docs/07 gives it its own `POST /finance/budgets/{id}/approve` rather than the shared `/approvals` endpoint, unlike every approval-gated document in 4a. `REVISED` is not a status anyone picks: it is entered the instant a line's `revised_amount` is first set on an approved budget, and a line always reports the original `budgeted_amount` alongside it. `remaining_amount` and `variance_pct` are computed when a budget is read, never stored, so they cannot drift from the columns that back them. `GET /finance/budgets/{id}/vs-actual` gives the same lines as a report shape (totals, no edit-permission flags), docs/07's own endpoint for it. | `finance/models.py`, `finance/services/budgets.py` |
| Committing (PO approval) | The approved order's items resolve through the *same* receipt posting rule (its debit account is where the spend will eventually land) and group by account; each group that matches a line of an approved budget for the PO's project, fiscal year, phase and cost centre gets a new `OPEN` commitment, and the line's `committed_amount` rises by it. A PO with nothing to match — no budget for that project yet, or no line for that phase/account — approves exactly as before; a budget is something to measure spending against, never a gate on it. | `procurement/services/purchase_orders.py` (`on_approved`), `finance/services/budgets.py::commit_purchase_order` |
| Releasing (GRN posting) | Every accepted, priced line resolves its account (per line, since one GRN can mix items against a purchase order with an ad-hoc extra one) and groups the same way. Each group books the actual against the matching budget line regardless of whether anything was committed; when it was, the matching `OPEN`/`PARTIALLY_RELEASED` commitment is released by the same amount, capped at what remains of it, and `committed_amount` falls by exactly that — *committed → actual → remaining* stays true at every step, not just at the end. The same grouped totals become the journal entry's debit and credit lines, posted through `ledger.post_system_entry` in the same transaction as the stock movement and the PO's received-quantity update; the entry's id is kept on the GRN (`journal_entry_id`, a column carried since Phase 3 for exactly this). | `grn/services/grn_service.py::_post_gl_and_budget` |
| Cancelling a posted GRN | Reverses the ledger transactions and the PO's received quantities as it already did, **and now also** reverses the journal entry (`journal_entries.reverse_system`, ungated on `finance.gl.reverse` — the caller's own `grn.cancel` permission is what authorised this) and reopens whatever the GRN released (`budgets.reverse_release`, a separate pass from `release_receipt` run backwards, not the same function with a sign flipped). Nothing is left half-undone: stock, the GL and the budget move together or not at all, exactly as posting does. | `grn/services/grn_service.py::cancel` |
| Stock issues and adjustments | An issue posts its journal entry (Dr Materials Consumed, Cr Inventory) the instant it posts, at the same total the ledger lines were just valued at; cancelling a posted issue reverses it the same way a GRN's cancel does. An adjustment posts its entry (charged to Site Overheads, by direction — an increase and a decrease in the same document become two balanced pairs of lines in one entry) inside the approval handler's own transaction, alongside the ledger movement — there is no "adjustment posted but not yet in the GL" moment, the same guarantee the module's own docstring already made for stock. A posted adjustment has no cancel path (a correction is corrected with another adjustment, docs/02), so it needed no reversal wiring. | `stock/services/issues.py`, `stock/services/adjustments.py` |
| Screens | Posting rules (list, create/edit drawer with a JSON condition field). Budgets (list, new/edit form with a per-line phase/cost-centre/account picker, detail with a committed/actual/remaining/variance table, approve, revise, close). A GRN, issue or adjustment's detail page links to the journal entry it posted. | `web/src/features/finance/PostingRulesPage.tsx`, `BudgetPages.tsx` |
| `GET /finance/commitments` | Listed by budget line, source type or source id — the audit trail behind a line's `committed_amount` (docs/07 §2, built alongside the budgets endpoints it was missing from on first pass). | `finance/api/routes.py` |

**Deliberately simplified.** A purchase order carries one phase and one cost centre at its header
(docs/02 §5), so a commitment is per (PO, resolved account) rather than per line — a PO that mixes
materials resolving to different accounts still commits correctly, just as more than one row. Budget
tracking is pre-tax on both sides (a PO item's `line_total`, a GRN item's already-accepted-scaled
`amount`) — tax is a pass-through more than a development cost, and this keeps commitment and actual
computed the same way. A counter purchase (no PO, no phase on the GRN) can only count against a
budget line with no phase or cost centre set; there is nowhere on today's counter-purchase form to
name one. A stock transfer posts nothing to the GL at all — it moves location, not value, and
Inventory — Materials is one company-wide account regardless of warehouse.

### Found by testing

- **The trial balance and general ledger silently excluded every reversed entry's own lines,
  contradicting the module's own docstring** ("a reversed entry's own lines are cancelled out by its
  reversal's lines rather than excluded"). The query filtered `status == POSTED`, and a reversed
  entry's status is `REVERSED`, not `POSTED` — so only the reversal's lines showed, never cancelled
  out against anything, leaving a real balance sitting in the trial balance for a movement that had
  been undone. Present since 4a; nothing in 4a's own tests reversed an entry and then re-read the
  trial balance, so it went unnoticed until this slice's GRN-cancel test did exactly that. Fixed by
  including both `POSTED` and `REVERSED` in the report's status filter.
- **A released commitment never reduced `committed_amount`.** `release_receipt` credited the line's
  `actual_amount` and the commitment's `released_amount` correctly, but the corresponding decrease to
  `committed_amount` was simply missing from the first version — *committed → actual → remaining*
  looked right only because `committed_amount` and `actual_amount` were each individually correct in
  isolation; only a test that read `committed_amount` **after** a release, not just after a commit,
  caught that the two never reconciled.
- **An invalid posting-rule condition returned a raw 500.** `conditions.validate()` raises its own
  `ConditionError`, which is not one of the API's handled error types — the existing pattern
  (`rules/services/rule_service.py` does the same conversion for a business rule's condition) was not
  yet mirrored here. Wrapped into a `ValidationError` so a bad condition is a clear 422.
- **The commitment-wiring E2E test collided with itself on a rerun.** `budgets` has a real uniqueness
  constraint — one per project and fiscal year — so a browser test creating one against the real
  fiscal year fails the second time it runs against the same shared dev database. Fixed by giving the
  budget lifecycle test its own fiscal year far in the future, never a real one, so reruns never see
  what an earlier run left behind.
- **A cancelled GRN left its journal entry and its budget effect standing.** The first version of
  `cancel` reversed the stock ledger and the PO's received quantities, as it already did before this
  slice, but nothing reversed the GL entry or gave the budget commitment back — a document that never
  happened, by the ledger's own account, would still show as spent against the budget. Caught by
  extending the existing cancel test to check the trial balance and the budget line afterward, not
  just the stock and the PO.

## Testing (state after slice 4b)

| Suite | Result |
|---|---|
| Backend | Full suite passed, coverage 92 % (gate 80 %); ruff, mypy, import contracts 4/4, `alembic check` clean; both migrations round-trip |
| New this slice | 21 tests (`test_budgets.py`): the seeded receipt rules are listed, only `finance.coa.manage` may write one, an unknown condition variable is refused, a rule can be created/edited/deactivated; a budget needs at least one line, two lines for the same phase/cost-centre/account are refused, only `finance.budget.create` may raise one, draft→edit→approve→revise→close end to end with permission checks at each step, `vs-actual` totals the lines; approving a PO commits the matching budget line and posting its GRN releases the commitment, books the actual and posts a balanced entry (asserted via the trial balance), cancelling that GRN reopens the commitment and reverses the entry (asserted the same way), and a counter purchase posts straight to Accounts Payable. Plus new assertions in the existing GRN-cancel test (the reversal really nets to nothing in the trial balance) and stock-movement tests (an issue's posting and cancel-reversal, an adjustment's charge to Site Overheads). Every pre-existing GRN, counter-purchase, PO, sourcing and stock-movement test still passes unchanged with posting rules now mandatory for a receipt, an issue or an adjustment to post. |
| Web | Typecheck, eslint (no warnings), prettier, build clean |
| Browser E2E | `budgets.spec.ts`: a budget drafted, approved, revised (the original figure kept alongside the new one) and closed, through the real browser. The GL/budget wiring for GRNs, issues and adjustments is exercised at the API/database level (`test_budgets.py`, `test_stock_movements.py`, `test_grn_inventory.py`) and, indirectly, by the existing `grn.spec.ts`, `counter-purchase.spec.ts` and `stock-movements.spec.ts` — all already post a real document through the browser, which now also builds and posts the journal entry this slice adds |

## What is not built in Phase 4 yet

| Item | State |
|---|---|
| Vendor invoices, 3-way match, AP ageing | not built (4c) |
| Payment requests, payments, allocations | not built (4d) |
| Minimal AR (customers, invoices, receipts) | not built (4e) |
| Chart-of-accounts CSV import (docs/12 Q2) | not built |
| Opening-balance entry flow, cut-over / parallel-run plan (docs/12 Q2) | not built; cut-over date still needed from the business |
| Posting rules for non-GRN, non-inventory events (invoice, payment) | arrives with 4c/4d, same `posting_rules` table |
| A per-line phase/cost centre on a purchase order or counter purchase | not built; both are header-level today (docs/02 §5), which is what 4b's commitment/release grouping assumes |
