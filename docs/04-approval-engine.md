# 04 — Configurable Approval Engine

§25: approvals must be configuration, not code. One engine serves purchase requests, POs, GRNs,
stock adjustments, vendor invoices, payments, journal entries, budgets, vendor onboarding, vendor
rate changes, leave requests and delivery-flag waivers.

---

## 1. Definition model

```
ApprovalWorkflow (doc_type, version, scope)
  └── ApprovalRule   (sequence, condition, stop_on_match)
        └── ApprovalStep (step_no, approver_type, quorum, sla, escalation)
```

**Matching:** rules are evaluated in `sequence` order against the document context; the first rule
whose `condition` is true supplies the step chain. A final catch-all rule (`condition: true`) is
mandatory, so no document can be submitted into a void.

### Condition language

Structured JSON, evaluated by a small interpreter over a whitelisted operator set — **never**
`eval`, never a DSL string that reaches Python.

```json
{ "and": [
    { ">=": [{ "var": "total_amount" }, 100000] },
    { "<":  [{ "var": "total_amount" }, 1000000] },
    { "in": [{ "var": "project.code" }, ["GVH", "RSD"]] }
]}
```

Supported operators: `and or not == != > >= < <= in not_in between var if`. Variables come from a
per-doc-type **context builder** that publishes a documented, stable set of fields:

| doc_type | context variables |
|---|---|
| `purchase_request` | total_amount, currency, project.*, site.*, department.*, phase.*, requester.role, item_count, max_line_amount, has_budget, budget_remaining, is_capex |
| `purchase_order` | total_amount, vendor.*, vendor.is_new, project.*, budget_remaining, is_amendment, variance_vs_quotation_pct, has_quotation |
| `vendor_invoice` | total_amount, vendor.*, match_status, max_variance_pct, is_po_backed |
| `payment` | amount, vendor.*, method, is_advance, aging_days |
| `stock_adjustment` | value_abs, quantity_abs, reason_code, warehouse.* |
| `delivery_waiver` | flag_types, deviation_pct, delivery_amount, vendor.* |
| `vendor_rate` | old_rate, new_rate, change_pct, material.*, vendor.* |
| `leave_request` | days, leave_type.code, employee.department, balance_after |

Adding a variable is a one-line change in the context builder plus a catalogue entry — the engine
itself never changes.

### Step model

| Field | Meaning |
|---|---|
| `approver_type` | `ROLE` (anyone holding role R in the doc's scope) · `USER` (a named person) · `DYNAMIC` (`project_manager`, `department_head`, `requester_manager`, `site_manager`, `budget_owner`) · `GROUP` (an explicit list) |
| `quorum_type` | `ANY` (one of them) · `ALL` (every eligible approver) · `N_OF_M` |
| `sla_hours` | Drives the due date, the reminder schedule and the ageing report |
| `escalate_to_*` | On SLA breach: notify, or auto-delegate to a superior |
| `allow_delegate` | Approver may hand the step to a named delegate, recorded as such |
| `allow_self_approve` | Default **false**. The document's creator is skipped as an eligible approver; if no one else is eligible, the step escalates rather than silently auto-approving |
| `condition` | Optional per-step condition — lets a chain skip a step without needing a separate rule |

### Worked example (§25)

```yaml
workflow: purchase_request, version 3, scope: COMPANY
rules:
  - seq: 1   condition: { "<": [total_amount, 100000] }
    steps:
      - 1: DYNAMIC project_manager,  ANY,  sla 24h
  - seq: 2   condition: { "and": [ {">=": [total_amount, 100000]}, {"<": [total_amount, 1000000]} ] }
    steps:
      - 1: DYNAMIC project_manager,     ANY, sla 24h
      - 2: ROLE    procurement_manager, ANY, sla 24h, escalate_to ROLE finance_manager
  - seq: 3   condition: true                     # catch-all, >= 1,000,000
    steps:
      - 1: DYNAMIC project_manager,     ANY, sla 24h
      - 2: ROLE    procurement_manager, ANY, sla 24h
      - 3: ROLE    finance_manager,     ANY, sla 48h
      - 4: ROLE    executive,           ANY, sla 72h
```

---

## 2. Runtime model

```
ApprovalRequest (doc_type, doc_id, workflow_snapshot, current_step_no, status)
  └── ApprovalRequestStep (step_no, status, due_at, escalated_at)
        └── ApprovalAction (actor, action, comments, acted_at)   [append-only]
```

**`workflow_snapshot jsonb` is the critical column.** At submission the resolved rule and its full
step chain are frozen onto the request. An administrator editing the workflow tomorrow cannot
change who must approve a document submitted today, and the audit trail can always answer "what
were the rules when this was approved?" (§45). Workflow edits create a new `version` row; in-flight
requests keep the old one.

### State machine

```
        submit
DRAFT ──────────► PENDING ──approve(final step)──► APPROVED
                    │ │
                    │ └──reject──► REJECTED ──(resubmit creates a NEW request)
                    │
                    ├──recall (by initiator, only while no decision recorded)──► RECALLED
                    └──request_changes──► CHANGES_REQUESTED ──resubmit──► PENDING
```

Rules:
- Approving the last step sets the **document's** status through its own state machine inside the
  same transaction, and emits `<doc>.approved` to the outbox.
- A rejection at any step terminates the whole request. The document returns to DRAFT with the
  rejection reason attached; resubmission creates a fresh request rather than resuming the old one,
  so the trail shows two distinct attempts.
- A document mutated while PENDING auto-recalls its request (`document_hash` is stored on the
  request and re-checked at each decision). Approving a document that changed after you looked at
  it is a real-world control failure; this makes it impossible.

> **Implementation note (2026-09-25).** `CHANGES_REQUESTED` is terminal, not a loop back to `PENDING`:
> resubmission creates a new request routed afresh, because the requested edit can change which rule applies.
> Dynamic approvers other than `project_manager` and `site_manager` are refused at save time until HR and
> budgets exist. See [16-phase-2-delivery-part-1](16-phase-2-delivery-part-1.md).

### Eligibility

`eligible_approvers(step, document)` returns users who (a) satisfy the approver_type, (b) hold a
grant whose scope covers the document, (c) are not the creator unless `allow_self_approve`, and
(d) are active. The list is materialised onto the step so the approval inbox is a simple indexed
query rather than a per-request permission computation.

### The approval inbox

`GET /api/v1/approvals/inbox` returns every pending step the caller is eligible for, across all
document types, with document summary, amount, age, SLA state and a deep link. One screen, one
query, backed by `approval_request_steps (status, due_at) WHERE status='PENDING'`.

---

## 3. Integration contract

Any module opts in by implementing one small interface — the engine knows nothing about purchase
orders:

```python
class ApprovableDocument(Protocol):
    doc_type: ClassVar[str]
    async def build_approval_context(self) -> dict[str, Any]: ...
    async def on_approved(self, request: ApprovalRequest) -> None: ...
    async def on_rejected(self, request: ApprovalRequest, reason: str) -> None: ...
    def approval_document_hash(self) -> str: ...
```

Submission is one call:

```python
request = await approvals.submit(document, actor=ctx.user_id)
```

If no workflow is configured for the doc type, submission fails loudly at configuration time
rather than silently auto-approving. Auto-approval must be an explicit workflow with zero steps.

---

## 4. Approval limits vs. approval workflows

These are two different things and are kept separate:

- The **workflow** decides *who must sign*.
- An **approval limit** (`business_rules.APPROVAL_LIMIT`, scoped to role x project x doc type)
  decides *how much a given signer may authorise*. A Project Manager with a 500 000 limit who is
  the named approver on a 900 000 PO gets a blocked action with an explicit message and the step
  escalates, rather than a silently accepted over-limit approval.

---

## 5. Background jobs

| Job | Cadence | Action |
|---|---|---|
| `approvals.remind` | hourly | Notify approvers at 50% and 90% of SLA |
| `approvals.escalate` | hourly | On SLA breach, apply the step's escalation policy and record it |
| `approvals.expire` | daily | Optionally auto-recall requests idle beyond N days (configurable, default off) |
| `approvals.reconcile` | nightly | Assert no document sits in PENDING_APPROVAL without a live request — an integrity alarm, not a fixer |

---

## 6. Testing

- Unit: condition evaluator against a table of expressions and contexts, including the operator
  whitelist rejecting anything unknown.
- Unit: rule selection — first-match wins, catch-all always present.
- Integration: the §25 three-tier PR example at 50 000 / 500 000 / 5 000 000, asserting exact step
  chains and that changing the workflow mid-flight does not affect an in-flight request.
- Integration: self-approval blocked; delegation recorded; rejection resets the document; document
  edit auto-recalls.
- Authorisation: an ineligible user gets 403 on the decision endpoint even with the permission bit.
