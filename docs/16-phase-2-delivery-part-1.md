# 16 — Phase 2, part 1: approval engine and purchase requests

Delivered 2026-09-25 (backend and frontend). Phase 2 in [10-roadmap](10-roadmap.md)
is **not finished**: this is the approval engine plus its first consumer. RFQs,
quotations and purchase orders followed in part 2
([17](17-phase-2-delivery-part-2.md)); the budget commitment is still to build —
see "Still to do" below.

**Done-when, as far as it goes:** the §25 three-tier purchase-request chain
routes correctly at 50 000 / 500 000 / 5 000 000, and editing a workflow does
not change a request already in flight. Both are asserted in
`backend/tests/integration/test_approvals.py`; the first is also driven through
a browser in `e2e/tests/approvals.spec.ts`.

---

## What was built

| Piece | Behaviour | Where |
|---|---|---|
| Condition language | JSON, whitelisted operators, no `eval`. Decimal arithmetic. A missing variable makes an ordering comparison false, so an unevaluable rule falls through to the catch-all. Bounded depth and size. | `approvals/domain/conditions.py` |
| Workflow definitions | Immutable versions stored as validated JSON. The last rule must be a literal `true`. Roles named in steps must exist *and hold the document's approve permission*; unknown variables are refused — all at save time, not when a 5 M request mis-routes. | `domain/definition.py`, `services/workflow_service.py` |
| Runtime | Submit freezes the chosen rule and step chain onto the request (`workflow_snapshot`). Steps activate in order; ANY / ALL / N-of-M quorum; per-step conditions; explicit zero-step auto-approval; project-scoped override workflows. | `services/engine.py` |
| Eligibility | Approvers are materialised per step so the inbox is one indexed query. The submitter is never their own approver (the step's escalation target gets it instead). Every decision re-checks the actor still holds access, and that the document is unchanged (hash) — otherwise the request is recalled, not approved. | `services/engine.py` |
| Escalation | Hourly Celery task; each overdue step escalates once, adding the escalation target as approvers. | `workers/tasks/approvals.py` |
| Trail | `approval_actions` is append-only, enforced by the existing DB trigger. | migration `a1e7e9334421` |
| Notifications | Approvers told when a step reaches them; initiator told of approve / reject / changes / auto-recall; approvers told of escalation. All through the outbox. | `workers/tasks/outbox.py` |
| Purchase requests | Draft → edit → submit → cancel. Totals recomputed server-side. A line without an estimated rate cannot be submitted (the total decides who signs). Numbered `PR-2026-27-00003` through the gapless counter. | `modules/procurement/` |
| Frontend | Approval inbox, purchase-request list/form/detail with the approval trail and decision buttons, workflow view + JSON editor + chain simulator. Server-supplied `can_decide` / `can_recall` / `can_edit` flags, so the UI never re-derives approval rules. | `web/src/features/approvals`, `features/procurement` |

## A deliberate deviation from docs/04

docs/04 draws `CHANGES_REQUESTED → resubmit → PENDING` on the *same* request.
It is implemented as terminal instead: resubmitting creates a **new** request,
routed afresh. Reason: the requested change can move the document into a
stricter tier (a PR edited from 90 000 to 900 000 must pick up the procurement
step), and resuming the old snapshot would route it by rules that no longer
describe it. Covered by `test_changes_requested_then_a_bigger_edit_is_routed_afresh`.
`docs/04` should be read with this note.

## Bugs found and fixed

1. **Site and project managers saw zero materials, units and vendors.** A
   project- or site-scoped grant of `materials.view` resolved to "no rows"
   because those tables have no project or site column. It meant a site manager
   could not raise a purchase request at all — and, unfixed, could not capture
   a delivery in Phase 3. Reference-data models now declare
   `__scope_company_wide__`; warehouses (which carry a site) stay scoped.
2. **A failed submit could leave a request in `PENDING_APPROVAL`.** The status
   change and the routing now share a savepoint.
3. **The test process never configured logging**, so structlog's default
   rendered exception tracebacks with every frame's local variables through
   `rich`. One unhandled error inside a request took minutes to print and the
   suite looked hung. Tests now use the application's logging setup, and the
   dev console renderer no longer shows locals.
4. **Coverage did not follow greenlets**, under-reporting everything after the
   first DB `await` (an engine driven by 21 tests showed 53 %). Fixed with
   `concurrency = ["greenlet","thread"]`. **The previously reported 80.4 % was
   under-measured; true coverage is 89.75 %.**
5. Seeded project and site manager columns were empty, which the "project
   manager" approval step reads. Filled when empty; an administrator's
   assignment is never overwritten.

## Testing

| Suite | Result |
|---|---|
| Backend `pytest` | 394 passed, coverage 89.75 % (gate 80 %); ruff, mypy --strict, import contracts 4/4, `alembic check` clean; migration downgrade/upgrade round-trips |
| Approval unit tests | 39 — evaluator table, operator whitelist, §25 tiers, boundaries (100 000 is not "under 100k"), definition validation |
| Approval integration | 21 — whole 5 M chain in order and out-of-turn refused, in-flight immunity, self-approval, ineligible user with the permission, rejection/changes/recall/auto-recall, no workflow ≠ auto-approve, escalation once, append-only trail |
| Web unit | 49 passed; typecheck, eslint `--max-warnings 0`, prettier clean; shell ≈ 140 KB gzipped |
| Browser E2E | 12 passed (8 Phase 1 + 4 new: 50 k single-step approval, 500 k two-step with rejection, unpriced refusal, workflow view + simulator) |

## Still to do in Phase 2

- ~~RFQs, quotations, comparison, purchase orders, amendments, the PO → PR sourcing
  loop~~ — delivered in [17](17-phase-2-delivery-part-2.md). Purchase-order **PDF** is
  still open.
- Budget commitments on PO approval (the budget tables themselves are Phase 4).
- **`approvals.remind`** (reminders at 50 % / 90 % of SLA) and
  **`approvals.reconcile`** (nightly integrity alarm). Escalation is built;
  these two are not.
- **Approval limits** (docs/04 §4, `APPROVAL_LIMIT`): who may authorise *how
  much*. Workflows decide who must sign; nothing yet stops an approver
  authorising more than their limit. Needs the business-rules store.
- Workflow editing is a **JSON editor** with server-side validation, not a
  visual builder. Project-scoped override workflows work through the API; the
  UI cannot create them yet.
- Dynamic approvers `department_head`, `requester_manager`, `budget_owner` are
  refused at save time: they need HR (Phase 5) and budgets (Phase 4). Only
  `project_manager` and `site_manager` exist.
- Context variables `has_budget`, `budget_remaining`, `is_capex` arrive with
  budgets; a condition using them simply does not match until then.
- Only `purchase_request` is registered as an approvable document. The registry
  makes the others (PO, GRN, adjustment, invoice, payment ...) a matter of
  adding a handler.

## Decisions for the user

- **A step with no eligible approver** fails the *submission* if it is step 1
  (the author or an admin can fix it immediately), but stays pending with an
  `approval.stuck` notification to the author if it arises later in the chain,
  since failing there would undo a colleague's approval. Say so if you would
  rather it always fail or always escalate.
- **The seeded default workflow** (docs/04 worked example) names the
  procurement, finance and executive roles. Confirm those match how KRB
  actually approves before pilot; it is editable at `/approval-workflows`.
