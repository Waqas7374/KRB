# 03 — RBAC Model

## 1. The model in one sentence

A user holds **grants**; each grant pairs a **role** (a bundle of permissions) with a **scope**
(global, company, project, site or department); an action is allowed when the user holds *some*
grant whose role contains the required permission **and** whose scope covers the record being
touched.

```
User ──< UserRoleGrant >── Role ──< RolePermission >── Permission
             │
             └── scope_type + scope_id   (GLOBAL | COMPANY | PROJECT | SITE | DEPARTMENT)
```

This is deliberately *not* ABAC. Attribute rules are impossible to explain to an operations
manager; "Site Manager, at Green Valley Site 2" is something they can reason about and audit.

## 2. Permission catalogue

Permissions are `module.action`, with a small set of qualifiers. `.view` implies read of list and
detail; `.approve` never implies `.create`.

| Module | Permissions |
|---|---|
| projects | view, create, update, close, delete |
| sites | view, create, update, manage_geofence |
| users | view, create, update, deactivate, assign_roles, reset_password |
| roles | view, manage |
| vendors | view, create, update, approve, suspend, manage_bank_details |
| materials | view, create, update, deactivate |
| units | view, manage, manage_conversions |
| procurement | pr.view, pr.create, pr.submit, pr.approve, rfq.view, rfq.create, rfq.issue, quotation.view, quotation.record, quotation.select, po.view, po.create, po.approve, po.send, po.amend, po.cancel |
| deliveries | view, create, update_draft, submit, review, approve, reject, request_correction, reopen, export |
| grn | view, create, approve, cancel |
| inventory | view, issue, transfer, adjust, approve_adjustment, view_valuation |
| rates | view, create, approve, view_history |
| finance | coa.view, coa.manage, gl.view, gl.create, gl.post, gl.reverse, ap.view, ap.create, ap.match, ap.approve, payment.view, payment.request, payment.approve, payment.execute, ar.view, ar.create, ar.receipt, budget.view, budget.create, budget.approve, period.close |
| hr | employee.view, employee.create, employee.update, employee.view_salary, attendance.view, attendance.mark, attendance.approve, leave.view, leave.request, leave.approve, payroll.view, payroll.process, payroll.approve |
| reports | view, view_financial, export |
| audit | view |
| settings | view, manage, manage_rules, manage_workflows |
| notifications | view_own, broadcast |

`hr.employee.view_salary` and `finance.*` are separated from the general `hr.employee.view` on
purpose: an HR clerk maintaining addresses must not see compensation.

## 3. Standard roles

Seeded as `is_system = true`, editable except for Super Administrator.

| Role | Permission set | Typical scope |
|---|---|---|
| **Super Administrator** | `*` | GLOBAL |
| **CEO / Executive** | all `.view` + `reports.view_financial` + high-limit `*.approve` | COMPANY |
| **Finance Manager** | all `finance.*`, `reports.view_financial`, `vendors.view`, `procurement.po.view`, `audit.view` | COMPANY |
| **HR Manager** | all `hr.*` incl. `view_salary`, `users.view/create/update` | COMPANY |
| **Procurement Manager** | all `procurement.*`, `vendors.*` (excl. `manage_bank_details`), `rates.*`, `materials.view` | COMPANY |
| **Project Manager** | `projects.view/update`, `procurement.pr.*`, `procurement.po.view/approve`, `deliveries.view/review`, `inventory.view/issue`, `finance.budget.view`, `hr.attendance.approve`, `hr.leave.approve`, `reports.view` | PROJECT (their projects) |
| **Site Manager** | `deliveries.*` (excl. `reopen`), `grn.view/create`, `inventory.view/issue/transfer`, `hr.attendance.*`, `reports.view` | SITE |
| **Store Manager** | `inventory.*`, `grn.*`, `materials.view`, `deliveries.view`, `reports.view` | SITE or PROJECT |
| **Site Staff** | `deliveries.view` (own site), `deliveries.create/update_draft/submit`, `materials.view`, `vendors.view` | SITE |
| **Accounts Officer** | `finance.ap.*`, `finance.ar.*`, `finance.gl.view/create`, `finance.payment.request`, `vendors.view` | COMPANY |
| **Auditor** | every `.view` + `audit.view` + `reports.export`. **No write permission at all.** | COMPANY |

Two invariants:
- A user with **no** grant sees nothing — there is no implicit default access.
- `Auditor` is enforced twice: by having no write permissions, and by a read-only guard in the
  session that rejects any non-GET request for users whose every grant is a read-only role.

## 4. Scope resolution

`scope_type` is ordered `GLOBAL > COMPANY > PROJECT > SITE > DEPARTMENT`. A grant covers a record
when the grant's scope is the record's scope or an ancestor of it.

```
GLOBAL          covers everything
COMPANY:c       covers rows with company_id = c
PROJECT:p       covers rows with project_id = p  (and their sites)
SITE:s          covers rows with site_id = s
DEPARTMENT:d    covers rows with department_id = d
```

**Rows that carry no project/site** (chart of accounts, materials, vendors) are covered by COMPANY
or GLOBAL grants only. A Site Manager therefore cannot edit the material master, which is correct.

### Enforcement: filter, do not post-filter

At request start, `access.resolve(user)` produces a cached `AccessContext`:

```python
@dataclass(frozen=True)
class AccessContext:
    user_id: UUID
    company_ids: frozenset[UUID]
    permissions: frozenset[str]          # union across all grants
    scopes_by_permission: Mapping[str, ScopeSet]   # permission -> allowed scopes
    is_global: bool
```

Repositories accept it and append the predicate to the SQL:

```python
stmt = select(Delivery).where(Delivery.company_id.in_(ctx.company_ids))
stmt = scope_filter(stmt, Delivery, ctx, permission="deliveries.view")
# -> AND (true | delivery.project_id IN (...) | delivery.site_id IN (...))
```

Filtering in SQL rather than in Python is what makes pagination counts, aggregates and exports
correct. A post-filter would report "247 results" and show 12.

The context is cached in Redis under `access:{user_id}:{grants_version}`; any grant change bumps
`grants_version` on the user row, invalidating it without a cache sweep.

## 5. Permission checks in the three tiers

```python
# 1. Route level — coarse
@router.post("/purchase-orders/{po_id}/approve",
             dependencies=[Depends(require("procurement.po.approve"))])

# 2. Service level — scope + record state
async def approve_po(self, po_id: UUID, ctx: AccessContext) -> PurchaseOrder:
    po = await self.repo.get_for_update(po_id, ctx)      # raises 404 if out of scope
    self.policy.assert_can_approve(po, ctx)              # state + self-approval + limit
    ...

# 3. Field level — projection
PurchaseOrderRead.model_validate(po, context={"access": ctx})
# drops `unit_rate` for users without procurement.po.view_pricing, etc.
```

**Out of scope returns 404, not 403** — a 403 confirms the record exists, which leaks the vendor
list and the PO numbering sequence.

## 6. Rules the engine enforces beyond the permission bit

| Rule | Where |
|---|---|
| No self-approval — the approver may not be the document's creator, unless the step sets `allow_self_approve` | Approval engine, `approval_steps` |
| Approval value limits per role, per project | `business_rules` with `rule_type = APPROVAL_LIMIT`, resolved per-document |
| Site Staff may only create deliveries for a site they hold a grant on, and only for **today +/- N days** | Delivery service + `business_rules.LATE_SUBMISSION` |
| Salary fields are stripped from every HR response without `hr.employee.view_salary` | Pydantic serialisation context |
| Vendor bank-account changes require `vendors.manage_bank_details` **and** produce an audit entry with old/new masked values | Vendor service |
| A user cannot grant a role containing a permission they do not themselves hold | `users.assign_roles` service check |

## 7. Frontend mirroring

The login response returns the flattened permission set and scopes. `web/src/lib/permissions.ts`
exposes `useCan("procurement.po.approve", { projectId })`, used to hide navigation, disable
buttons and skip queries. **This is a UX affordance only** — every check is re-run server-side. The
generated route manifest marks each route with its required permission so the router can 404
directly rather than rendering a page that will fail its first query.

## 8. Testing

`backend/tests/authz/` runs a matrix: for each of the ~12 seeded roles x each protected endpoint,
assert the expected status (200 / 403 / 404). The matrix is a data file, so adding an endpoint
without adding its authorisation expectation fails CI.
