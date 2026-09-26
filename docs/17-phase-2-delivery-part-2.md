# 17 — Phase 2, part 2: RFQs, quotations, purchase orders

Delivered 2026-09-25 (backend, frontend and browser E2E). Together with
[16-phase-2-delivery-part-1](16-phase-2-delivery-part-1.md) this is the
procurement chain from request to approved order. The close-out items added
afterwards (purchase-order PDF, SLA reminders, the nightly integrity check) are
listed under "Close-out". Budget commitments and approval limits are deliberately
deferred to the phases that own their dependencies — see "Still to do".

**Done-when, extended:** an approved purchase request becomes an RFQ, three
vendors quote, a person selects one *and says why*, the order routes for
approval by its value, and on approval the request's lines are marked sourced —
driven through a browser in `e2e/tests/sourcing.spec.ts` with PostgreSQL asserted
at each step.

---

## What was built

| Piece | Behaviour | Where |
|---|---|---|
| RFQ | Raised from the **unsourced** remainder of an approved request (or stated directly through the API). Only active vendors can be invited. Issuing needs lines, ≥ 1 vendor and a due date, and re-checks that no invited vendor has been suspended since. Draft-only editing; once issued, invitations are declined rather than deleted so the record of who was asked stays. | `procurement/services/rfqs.py` |
| Quotation | Recorded per invited vendor: rate, discount %, tax %, quantity (may be partial), delivery days. Totals are computed server-side by one pricing module shared with purchase orders. One per vendor per RFQ. | `services/quotations.py`, `domain/pricing.py` |
| Comparison | Vendors side by side, line by line. Marks the lowest **net** rate (after discount, before tax), shows the request's budgeted rate for reference, flags partial quotes, and will not call a vendor who quoted 1 line of 5 the "lowest total". Rejected quotations leave the grid. | `quotations.compare`, `GET /rfqs/{id}/comparison` |
| Selection (§10) | A person selects and gives a written reason (≥ 10 characters) — even when it is the cheapest bid. Stored on the quotation and in the audit log. The database refuses `SELECTED` without a reason and permits one winner per RFQ. A lapsed `valid_until` cannot be selected. Choosing another vendor demotes the first; withdrawing is refused once an order stands. | `quotations.select_quotation`, migration `06cad11c4e5d` |
| Purchase order | From a selected quotation (lines, rates, terms copied) or direct. Routed by the approval engine; seeded tiers `< 500 000` procurement · `< 5 000 000` + finance · else + executive. Context variables include `is_amendment`, `has_quotation`, `variance_vs_quotation_pct`, `vendor.*`. | `services/purchase_orders.py`, `seeds/approvals.py` |
| Sourcing loop | On **approval** an order claims its quantities from the linked request lines under a row lock (`sourced_quantity`), and the request becomes `PARTIALLY_SOURCED` / `SOURCED`. Two orders drafted for the same units cannot both be approved. Cancel, amend and close give quantity back (close returns only the unreceived part). Approval closes the RFQ; cancelling the order reopens it so another vendor can be chosen. | `purchase_orders.claim_sourcing / release_sourcing` |
| Amend | An approved / sent / acknowledged order goes back to **draft**, releases its claim, bumps `revision`, and must be approved again. Refused once goods have been received. | `purchase_orders.amend` |
| Send / acknowledge | Approved → sent (refused if the vendor has been suspended since) → acknowledged. | `purchase_orders.send`, `.acknowledge` |
| Price masking | A reader without `procurement.po.view_pricing` sees quantities but no rates, discounts, taxes or totals — in the list and the detail. | `api/po_routes.py` |
| Frontend | RFQ list / form / detail (with vendor invitations), quotation form with live line totals, quotation detail with the selection panel, **comparison grid**, purchase-order list / form / detail with the approval trail and amend / cancel / close dialogs. "Request quotations" on an approved purchase request. | `web/src/features/procurement/` |

## Decisions worth knowing

1. **The whole quotation is awarded, not line by line.** Splitting one RFQ across
   vendors is done by ordering the remainder through a second RFQ or a direct order —
   the request's `sourced_quantity` makes that safe.
2. **Sourcing counts at approval, not at draft.** A draft or pending order claims
   nothing; the check is made at submit (fast feedback) *and* again under a row lock
   at approval (the one that matters).
3. **`FINANCE_MANAGER` gains `procurement.po.approve`.** The seeded workflow names
   finance as a signer and the engine refuses a role that cannot approve the document.
   An order the procurement manager raised is signed by finance — the first step's
   escalation target — rather than approving itself.
4. **Amendment reuses the ordinary edit-and-resubmit path** instead of a separate
   revision table; the audit log holds each state. If you need to *read* a previous
   revision's lines back, that is a further table.

## Bugs found while building it

- A new `Rfq` had `vendors` unset, so the first access lazy-loaded inside an async
  request (`MissingGreenlet`). Fixed by constructing it with its children.
- My own tests set a vendor `SUSPENDED` without a reason and hit the database's
  `suspension_requires_reason` check — the constraint doing its job, not a bug in
  the code.
- Adding a second seeded workflow broke a Phase 2 E2E assertion that assumed one
  "New version" button on `/approval-workflows`. The assertion was fixed; the screen was right.
- Line endings: the working tree mixes CRLF and LF although `.gitattributes` says
  LF. Nothing to fix here, but a bulk rewrite of a file from a Windows script will
  turn its every line into a diff.

## Testing

| Suite | Result |
|---|---|
| Backend `pytest` | 448 passed (434 + 11 SLA / integrity + 3 PDF), coverage ≥ 90 % (gate 80 %); ruff, mypy --strict (165 files), import contracts 4/4, `alembic check` clean; migration downgrade / upgrade round-trips |
| New backend | 30 integration (`test_sourcing.py`: RFQ rules, quotation pricing, comparison, selection, PO lifecycle, sourcing loop, oversourcing race, price masking, chain tiers) + 10 unit (`test_pricing.py`) |
| Web unit | 55 passed; typecheck, eslint `--max-warnings 0`, prettier clean |
| Browser E2E | 13 passed — the new spec drives request → RFQ → three quotes → selection with a reason → order → finance approval → sourced → sent → site manager sees no prices → amend → cancel → RFQ reopened |

## Close-out (same day)

| Piece | Behaviour | Where |
|---|---|---|
| Purchase-order PDF | `GET /purchase-orders/{id}/pdf`. Needs `procurement.po.view_pricing` (a printout without amounts is useless; one with them must not reach a reader who may not see them). Unapproved and cancelled orders carry a banner so a printed draft cannot pass for the real thing. All values are escaped and WeasyPrint is told never to fetch external resources. Rendered in a worker thread. A reusable `platform/pdf.py` serves GRNs, payslips and reports later. | `api/po_document.py`, `platform/pdf.py` |
| SLA reminders | `approvals.remind` (hourly): approvers are reminded at 50 % and again at 90 % of a step's SLA, once each. A step first seen at 95 % gets one reminder, not two. Overdue steps are left to escalation. | `engine.remind_due`, migration `0b818b783b15` |
| Integrity check | `approvals.reconcile` (02:15): finds a pending request with no active step, a pending step on an ended request, a step nobody can decide, and any disagreement between a document being "pending approval" and it having a pending request. One alarm per company to the super administrators, and an error log line per finding. | `services/integrity.py` |

An earlier version of this document said `weasyprint` was "not in the image". That
was a check made on the host, not in the container — the image has always had it. The
only addition needed was `libharfbuzz-subset0` in the Dockerfile, to silence a
deprecation notice.

## Still to do in Phase 2

- **Budget commitments** on PO approval. They need the budget tables, which arrive
  with finance (Phase 4); the hook is `PurchaseOrderApprovals.on_approved`. Doing them
  earlier would mean inventing tables Phase 4 must then reshape.
- ~~**Approval limits**~~ — delivered in slice 3a of Phase 3, on the business-rules store
  it needed: [18-phase-3-delivery](18-phase-3-delivery.md).
- **Nothing is emailed to vendors.** "Issued" and "Sent" are states a buyer records;
  the document is shared out of band until the vendor portal (the `access_token_hash`
  column is already there) or an email template exists.
- **No quotations list screen** — quotations are reached from their RFQ (the API list
  exists). RFQs are raised from a purchase request in the UI; the API also accepts a
  request-less RFQ.
- A visual workflow builder, and the dynamic approvers that need HR / budgets —
  unchanged from doc 16.
- Receiving against an order (`received_quantity`, `PARTIALLY_RECEIVED`, `RECEIVED`)
  belongs to Phase 3; the columns and statuses are in place.

## For the user to confirm

- The seeded **purchase-order tiers** (500 000 / 5 000 000) and the roles they name.
  They are editable at `/approval-workflows`, but they are a guess at how KRB signs.
- Whether a **minimum number of quotations** is required before selection (e.g. three,
  or a recorded justification for fewer). Nothing enforces one today.
