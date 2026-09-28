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
| 4c | Vendor invoices, the 3-way match with tolerance, AP ageing | 4a, 4b | done |
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

## 4c — Vendor invoices, the 3-way match, AP ageing

**The done-when this slice exists for (docs/02 §8): a vendor's bill is entered, checked against what
was ordered and what actually arrived within tolerance, and only once it matches does approving it
touch the GL — and even then, never by re-booking what the GRN already booked.** A GRN line posts its
own cost at receipt (4b); a 3-way-matched invoice line's own entry only clears the accrual (2110) that
posting left behind into a real payable (2100), through the *same* posting rule the GRN used, so the
two can never disagree about which account that is. A GRN with no PO behind it (a counter purchase,
already posted straight to 2100) degrades the match to 2-way and the matching invoice line posts
nothing at all — the GRN already booked it in full. A line with neither an order nor a GRN behind it is
unmatched: a direct cost, priced and accounted for by hand, and the one kind of line that is new to the
ledger here.

| Piece | Behaviour | Where |
|---|---|---|
| Tax codes | One rate, of one kind (`SALES_TAX` / `WITHHOLDING`), on one thing (`GOODS` / `SERVICES` / `PAYMENT`), with a `section_code` for a withholding row (docs/12 Q1). Plain CRUD, the same shape as posting rules, gated by the existing `finance.coa.view` / `finance.coa.manage` (a tax code is chart-of-accounts configuration, not its own permission). | `finance/services/tax_codes.py` |
| Vendor invoices | `VendorInvoice` → `VendorInvoiceItem` (one row per billed line, each resolving its own `account_id` — never one account for the whole invoice, since a single bill can mix a PO-backed line, a counter-purchase line and a direct line) → `VendorInvoiceMatch` (one row per item once matched: `match_type`, the three variances, `within_tolerance`, and a `tolerance_rule_id` snapshot so tightening a rule later never rewrites why an old line passed). `DRAFT` / `DISPUTED` are the only editable statuses; editing replaces every item wholesale and resets the invoice to `DRAFT`. | `finance/models.py`, `finance/services/vendor_invoices.py` |
| Account resolution | A line naming a GRN item asks `posting_rules.resolve_receipt_account(..., is_po_backed=True)` — the *exact* call the GRN itself made — for its account when the GRN item has a `po_item_id` (3-way); a GRN item with none gets `account_id = None` (2-way, nothing to post); a line naming neither gets whatever account the person named by hand (unmatched). | `vendor_invoices.py::_build_item` |
| The 3-way match | An item naming a GRN line is checked against it: quantity against what was actually *accepted*, rate against the order's own rate when the GRN line came from one (against the GRN's own recorded rate otherwise, so a 2-way line matches when it repeats what the GRN already said). Reuses the *same* `QTY_TOLERANCE` / `PRICE_TOLERANCE` business rules a delivery's own check already resolves through — a company that tightens delivery tolerance and invoice tolerance moves the same number. A rule with nothing configured always passes, the same permissive-fallback philosophy `resolver.py` already states. Matching is idempotent and re-runnable: it deletes and rebuilds every match row for the invoice each time, so editing a disputed invoice and matching it again always reflects only the current lines. | `vendor_invoices.py::_check_item`, `match()` |
| Approval and posting | Only a `MATCHED` invoice may be approved. Every item with a real `account_id` is grouped and debited (its amount plus its own tax); every item with `account_id = None` (a 2-way line) is skipped entirely; the posting rule for `INVOICE`/`PAYABLE` supplies the one credit account (2100) for the lot. A fully 2-way-matched invoice — nothing to post — approves with `journal_entry_id` left `None` and no posting-rule lookup at all, rather than failing over a rule it never needed. A 3-way-matched line's approval also writes the order's own `invoiced_quantity` (`receiving.apply_invoice`, the third running total alongside `received_quantity`/`accepted_quantity` that makes the whole match possible without recomputing history). | `vendor_invoices.py::approve` |
| Dispute | A matched invoice can be sent back to `DISPUTED` by hand, with a required reason — distinct from an automatic dispute (any line outside tolerance disputes the whole invoice the moment it is matched, no separate step). Either way, a disputed invoice is editable and rematchable, the same as a draft. | `vendor_invoices.py::dispute` |
| AP ageing | Every vendor with something still owed on an `APPROVED`/`PARTIALLY_PAID` invoice, `(total_amount - paid_amount)` bucketed by days past `due_date` `as_of` a given date — current / 1-30 / 31-60 / 61-90 / 90+. A draft or disputed invoice is not yet a real obligation and contributes nothing. | `vendor_invoices.py::payables_aging` |
| Cross-module lookups | `procurement/services/po_lookup.py::item()` (one order line, added this slice) and the new `grn/services/grn_lookup.py::item()` — plain dataclasses, never the ORM models, so finance can read a PO/GRN line's facts without crossing the module-layering contract `tests/unit/test_architecture.py` enforces. | `procurement/services/po_lookup.py`, `grn/services/grn_lookup.py` |
| Screens | Vendor invoices (list with status/vendor filters; new/edit form with a line either picked from one of the vendor's own posted GRNs — a dialog lists the GRN, then its lines, and fills quantity/rate from what was actually received — or entered direct with its own account; detail with match/dispute/approve actions and, once matched, each line's match type and any variance). Tax codes (list with a create/edit drawer, the same shape as posting rules). Payables ageing (one report, bucketed columns, an "as of" date). | `web/src/features/finance/VendorInvoicePages.tsx`, `TaxCodesPage.tsx`, `PayablesAgingPage.tsx` |

**Deliberately simplified.** Tax is folded into the same debit account as the line's own cost — no
separate input-tax-recoverable account — since a land-development company's purchase-side sales tax is
closer to a pass-through cost than a distinct asset here. `withholding_amount` on the invoice is
informational only: docs/12 Q1 frames withholding as something that happens "on supplier payments," so
its actual GL posting arrives with 4d, not here. A purchase order named on the invoice header
(`purchase_order_id`) is metadata only — matching is entirely driven by what each *line* names (a
`grn_item_id`, which itself carries the PO line behind it), so the form does not ask for one. Approving
a vendor invoice is a single direct permission check (`finance.ap.approve`), not routed through the
shared `/approvals` engine — the same precedent 4b's budget approval set, and what docs/07's literal
API spec gives it (`POST /finance/vendor-invoices/{id}/approve`, no `/submit` or `/approvals/requests/
...` reference).

### Found by testing

- **`VendorInvoiceRead.outstanding_amount` (and, before a fix, every other route-computed field
  without a schema default) made `model_validate()` itself fail** with "Field required" — a route
  cannot set a field after `model_validate()` runs if the schema has no default for it, since Pydantic
  populates every required field from the ORM row's own attributes up front, and this one is computed,
  not a column. See [[finance-module-gotchas]] #8.
- **`vars()` on `AgeingBucket` (a frozen, slotted dataclass) crashed with `TypeError: vars() argument
  must have __dict__ attribute`** the first time `/finance/payables/aging` was actually called — slots
  leave no `__dict__` for `vars()` to read. Fixed by using `AgeingBucketRead.model_validate(row)`
  instead, the same construction every other read schema in this codebase already uses. See
  [[finance-module-gotchas]] #9.
- **A duplicate `vendor_invoice_ref` for the same vendor returned 422, not 409.** The first draft
  raised `BusinessRuleError`, which is always 422; a genuine natural-key collision should raise
  `DuplicateError` (409), matching what `ScopedRepository.assert_code_available` already does for a
  duplicate account or tax code. See [[finance-module-gotchas]] #10.
- **Editing a tax code raised `MissingGreenlet` on `updated_at`.** The route validated the row returned
  from `update()` straight into `TaxCodeRead` without refreshing it first, the exact expired-column trap
  `accounts.py`/`posting_rules.py`'s own update routes already carry a fix (and a comment) for — missed
  on tax codes' first pass since it is new this slice. See [[finance-module-gotchas]] #11.
- **Picking a GRN line's rate into an invoice line failed a 4-decimal-place validation**, caught only
  once the browser E2E spec exercised the real picker: a GRN item's `rate`/`unit_cost` columns carry six
  decimal places, coarser than a vendor-invoice line's `Numeric(18,4)`. Fixed by rounding to four places
  at the point of copying. See [[finance-module-gotchas]] #12.
- **The E2E spec's own SQL assertions first failed on a wrong column name** (`journal_entry_id` instead
  of `je_id` on `journal_entry_lines`) and then on raising a purchase order against a freshly-created,
  not-yet-approved vendor (`vendor_not_active`) — neither a product bug, both recorded as
  [[finance-module-gotchas]] #13-14 so a future E2E spec does not repeat either guess.
- **`npm run build`'s `tsc -b` step turned out to already be broken on `main`, unrelated to this
  slice** — confirmed by stashing every 4c change and rebuilding clean against the pre-4c tree
  (`RateGridPage.tsx`'s `Sparkline`, `RfqPages.tsx`'s `variant="default"`). `npm run typecheck` and
  `npm run lint` both stay green regardless, which is why it went unnoticed until this slice actually
  ran the full build script. Not fixed — out of scope for Finance — but now flagged rather than silently
  carried forward. See [[finance-module-gotchas]] #15.

## Testing (state after slice 4c)

| Suite | Result |
|---|---|
| Backend | Full suite passed, coverage 93 % (gate 80 %); ruff, mypy, import contracts 4/4, `alembic check` clean; the new migration round-trips |
| New this slice | 26 tests (`test_vendor_invoices.py`): tax codes (seeded codes listed, only `finance.coa.manage` may write one, a duplicate code is refused, edit by hand); a line with no order or GRN needs an account, a GRN-backed line resolves its own account from the posting rule, a duplicate `vendor_invoice_ref` for the same vendor is refused (409), only `finance.ap.create` may raise an invoice, a draft can be edited and deleted; a 3-way line within tolerance matches clean, a 3-way line with a rate outside tolerance disputes itself, a 3-way line with qty inside the configured tolerance still matches, a 2-way counter-purchase line degrades gracefully, an unmatched direct line always passes its own check, a disputed invoice can be corrected and rematched, a matched invoice can be disputed by hand, only `finance.ap.approve` may dispute; approve requires `MATCHED` first, approving a 3-way match clears the GRN accrual into payable (asserted via the general ledger, since the account nets to zero on the trial balance) and writes the PO's `invoiced_quantity`, approving a 2-way match posts nothing new, an unmatched direct line posts from its own named account, only `finance.ap.approve` may approve; AP ageing buckets an approved invoice correctly and a draft never appears in it, permission check. |
| Web | Typecheck, eslint (no warnings), prettier clean. `npm run build`'s `tsc -b` step fails, but on pre-existing, unrelated errors (see "Found by testing") — confirmed present on `main` before this slice too |
| Browser E2E | `vendor-invoices.spec.ts`: a vendor invoice created against a real GRN line (the PO → delivery → GRN pipeline behind it built directly through the API, the same way `budgets.spec.ts` treats its own prerequisites), matched, and approved — asserted against PostgreSQL: the invoice's status, its journal entry's debit to 2110 and credit to 2100, and the purchase order's `invoiced_quantity`. Full suite (26 specs) passes, including `smoke.spec.ts`, which now also renders the vendor-invoice, tax-code and payables-ageing screens without crashing |

## What is not built in Phase 4 yet

| Item | State |
|---|---|
| Payment requests, payments, allocations | not built (4d) |
| Minimal AR (customers, invoices, receipts) | not built (4e) |
| Withholding tax actually posted to the GL | not built; `vendor_invoices.withholding_amount` is informational only until 4d posts it at payment time |
| Chart-of-accounts CSV import (docs/12 Q2) | not built |
| Opening-balance entry flow, cut-over / parallel-run plan (docs/12 Q2) | not built; cut-over date still needed from the business |
| Posting rules for payment events | arrives with 4d, same `posting_rules` table (`INVOICE`/`PAYABLE` arrived with 4c) |
| A per-line phase/cost centre on a purchase order or counter purchase | not built; both are header-level today (docs/02 §5), which is what 4b's commitment/release grouping assumes |
| `npm run build`'s `tsc -b` step | broken on `main`, pre-existing and unrelated to Finance (`RateGridPage.tsx`, `RfqPages.tsx`) — `typecheck`/`lint` stay green; see 4c's "Found by testing" |
