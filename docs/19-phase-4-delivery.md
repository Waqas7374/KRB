# 19 — Phase 4: Finance

Built in the same additive, slice-by-slice discipline as Phase 3: each slice adds tables, services
and screens and never changes what an earlier slice does, and is committed with its tests before
the next starts. This document is updated per slice; the roadmap ([10-roadmap](10-roadmap.md)) says
what remains.

## Order, and why

| # | Slice | Depends on | Status |
|---|---|---|---|
| 4a | Chart of accounts, accounting periods, manual journal entries, trial balance, general ledger | Phase 2's approval engine | done |
| 4b | `posting_rules` configuration; GRN and inventory auto-posting to the GL; budgets, budget lines, commitment consumption tied to PO submit/approve and GRN posting | 4a | not built |
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

## What is not built in Phase 4 yet

| Item | State |
|---|---|
| `posting_rules` (sub-ledger → GL as configuration) | not built (4b) |
| GRN and inventory auto-posting | not built (4b) |
| Budgets, budget lines, commitment consumption, budget vs actual | not built (4b) |
| Vendor invoices, 3-way match, AP ageing | not built (4c) |
| Payment requests, payments, allocations | not built (4d) |
| Minimal AR (customers, invoices, receipts) | not built (4e) |
| Chart-of-accounts CSV import (docs/12 Q2) | not built |
| Opening-balance entry flow, cut-over / parallel-run plan (docs/12 Q2) | not built; cut-over date still needed from the business |
