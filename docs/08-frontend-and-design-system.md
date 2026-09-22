# 08 — Frontend Architecture & Design System

Covers §3, §30, §36, §48.

---

## 1. Architecture

**Feature-sliced**, not layer-sliced. Everything a feature needs lives together:

```
src/features/purchase-orders/
  api.ts             typed fetchers over the shared client
  queries.ts         TanStack Query hooks + query-key factory
  schemas.ts         Zod schemas (form input + server contract narrowing)
  columns.tsx        DataTable column definitions
  components/        PoForm, PoLineEditor, PoStatusBadge, PoApprovalTrail
  routes/            list.tsx, detail.tsx, new.tsx
  index.ts           public surface — other features import only from here
```

### State ownership

| Kind of state | Owner |
|---|---|
| Server data | **TanStack Query** — the cache *is* the model. No duplication into a store. |
| URL state (filters, sort, page, selected tab) | **The URL**, via typed search params. A filtered grid must be shareable and back-button-correct. |
| Form state | React Hook Form + Zod resolver |
| Genuinely global client state | **Zustand**, three small stores only: `authStore` (user, permissions), `uiStore` (sidebar, density, theme), `draftStore` (unsaved multi-step drafts) |

Redux Toolkit is not used: there is no cross-cutting client state complex enough to earn it, and
duplicating server data into a store is the most common cause of stale ERP screens.

### Query conventions

```ts
export const poKeys = {
  all:    ['purchase-orders'] as const,
  list:   (f: PoFilters) => [...poKeys.all, 'list', f] as const,
  detail: (id: string)   => [...poKeys.all, 'detail', id] as const,
};
```

- `staleTime` 30 s for lists, 0 for a document being edited, 5 min for master data.
- Mutations invalidate by key prefix, and additionally invalidate the **approval inbox** and
  **notification** keys when the action can create an approval step.
- Optimistic updates only for genuinely reversible, low-stakes actions (marking a notification
  read, toggling a saved view). **Never** for financial or inventory mutations — showing a PO as
  approved before the server agrees is exactly the lie an ERP must not tell.

### Cross-cutting

- `ApiError` → RFC 9457 parsed into field errors and mapped onto the form by field path.
- Route-level `<ErrorBoundary>` + a global one; a crashed grid never takes down the shell.
- `<Suspense>` with skeletons matched to the real layout (no layout shift).
- Code splitting per route group; the ERP shell loads under 200 KB gzipped.
- `useCan()` gates navigation, actions and queries (mirrors [03-rbac](03-rbac.md) §7).

---

## 2. Application shell

```
+------------------------------------------------------------------------+
| ☰  KRB ERP    [ Green Valley ▾ ]   ⌕ Search…        ⌘K   🔔 3   AR ▾   |
+---------------+--------------------------------------------------------+
| Dashboard     |  Procurement › Purchase Orders › PO-2026-00184          |
| Projects      +--------------------------------------------------------+
| Procurement ▸ |  [ Toolbar: filters · saved views · columns · export ]  |
|   Requests    |  +--------------------------------------------------+  |
|   RFQs        |  |  Dense data table                                |  |
|   Quotations  |  |                                                  |  |
|   Orders      |  +--------------------------------------------------+  |
| Deliveries  ▸ |  247 records · 50 per page                      ‹ 1 2 › |
| Inventory   ▸ |                                                         |
| Finance     ▸ |                                                         |
| HR          ▸ |                                                         |
| Reports       |                                                         |
| Settings      |                                                         |
+---------------+--------------------------------------------------------+
```

- **Left navigation**, collapsible to icons, sections gated by permission. Persisted per user.
- **Project/company switcher** in the header — nearly every screen is project-scoped, so this is
  the single most-used control.
- **Global search** (⌘K / Ctrl-K): cross-entity, permission-filtered, with a type-ahead grouped by
  entity and recent-items memory. Typing a PO number or truck plate must land you on the record.
- **Breadcrumbs** on every detail route, reflecting real hierarchy, not navigation history.
- **Notification bell** fed by SSE.

### Interaction patterns

| Pattern | Used for |
|---|---|
| Full page | Lists, dashboards, complex documents (PO, GRN, invoice) |
| **Side drawer** | Quick view / quick edit from a grid without losing the grid's state |
| Modal | Only for a short, blocking decision: confirm, reject-with-reason, select approver |
| Confirmation dialog | Every destructive or irreversible action; the confirm button names the action ("Cancel PO-2026-00184"), never "OK" |
| Inline row edit | Only in the quotation-comparison and budget-line grids |
| Wizard | Multi-step creation with a persisted draft (PR → RFQ → PO chain) |

---

## 3. The DataTable

One component, used everywhere. It is the ERP.

Features: server-side sort/filter/paginate · column show/hide/reorder/resize (persisted) · density
toggle (comfortable / compact / dense) · row selection with a bulk-action bar · sticky header and
first column · keyboard navigation (arrows, Enter opens, Space selects, `/` focuses search) ·
**saved views** (named filter+column+sort sets, personal or shared) · export of the *current* view
to XLSX/CSV · empty, loading, and error states that look different from each other · a "247 rows
match, 50 shown" footer that is always honest.

Numeric columns are right-aligned and tabular-figure aligned. Dates render in the site's timezone
with the raw UTC value on hover. Status is a dot + label, never colour alone (§3 accessibility).

---

## 4. Design system

### Tokens

Tailwind theme extension mapped to CSS variables, so a light/dark toggle is one attribute swap.

| Token | Light | Purpose |
|---|---|---|
| `--bg` | `0 0% 100%` | Page |
| `--surface` | `210 20% 98%` | Panels, table header |
| `--border` | `214 20% 89%` | Hairlines — 1px, everywhere |
| `--fg` | `222 30% 12%` | Primary text |
| `--fg-muted` | `215 16% 45%` | Labels, metadata |
| `--primary` | `221 70% 42%` | Actions, links |
| `--success` / `--warning` / `--danger` / `--info` | — | Status only, never decoration |

Type scale: 12 / 13 / 14 / 16 / 20 / 24 px. **14 px is the body default** — ERP screens are read,
not admired. Inter (UI) + JetBrains Mono (codes, amounts in ledgers). Spacing on a 4 px grid.
Radius 6 px. Elevation used only for genuinely floating surfaces (dropdown, drawer, dialog) — three
shadow levels, no more.

### What is deliberately absent (§3, §36)

No gradients. No decorative illustration. No card-wrapping of things that are lists. No animation
beyond 150 ms state transitions and the drawer/dialog enter-exit. No hero sections. No dashboard
tile bigger than it needs to be. Whitespace is tuned for density: a 1440 px screen should show
~25 table rows without scrolling.

### Components (shadcn/ui base + project layer)

`Button` `Input` `Select` `Combobox` `DatePicker` `DateRangePicker` `Checkbox` `RadioGroup`
`Switch` `Textarea` `Tabs` `Dialog` `Drawer` `DropdownMenu` `Tooltip` `Toast` `Badge` `Avatar`
`Skeleton` `Separator` `Breadcrumb` `Pagination` `Command`
· project layer: `DataTable` `FilterBar` `SavedViews` `PageHeader` `DetailLayout` `FieldGrid`
`StatusBadge` `ApprovalTrail` `AuditTrail` `AttachmentList` `MoneyInput` `QuantityInput`
`EntityPicker` `AmountDisplay` `EmptyState` `ErrorState` `ConfirmDialog` `PermissionGate`

`MoneyInput` and `QuantityInput` are not cosmetic: they bind to strings, never JS numbers, so
`0.1 + 0.2` can never appear in a financial form, and they carry the unit/currency with the value.

### Accessibility

WCAG 2.2 AA as a build gate: contrast >= 4.5:1, every interactive element reachable and visibly
focused, dialogs focus-trapped with restore, tables with proper `<caption>`/scope, live regions for
toasts and async results, no colour-only meaning, full keyboard paths for every workflow that a
power user repeats. Tested with `axe-core` in component tests and in the Playwright suite.

---

## 5. Dashboards (§30)

Role-specific, each a single screen with no scrolling on a 1440x900 display:

| Dashboard | Content |
|---|---|
| **Executive** | Project portfolio table (budget, committed, actual, variance, % complete) · procurement spend MTD/YTD · payables ageing buckets · material expenditure trend · headcount · alert list (budget overruns, overdue approvals, flagged deliveries) |
| **Procurement** | Pending PRs / RFQs awaiting quotes / POs awaiting approval as actionable queues · spend by category · top vendors · material price trend · quotation response rate |
| **Project** | Budget vs committed vs actual by phase · deliveries this week · inventory at site stores · open POs · contractor status · approval queue for this project |
| **Site** | Today's deliveries, approved / pending / rejected counts · tonnage · flagged list with one-tap review · stock on hand · low-stock warnings |
| **Material delivery (§23)** | Today's totals; charts by project, material, vendor; daily tonnage; cost trend; flagged deliveries |

Every tile is a link to the filtered list behind it. A number you cannot click through to its rows
is a number no one will trust.

**Charts:** Recharts. The palette, chart-type selection and accessibility rules are governed by the
project's data-visualisation standard, which is applied at implementation time rather than guessed
here — the rule is one consistent categorical palette, no 3D, no pie charts beyond 5 slices, direct
labelling over legends where space allows, and every chart paired with the table it summarises.

---

## 6. Mobile-first ERP surfaces

The full ERP is desktop-first, but three surfaces must work on a phone browser: the **approval
inbox**, the **flagged-delivery review**, and **dashboards (read-only)**. These use the same
components with a responsive variant: the DataTable degrades to a card list below `md`, and the
detail layout stacks. Everything else is explicitly desktop-only and says so rather than rendering
a broken grid.

---

## 7. Frontend testing

| Level | Tool | Scope |
|---|---|---|
| Component | Vitest + Testing Library | Every project-layer component; DataTable behaviours; `PermissionGate` |
| Form validation | Vitest | Each Zod schema against valid/invalid fixtures, including server-error mapping |
| API integration | MSW | Query hooks, error and empty paths, pagination, retry |
| Accessibility | `jest-axe` | Every screen-level component |
| E2E | Playwright | The 18 workflows in §42, against a real backend + seeded database |
| Visual | Playwright screenshots on key screens | Guards density and layout regressions |
