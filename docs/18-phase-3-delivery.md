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
| 3c | Deliveries: ingest, geofence, quantity checks, rate and conversion snapshots, flags | 3a, 3b, unit conversion (Phase 1) | done |
| 3d | Review queue and decisions, corrections, flag waivers, attaching an order | 3c | done |
| 3e | GRN and the inventory ledger (balances, weighted-average cost, reversal, nightly reconcile) | 3c, 3d | done |
| 3f | Mobile sync API (push / pull, idempotency, devices) — the **server** side | 3c | done (the Expo app that calls it is not built) |
| 3h | Stock issues, transfers and adjustments (more writers to the same ledger) | 3e | done |
| 3i | The rest of the server-side Phase 3 list: reviewer notifications, GRN PDF, counter purchases, photos and documents, delivery dashboard, rate overview | 3c–3h | done |
| 3j | The phone: the offline outbox, the sync engine and the app (`mobile/`), plus the `my_deliveries` pull feed | 3f | built; **not yet run on a device** |
| 3g | Web: delivery, review, GRN, inventory screens | 3c–3e | done (the delivery dashboard tiles, §23, and the vendor-rate history grid remain) |

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

## 3c — Deliveries

**One entry point** (`deliveries/services/ingest.py`) serves the web form and, later,
the mobile sync API, so the two can never disagree about what a delivery is or how it
is checked.

| Rule (docs/05 §3, docs/06 §4) | How it is honoured |
|---|---|
| **A delivery is always saved.** | Only a malformed request, or one naming something that does not exist, is refused — with a field error. Everything else raises a *flag*. Even a suspended vendor, a missing rate, a truck on the wrong side of town. |
| **The device never supplies a price, factor or rule.** | `DeliveryCreate` has no rate, amount, conversion or flag field. The rate is resolved from the vendor's approved rates *as of the capture time* (3b), the unit conversion from the conversion table, and both are **snapshotted** on the line. A later rate or factor change cannot restate a delivery already counted. |
| **Idempotent.** | The client-generated `id` is the key. The same id again returns the stored delivery (HTTP 200, same number, nothing changed — even if the second payload differs). Someone else's id is a 409, not a read. |
| **Missing rate / factor does not stop it.** | `RATE_MISSING` / `CONVERSION_MISSING`: captured now, priced later. |
| **Flags carry the rule that fired.** | `rule_id` plus a snapshot of the rule's value, scope and dates; tightening a rule next month never rewrites why last month's load was flagged. |
| **Geofence uses PostGIS.** | Distance is 0 inside a polygon boundary, else metres beyond the centre's radius. A rule scoped to the site beats the site's own radius. Within the GPS accuracy is an INFO note, not an accusation; a fix worse than 100 m can never be CRITICAL by itself; no fix at all is a WARNING. |
| **Clock skew is not lateness.** | Skew is the device's clock against the server's (reported with the batch); how long a delivery waited offline is `LATE_SUBMISSION`. A capture time in the future is skew whatever the batch said. |
| **Tonnage** | Compared in tonnes after conversion (skipped for loads not counted by weight). The rule store decides; with no rule the truck type's own default applies. +25 % or more over is CRITICAL. |
| **PO balance** | Received to date (from deliveries that still stand, converted to the order line's unit) plus this load must fit the ordered quantity plus the tolerance — the *greater* of a percentage and an absolute amount. |
| **No order (docs/12 Q3)** | `NO_PO` WARNING and into review, never refused — unless the project sets `require_po_for_delivery`, which makes it a hard 422. |
| Also | `DUPLICATE_SUSPECT` (same truck — letters and digits only — and material at the site within the window), `DAILY_CAP_EXCEEDED`, `VENDOR_INACTIVE`, `LATE_SUBMISSION`. |

Status: no flag of WARNING or worse → `SUBMITTED`; otherwise `UNDER_REVIEW`. Site staff
see only their site (out of scope reads as "no such site", not "forbidden").

## 3d — Review

| Piece | Behaviour |
|---|---|
| Queue | `GET /deliveries/review-queue`: `UNDER_REVIEW`, most severe open flag first, oldest first within a severity. |
| Approve | Accepts every open flag (kept on the record with the reviewer's note). **A critical flag cannot be accepted without a reason.** |
| Reject | Needs a reason; flags become REJECTED; the delivery stays, marked rejected, and stops counting against its order. |
| Send back | Returns to the capturer with a note. |
| Correct | The capturer's `PUT` puts the corrected entry through the **same pipeline** as a new one — a correction cannot edit a flag away unseen: old flags are kept as `CORRECTED`, the new version raises its own. Same delivery number. |
| Reopen / waive | Administrator-only by default (the roles do not carry `deliveries.reopen` / `waive_flag`). Reopen needs a reason and is refused once a GRN exists. Waiving the last open flag releases the delivery from review. |
| Attach an order | For a delivery that arrived without one: checks the vendor and that the order is receivable, **re-runs the balance check** (attaching cannot hide an over-delivery), retires the `NO_PO` flag. |
| History | `delivery_reviews` is append-only by trigger; every decision, who, when, from-status, to-status, note. |
| Screens | Deliveries list (filters, flag column), record form (browser geolocation button; you never enter a price), detail (flags with the rule in force, load with priced quantity, review history, decision dialogs), review queue with time waiting. |

## 3e — Goods received and the stock ledger

**Posting a GRN is the moment stock moves** (docs/02 §6) — never before.

| Piece | Behaviour |
|---|---|
| Raise | From an *approved* delivery, once (a partial unique index allows one live GRN per delivery). Lines start fully accepted; the price is the delivery line's snapshot. The site's default receiving warehouse is used unless one is named (it must belong to the delivery's site). |
| Inspect | Per line: how much is accepted; the rest is rejected and **must say why** (a database check enforces `accepted + rejected = delivered` and that a rejection has a reason). The line is priced by what is accepted. Inspection result (passed / partial / failed) follows. |
| Price | A load captured without a rate can be repriced once one exists, resolved as of the *capture* date; the price is written back to the delivery line too, so the two never disagree. **An unpriced line cannot be posted**: stock is valued at what it cost, and a shelf of goods at nothing would drag the average down. |
| Post | For each accepted line: converts to the material's base unit, writes a `GRN_IN` ledger row at cost per base unit, tells the order line what arrived (received *and* accepted, in the order line's own unit), marks the delivery `RECEIVED` / `PARTIALLY_RECEIVED`, and moves the order to `PARTIALLY_RECEIVED` / `RECEIVED` as its lines fill. One transaction. |
| Cancel | Draft: releases the delivery. Posted: **contra rows**, at the *original row's cost* (not today's average), in reverse order; the order quantities are given back and the delivery returns to approved so it can be received again. **Refused when the stock has since been used** — nothing is left negative and nothing changes. |
| Ledger | `inventory_transactions` is append-only by trigger. Every stock change in the system goes through one function (`ledger.post`): lock the balance row → refuse to go below zero → weighted-average cost → append the row with the balance *after* → update the cached balance. The database also enforces "moves one way" and "on hand ≥ 0". |
| Costing | Moving weighted average (docs/02 §7). A receipt blends its cost in; an issue leaves at the current average and never changes it; the last unit out takes whatever value remains, so rounding cannot strand a paisa in an empty store. |
| Balances | `inventory_balances` is a *cached projection*, scoped like everything else (project / site copied from the warehouse). Valuation (average cost, value) is hidden from readers without `inventory.view_valuation`; the site manager sees quantities only. |
| Low stock | A `REORDER_LEVEL` rule scoped by material / category / site wins over the material's own reorder level. |
| Reconcile | `inventory.reconcile_balances` (02:00) proves every balance equals the sum of the ledger (quantity *and* value), including a ledger with no balance row at all; drift raises an urgent alarm to the super administrators. |
| Screens | GRN list and detail (inspection drawer, price-now, post, cancel), *Receive into stock* on an approved delivery, stock balances (low-stock marker), the ledger. |

**Deliberately not in this slice** (and why):
- **Stock issues, transfers and adjustments** — built in slice 3h below, as further writers to the *same* ledger through the *same* `ledger.post`, so they changed nothing above.
- **GRN PDF** and **counter purchases** (a GRN with no delivery) — built in slice 3i below.
- **General-ledger posting** on GRN approval and the `PENDING_APPROVAL` / `APPROVED` GRN states — Phase 4, with finance.

## 3f — Mobile sync API

The phone is offline most of the day; this is how what it captured reaches the
server and how it learns what changed. It is a **thin wrapper over the same
`deliveries.ingest` the web form uses** (`ingest.input_from` was moved out of the
web route so both build the identical input), so a delivery is checked and priced
the same way whichever door it came through.

### `POST /sync/push`

Up to 50 operations per call, each answered on its own — **never all-or-nothing**:
one bad entry must not hold forty good ones behind it on a 2G connection.

| Outcome | Meaning | The app should |
|---|---|---|
| `applied` | Recorded now; the result carries the server's view (number, status, flags, and — only for people who may see prices — rate and amount) | mark it synced |
| `duplicate` | This entry's id was already recorded; the stored record is replayed | treat as success |
| `conflict` | A correction arrived for an entry head office already decided; **the server's version stands** | show the server's record, drop the edit |
| `rejected` | Permanent: invalid payload (with the fields), unknown vendor, a site the person is not assigned to, a rule (`purchase_order_required`, `site_inactive`) | fix or drop; do not retry |
| `deferred` | A transient server problem inside that entry | retry later |

- **Idempotent** on the delivery's client-generated id, so a dropped connection or
  a killed app can push the same batch again safely.
- **Each operation runs in its own savepoint**; the request as a whole succeeds
  whenever the batch was understood. A whole-request error is reserved for things
  that apply to every entry: not signed in, no `deliveries.create`, batch too large,
  **device revoked**.
- **The phone cannot supply what it cannot know.** The payload has no field for a
  rate, a conversion factor, a flag or a rule (unknown fields are rejected), and the
  device id, app version and "waited offline" come from the batch envelope, so an
  entry cannot claim a different device than the one that sent it.
- `client_created_at` becomes the capture time when the payload has none, so an
  entry keeps the moment it was recorded, not the moment it reached the server.

### `GET /sync/pull`

`?since=<server_seq>&entities=…&limit=…&site_id=…&device_id=…`. Returns what
changed after the cursor as one stream ordered by a **single global sequence**, with
a `has_more` flag and a cursor that is a true high-water mark across every entity.

| Entity | Contents | Who gets it |
|---|---|---|
| `materials` | sku, name, base unit, category, **all units usable for it** (base + alternates), purchasable / stockable | anyone who may view materials **or** record deliveries |
| `units`, `truck_types` | codes, names, precision / default max tonnage | same |
| `vendors` | code, name, status, phone | vendors view **or** record deliveries |
| `sites` | code, name, project, **centre and radius** for the on-device geofence pre-check, timezone | only sites the person may record deliveries at |
| `rules` | the rules a phone can pre-check with (tonnage, geofence, clock skew, duplicate window, late submission) — **not** tolerances, price rules or approval limits | those who record deliveries |
| `open_pos` | receivable orders with their lines (material, unit, quantity, received so far) | orders at sites the person may record at |

- **Deletes are tombstones** (`deleted: true`): a soft-deleted row, an inactive
  rule, or an order that stopped being receivable (cancelled, closed, fully
  received) arrives as a tombstone so the phone drops it and never offers what the
  server would refuse.
- **Nothing priced is ever sent** (a test scans the whole payload).
- Entities the person may not sync are named in `not_permitted`, so the app can say
  so instead of showing an empty list.
- **The cursor is `server_seq`**, assigned by a trigger (`krb_bump_server_seq`) on
  insert *and* update of the seven tables above, from one sequence
  (`global_change_seq`). The application never writes it, so a new code path cannot
  forget to. Two child tables bump their parent: a change to a material's alternate
  units re-sends the material; a change to a purchase-order line (including what was
  received against it) re-sends the order.
- **Known limit.** A sequence value is taken when a row is written, not when its
  transaction commits, so a slow transaction can commit a row *behind* a cursor
  another phone already passed. Reference data changes rarely and in short
  transactions; the app closes the gap with a full pull (`since=0`) on first launch
  and once a day.

### Devices

The registry already existed (identity, Phase 1: `user_devices`, with revocation).
Sync reports into it: a push registers a phone it has not seen, and every push and
pull records the version, push token and `last_sync_at`. **A revoked device is
refused on both endpoints** before it can read or write anything. A person can
revoke their own phone (`POST /auth/devices/{id}/revoke`, e.g. when it is lost);
revoking *someone else's* device from head office is not built yet.

**Deliberately not in this slice.** Photos (attachments upload separately; the
delivery screen does not link them yet), and push notifications (the token is
captured; no channel adapter yet).

## 3h — Stock issues, transfers and adjustments

Three more ways stock moves, all through the **same `ledger.post`** as a receipt, so
nothing about the ledger's rules (lock, never below zero, weighted-average cost,
append-only) is repeated or bypassed. A new module, `stock`, owns the documents; the
`inventory` module still owns the ledger.

| Document | Behaviour |
|---|---|
| **Issue** | Raised as a draft, **posted** when the goods leave. Posting writes `ISSUE_OUT` at the store's **current average cost** (an issue never changes the average), recorded on the line — that is what the person or job is charged. Counted in any unit with a conversion; the ledger holds the base unit. Insufficient stock refuses the post and changes nothing. Cancelling a posted issue returns the stock with contra rows at the cost it left at. Issued to: employee / contractor / work order, as a name (those tables do not exist yet). |
| **Transfer** | Two ledger rows per line at two moments. **Dispatch** takes the stock out of the source at its average cost, records that cost on the line, and marks the quantity **in transit** at the destination (a marker on the balance: not on hand, not lost, never valued twice). **Receive**, by someone with transfer rights at the *destination*, counts it in **at the cost it left at**, so a transfer moves value and never changes it. A transfer in transit can be cancelled and goes back to the source at its original cost; a received one cannot (the stock may already be in use) — send it back with a new transfer. Visible at both ends. |
| **Adjustment** | Creates or destroys stock without a receipt or an issue behind it, so: a **reason code and note are required**; it **always goes through the approval engine**; and it reaches the ledger **only through its approval** — the handler posts the lines in the same transaction that records the decision, so there is no state in which an approved adjustment has not moved stock or a moved one was never approved (a database check: a `POSTED` adjustment names its approval request). Quantities are signed, in the material's base unit. An increase is valued at the current average unless a cost is given (required when nothing is in stock to average); a decrease leaves at the average of the day it is posted. The form shows what the books say is on hand. Stock is re-checked at submission (a draft may be days old) and again at posting. A rejected adjustment can be edited and resubmitted; a pending one can be withdrawn back to a draft before anyone has approved a step. |

**Approval routing (seeded, editable at `/approval-workflows`).** Nothing about an
adjustment approves itself: under 25,000 (value moved, either direction) the **project
manager** signs; from 25,000 up, **finance** signs as well. When the project manager
raised it, it goes up to finance rather than to themselves. The context available to
conditions: `value_abs`, `value_net`, `quantity_abs`, `reason_code`, `is_write_off`,
`line_count`, `warehouse.*`, `project.*`, `site.*`, `requester.*`. Project and finance
managers gained `inventory.approve_adjustment`; site managers can raise but not sign.

**Also added.**
- `GET /inventory/warehouse-options?action=issue|adjust|transfer_from|transfer_to`: the stores a
  person may act on, for the pickers. A site manager holds no warehouse-management right, so
  the existing `/warehouses` list was no use to them. Transfer destinations are every store.
- Screens: issues, transfers, adjustments (list, form, detail); the adjustment page hosts the
  approval chain and decision buttons (`ApprovalSection`, reusable).
- Forms offer *Post now* / *Dispatch now* (on by default). If the draft is saved but the post is
  refused (not enough stock), the person is told the draft exists and why it was not posted.

**Deviations from docs/07, on purpose.** Issues and transfers use `/post` and `/dispatch` +
`/receive` rather than `/approve` (an issue has one permission and no second signer; calling it
"approve" would suggest one). Adjustments are approved through the engine's own endpoints, as
every other approved document is.

**Deliberately not in this slice.** Part-receipt of a transfer (a short delivery between stores
is recorded by receiving and then adjusting, with a reason); issues to a phase or cost centre
(the free-text target is what is on the slip today; cost centres arrive with finance); returns
to stock (`RETURN_IN` / `RETURN_OUT` exist as ledger types with no document); attaching files to
an adjustment (the attachment entity type exists, the screen does not link it).

## 3i — Notifications, printing, counter purchases, photos, dashboards

Everything below is additive: it reads what earlier slices wrote and adds surfaces on top.

| Piece | Behaviour |
|---|---|
| **Reviewer notifications** | When a delivery is saved and needs a person's attention (`needs_review`), the people who hold `deliveries.review` at that site or project are told, with what the flag is (a critical one is high priority). Not the person who captured it, and **not the super administrator**: a global grant covers everything and would hear of every delivery everywhere. A corrected entry that still needs a look is announced again. Delivered by the outbox after commit, so nobody is told of a change that rolled back. |
| **Decisions reach the capturer** | Approved, rejected and sent-back-for-correction each notify the person who captured the delivery, with the reviewer's note (a rejection is high priority). Deciding your own entry does not notify you. |
| **GRN PDF** | `GET /grns/{id}/pdf`, built from the same read model the screen uses, so valuation is withheld from anyone the screen withholds it from. A draft carries a "no stock has moved" banner and a cancelled note a "cancelled" one, so a printout cannot pass for the record of stock that moved. Every typed value is escaped. |
| **Counter purchases** | A GRN with no delivery, for stock bought over the counter. The riskiest way stock arrives, so it asks for what makes it checkable: a **bill or receipt number** (entered once per vendor, freed if the note is cancelled), **a rate per line from that bill**, an active vendor, a stocked material, a store the person may act on, and — if a `PURCHASE_LIMIT` rule applies to their role (`doc_type = counter_purchase`) — a total within it. Nothing is limited until someone defines the limits. Drafted, inspected and **posted by someone with the right to post**, like any GRN; inspection prices what is accepted by the bill's rate. A database check says a GRN has a delivery or a bill reference, never neither. |
| **Photos and documents** | A reusable attachments block on the delivery (photo, challan), GRN (bill, photo) and adjustment (evidence) pages: pick a file, it goes **straight to storage** on a short-lived address, then the API checks what actually landed before it counts. Open, and remove with a reason (the file stays in storage as evidence). |
| **Delivery dashboard** | `GET /deliveries/summary` and `/deliveries/dashboard`: totals, status counts, open flags, tonnage and per-unit quantities, value (with `rates.view` only), daily series, top materials and vendors, loads and quantity by site, and what is waiting for review, oldest first. Today is the *company's* day. Rejected and cancelled loads are counted as such but add nothing to a quantity or value. A load with no price yet still counts toward quantities (in the unit it was entered in). **Every tile links to the filtered list behind it**, and every chart has its table beside it. |
| **Rate overview** | `GET /vendor-rates/grid` and `/vendor-rates/grid`: one row per rate now in force, in the scope it applies to, with the periods that have stood as a sparkline (with the same facts in words for a screen reader), the last change, and a "change pending" marker for a proposal not yet in force. Only rates the caller may see. |

### Found by building it

- **Uploads could never have worked from a browser.** Presigned storage URLs were signed
  against the internal address (`http://minio:9000`), which no browser or phone can reach, and a
  presigned signature covers its host, so it cannot be rewritten afterwards. The store now signs
  with a second client pointed at `STORAGE_PUBLIC_BASE_URL` (default `http://localhost:9000` in
  the compose file; from an Android emulator use `10.0.2.2`, from a real phone the LAN address).
  It was invisible to every API test and was found the first time a file was chosen in a browser.
- **A load with no price yet vanished from the dashboard totals**, because converted quantities
  are only written once a rate is found. Found by the dashboard's first look at real data; a test
  now pins it.
- **The dashboard's "today" is the company's.** A test that captured loads "hours ago" failed when
  run just after midnight in Karachi — correct behaviour, so the tests now use minutes.

**Deliberately not in this slice.** Daily caps on counter purchases (a storekeeper could split
bills under the limit; the duplicate-bill check and the reviewer-visible bill number are the
defence today); virus scanning of uploads (magic-byte type checks only); an in-page image preview
(files open in a new tab); expanding a rate-overview row inline to the full trail (a link opens the
list of every period, which carries reasons and approvers).

## 3j — The phone

The Expo app now lives in `mobile/` (the old prototype folder was merged into this repository,
and its stub backend retired: the phone and the web app share **one** backend). It is described
in [`mobile/README.md`](../mobile/README.md); what matters here is the contract and what was
checked.

**A gap closed on the server.** `pull` carried reference data but not the fate of what the phone
sent, so a reviewer's decision, a flag, or "please correct this" would never have reached it. A
`my_deliveries` feed now returns the caller's own deliveries from the last 30 days — status,
open flags, the latest review's action and note, `can_correct`, and the entry as recorded so a
correction can start from it — through the same cursor (`deliveries` gained `server_seq` and
the same trigger). Never a price. Only the caller's own captures.

**The client** (`mobile/src/core`, plain TypeScript behind two interfaces — a SQL database and a
secure store — so the identical code and SQL run on the phone and in Node tests):

| Rule | Where it is enforced |
|---|---|
| Saving writes to the phone and returns; the network is never on that path | `Outbox.captureDelivery` |
| An entry leaves the queue only when the server says it has it; a lost reply, a killed app and a repeated send all end in `duplicate` | `SyncEngine`, `LocalStore.recoverInflight` |
| Oldest first, ≤ 50 entries / 512 KB per request; each entry has its own fate | `fit`, `settle` |
| Backoff 2 s, 5 s, 15 s, 60 s, 5 m, 15 m, 1 h, then hourly, ±20 %; `DEAD` (never discarded) after 72 h | `backoff.ts` |
| One run at a time, however many things ask | `SyncEngine.run` |
| A session that ends parks the queue with no penalty; offline at start-up is not signed out; signing out keeps the queue | `Session`, `requeue` |
| A refused entry stays for the person to fix and resend under the same id; a conflict says "already reviewed" and the server's version stands | `Outbox.fixAndRetry`, `acknowledgeConflict` |
| The form has nowhere to put a price, a factor or a rule | `buildDraft`, `DeliveryDraft` |

**What was checked, honestly.**

- 56 unit tests of the client, including a fake server that can misbehave on demand. As a
  check that they can fail, breaking the fake server's idempotency makes three of them fail.
- The same engine and SQL run against the **real API** (`mobile/src/live`, `npm run test:live`):
  twenty entries captured with no connection land **exactly once**, in order, when the connection
  returns — even when the first reply is lost, and again when everything is sent a second time;
  one entry with a vendor that does not exist is refused without holding up the others; a
  reviewer's "please correct this" comes back with their words and the correction is checked
  afresh; a revoked device is refused with its queue untouched. PostgreSQL confirmed exactly
  twenty rows from one device.
- The whole app type-checks and **bundles for Android** with Metro (742 modules, Hermes bytecode).

**What was not checked.** The screens have never been run on an emulator or a phone, so layout,
touch targets, GPS, permissions and the real radio are untested. There is no Detox / airplane-mode
test and no 72-hour field soak (docs/06 §10). The Phase 3 done-when — "verified end-to-end by
Playwright + Detox" — is therefore met for the server and the sync logic, and **not yet for the
device**.

**Not built:** photo capture and upload from the phone (the server side works; photos will be
their own outbox entries depending on the delivery), the head-office review queue on mobile,
history filters, language, push notifications.

## What is not built in Phase 3 yet

| Item | State |
|---|---|
| Returns to stock; part-receipt of a transfer; issues to a phase / cost centre | not built (see 3h) |
| **Mobile sync API** | **built** (3f). What is not: the app that calls it. |
| **The Expo mobile app** | built (3j) and tested from a computer; **never run on a device**, so Detox / airplane-mode and the 72-hour soak remain. Photos from the phone, the mobile review queue, history filters and language are not built. |

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
- **The worker knew no approval handlers.** The API registers each document type's approval
  handler as a side effect of importing its routers; the Celery worker imports none, so the
  nightly integrity check (`approvals.reconcile`) iterated an empty registry and checked nothing.
  The worker task module now imports the handler-bearing services. (Found while adding the
  adjustment handler; the gap predates it.)
- **A rule that approves by itself would have posted before the approval was recorded** (caught
  while writing the handler, then pinned by a test). A zero-step rule
  calls the handler from inside `approvals.submit`, before the caller has stored the request id,
  which the "posted needs an approval" check would refuse. The handler now records its own request
  id. A test publishes an always-approve workflow and submits an adjustment through it.

## Testing (state after slice 3i)

| Suite | Result |
|---|---|
| Backend | 713 passed, 0 failed, coverage 92 % (gate 80 %); ruff, mypy --strict (263 files), import contracts 4/4, `alembic check` clean; every migration round-trips |
| New in 3e–3i | GRN and ledger (22 + costing unit tests) · mobile sync (23) · stock movements (34) · delivery notifications (6) · GRN print (4) and counter purchases (6) · delivery dashboard (10) · rate overview (3) · presigned-URL addressing (3) |
| Web | 68 unit; typecheck, eslint (no warnings), prettier, build clean (the charts library is its own chunk, loaded only by the dashboard) |
| Browser E2E | 23 passed, including stock movements, **a counter purchase from a bill with a photo uploaded straight to storage, the PDF, and posting**, a photo on a delivery, the dashboard (a load recorded through the API appears, and its tile opens the filtered list) and the rate overview |

## Decisions to confirm

- **The ±10 % auto-approval threshold** and "first rate approves itself" are defaults
  chosen to keep routine renegotiation from queueing at finance. Change them at
  `/approval-workflows` if KRB wants every price change reviewed.
- **No approval limits are seeded.** Until someone defines them, nothing is limited.
  A limit on a role applies to that role only.
