# 15 — Phase 1 Delivery: Frontend

Completed 2026-09-24. Companion to [14-phase-1-delivery](14-phase-1-delivery.md)
(backend). With this, **Phase 1 is done by the roadmap's own criterion**: a
vendor created in the browser lands in PostgreSQL with an audit entry, an
Auditor can read it and cannot write it — verified by Playwright against the
running stack, not by hand (`e2e/tests/phase1-done-when.spec.ts`).

---

## What was built

| Area | Screens / pieces | Key files |
|---|---|---|
| Auth | Sign-in (email or phone), forced first-login password change, voluntary change, forgot / reset via emailed link, session restore on reload, sign-out, "My account" (active sessions with remote sign-out, notification preferences) | `web/src/features/auth/` |
| Shell | Collapsible left nav gated by permission, header with company, read-only badge, notification bell (60 s poll), user menu with theme switch, route-level error boundary, lazy-loaded routes | `web/src/app/` |
| DataTable | Server-side sort (only on fields the API allows) and paging, URL-owned state, column show/hide + density (persisted), keyboard row navigation (↑/↓/Enter), `/` focuses search, distinct loading/empty/error/forbidden states, honest "x–y of N" footer, CSV export of the current filtered view (up to 5 000 rows, formula-injection safe) | `web/src/components/data-table/` |
| FilterBar & saved views | Debounced search + enum/lookup filters bound to the URL; personal saved views | `filter-bar.tsx` |
| Organisation | Projects (list, create/edit, detail with phases edit, status lifecycle with reasons, sites); Sites (list, create/edit, detail with geofence change and warehouses); Departments; Cost centres | `features/projects`, `features/sites`, `features/masterdata/ReferencePages.tsx` |
| Vendors | List, create (with contacts, NTN/CNIC rule mirrored), edit (sends only changed fields with `If-Match`), detail with approve/reinstate/suspend/blacklist (reason required) and bank accounts (add, verify; hidden without `vendors.manage_bank_details`) | `features/vendors/` |
| Master data | Materials (list/create/edit/detail/alternate units/deactivate), categories, units, truck types, warehouses; unit conversions with history and the resolve simulator (§5.7); weighbridge calibration (§N-6: log readings, review spread, tick readings, confirm factor) | `features/materials/`, `features/masterdata/` |
| Admin | Users (list, invite, edit, deactivate, reset password, role grants with scope pickers, revoke with reason); Roles (list, create, permission matrix grouped by module, locked roles read-only) | `features/users/`, `features/roles/` |

Design system tokens are exposed as Tailwind colours (`bg-surface`,
`text-fg-muted`…) via `@theme inline`, so light/dark is one attribute. The
initial shell is ≈140 KB gzipped (budget 200 KB). Types come from the live
OpenAPI schema (`npm run api-types` → `src/types/api.d.ts`), aliased in
`src/types/models.ts` so a renamed field fails the type check.

## Bugs found and fixed

All found by driving the real stack from a browser or by reading the code the
browser depends on. Each has a regression test.

**Backend**

1. **Responses were sent before the transaction committed.** FastAPI runs a
   `yield` dependency's exit code *after* the response by default; our unit of
   work commits there. A login followed at once by `/auth/me` failed ~1 in 15
   with "session revoked" (the session row did not exist yet), and any write
   could report success and then fail to commit. Fixed with
   `Depends(get_uow, scope="function")` (`app/api/deps.py`); pinned by
   `tests/unit/test_deps.py`, since the in-process test client cannot observe
   the race. Probe: 2/30 failures before, 0/40 after.
2. **Invitation and password-reset links were never delivered** — only
   written to a debug log, so an invited user could never sign in outside
   development and "forgot password" did nothing. Now emitted as
   `auth.password_link_issued` through the outbox; the worker emails the link
   and **scrubs the clear token from the stored event** in the same
   transaction. Invites last `INVITE_LINK_TTL_HOURS` (72 h), resets stay 30 min.
   E2E proves the round trip through Mailpit.
3. **Audit rows never recorded who acted by name or role.** `actor_name` and
   `actor_roles` existed but nothing populated them; now snapshotted from the
   request context on every automatic and explicit audit row.
4. **Changing your password signed you out too**, contradicting the API's own
   "signed out of all other devices" — which would bounce every new user to
   the login screen at the end of the forced first-login change. The calling
   session is now kept.
5. **Notification preferences returned only explicit overrides**, so a
   client could not render the settings screen without copying the type
   enum. Now returns the full type × channel matrix with defaults.

**Frontend / infrastructure**

6. **The production build could not make any API call**: it is built with a
   relative `VITE_API_BASE_URL=/api/v1`, and `new URL()` without a base
   throws. Pre-existing since Phase 0; hidden because dev uses an absolute URL.
7. **nginx dropped every security header on `index.html`** (a location with
   its own `add_header` discards server-level ones) and there was no CSP. Both
   fixed; `nginx -t` passes.
8. **Vite in Docker served stale code** after edits on a Windows host (bind
   mounts emit no file events inside the container). Polling is enabled for
   the container only (`VITE_WATCH_POLLING=1`).

## Testing

| Suite | Result |
|---|---|
| Backend `pytest` | 321 passed, coverage 80.4 % (gate 80 %); ruff, mypy --strict, lint-imports (4/4) clean |
| Web unit (Vitest) | 49 passed (was 22) — API client incl. relative base URL, incl. single-flight refresh, CSV safety, form helpers, DataTable states/sort/keyboard, PermissionGate |
| Browser E2E (Playwright, `make e2e`) | 8 passed — Phase 1 done-when, approval + suspension flow, wrong password, session restore + sign-out, site-staff gating, forgot-password via Mailpit, every screen renders, list → detail by keyboard |

E2E runs against the live stack and resets seeded passwords first with
`python -m app.seeds --reset-dev-passwords` (refused in production).

## Open items and decisions for the user

- **`must_change_password` is enforced only by the web UI.** The API accepts
  every call from such an account. Recommend a server-side guard (allow only
  `/auth/me`, `/auth/password/change`, `/auth/logout`) before go-live.
- **Refresh token storage.** Kept in `localStorage` because the API takes it in
  the request body; mitigated by rotation with reuse detection and the new CSP.
  An httpOnly cookie is the stronger option and needs an API change — decision
  wanted before the pilot.
- **Saved views are per browser.** Shared/team views need a server table.
- **Deferred to their natural phase**: project switcher and ⌘K global search
  (no search endpoint yet), SSE for the bell (Phase 6), attachment UI (lands
  with PO/GRN screens), `jest-axe` checks.
- **Pickers load at most 200 rows** per lookup. Fine for seeded data; an async
  search picker is needed once real master data is imported.
- **Phone-only users receive no invite/reset link** until SMS exists; an
  administrator must relay it.
- **E2E is not in CI yet** — it needs the full Docker stack in the runner.
