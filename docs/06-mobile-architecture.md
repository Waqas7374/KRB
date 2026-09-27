# 06 — Mobile Architecture & Offline Sync (Site Ledger)

Covers §12, §13, §14, §33, §34, §37. Continues the existing Expo prototype, whose two correct
instincts — client-generated UUIDs and server-side rate resolution — become the backbone here.

---

## 1. Stack

| Concern | Choice | Why |
|---|---|---|
| Framework | **Expo SDK 51+, dev-client build** (not Expo Go for production) | Android + iOS from one codebase; the prototype is already Expo. Dev-client because background sync and secure storage need native modules. |
| Language | TypeScript, strict | Shares generated API types with the web app via `packages/api-types`. |
| Navigation | `expo-router` | File-based, deep-linkable (notification → delivery). |
| Local DB | **`expo-sqlite` (SQLCipher-backed where available)** | Durable queue. The alternative — AsyncStorage — loses data under memory pressure. |
| Server state | TanStack Query + a SQLite-backed persister | Same mental model as web; cached reference data survives app restarts. |
| Forms | React Hook Form + **the same Zod schemas as the backend contract** | One definition of "valid delivery". |
| Location | `expo-location` | Foreground fix with accuracy; no background tracking (privacy and battery). |
| Camera | `expo-camera` + `expo-image-manipulator` | Resize/compress before queueing — a 4 MB photo over 2G is not a plan. |
| Secure storage | `expo-secure-store` | Refresh token only. Access token in memory. |
| Background sync | `expo-task-manager` + `expo-background-fetch`, plus `NetInfo`-triggered foreground sync | Belt and braces; background fetch on iOS is best-effort by design. |
| Builds | EAS Build + EAS Update (OTA for JS-only fixes) | Field devices are hard to recall. |

---

## 2. Screens

```
Login  (phone + password)                          §13
  └─ auto-route by role
     ├─ SITE STAFF / SITE MANAGER → Site Home
     └─ HEAD OFFICE roles        → Review Queue

Site Home            today's count, tonnage, sync badge, [+ New delivery] (big)
New Delivery         the 20–30 second screen                          §14, §37
Delivery Detail      status, flags, reviewer notes, correction action
Queue                pending-sync items with state and last error     §34
History              last 30 days at this site, filterable
Review Queue (HO)    flagged deliveries: accept / reject / request correction  §18
Vendor Rates (HO)    read-only on mobile; maintained on web           §19
Profile              site switch, language, sync now, sign out, diagnostics
```

### New Delivery — the field screen

Ordered so the common case is thumb-only and sequential:

1. **Material** — horizontal pill row of the site's top 6 materials (the prototype's pattern),
   `Search…` for the rest.
2. **Truck number** — auto-uppercased, remembers the last 20 plates used at this site as
   one-tap chips. Most trucks repeat; typing a plate is the slowest part of the form.
3. **Vendor** — pre-filled from the material's default vendor at this site; changeable.
4. **PO** — auto-selected when exactly one open PO matches (vendor + material + site); otherwise a
   picker showing ordered vs received balance. Skippable if the company permits PO-less deliveries
   (open decision Q3).
5. **Tonnage** + unit — numeric keypad, unit defaults from the material.
6. **Driver name / phone, challan no.** — collapsed under "More details", optional.
7. **Photo** — optional in v1; one tap, compressed to ~200 KB.
8. **Location** — captured automatically on screen open, with a visible status chip:
   `Locating… → Captured ±8 m → Poor signal ±120 m → Unavailable`.
9. **Save entry** — full-width, always visible above the keyboard.

Design rules: minimum 48 dp touch targets, 16 pt minimum text, high contrast for direct sunlight,
no field requiring two hands, no blocking spinner on save. Save writes locally and returns
immediately; the network is never on the critical path.

---

## 3. Local database

```sql
CREATE TABLE deliveries (            -- local mirror + drafts
  id TEXT PRIMARY KEY,               -- UUIDv7, generated on device
  payload TEXT NOT NULL,             -- JSON
  local_status TEXT NOT NULL,        -- DRAFT | QUEUED | SYNCING | SYNCED | REJECTED | CONFLICT
  server_status TEXT,                -- mirrored from server after sync
  created_at TEXT, updated_at TEXT,
  attempt_count INTEGER DEFAULT 0, last_error TEXT
);

CREATE TABLE outbox (
  op_id TEXT PRIMARY KEY,            -- UUIDv7; the idempotency key
  entity TEXT NOT NULL,              -- 'delivery' | 'attachment' | 'delivery_correction'
  entity_id TEXT NOT NULL,
  op TEXT NOT NULL,                  -- 'create' | 'update' | 'submit'
  payload TEXT NOT NULL,
  depends_on TEXT,                   -- op_id that must succeed first (photo after delivery)
  status TEXT NOT NULL,              -- PENDING | INFLIGHT | DONE | FAILED | DEAD
  attempt_count INTEGER DEFAULT 0, next_attempt_at TEXT, last_error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE reference_data (        -- materials, vendors, sites, POs, units, rules
  entity TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
  server_seq INTEGER NOT NULL, updated_at TEXT, deleted INTEGER DEFAULT 0,
  PRIMARY KEY (entity, id)
);

CREATE TABLE sync_state (entity TEXT PRIMARY KEY, last_server_seq INTEGER, last_synced_at TEXT);
CREATE TABLE pending_media (op_id TEXT PRIMARY KEY, local_uri TEXT, bytes INTEGER, status TEXT);
```

---

## 4. Sync protocol

### Push

```http
POST /api/v1/sync/push
Idempotency-Key: <batch uuid>
{
  "device_id": "…", "app_version": "1.4.2", "network": "2g",
  "device_time": "2026-09-21T09:14:03+05:00",
  "ops": [
    { "op_id": "018f…", "entity": "delivery", "op": "create",
      "client_created_at": "2026-09-21T08:55:11+05:00",
      "payload": { "id": "018f…", "site_id": "…", "vendor_id": "…",
                   "material_id": "…", "quantity": "12.5", "unit_id": "…",
                   "truck_number": "RJ14 GB 4021", "truck_type_id": "…",
                   "captured_at": "2026-09-21T08:55:03+05:00",
                   "lat": 26.9124, "lng": 75.7873, "gps_accuracy_m": 8.2 } }
  ]
}
```

Response — **per-op results, never all-or-nothing**:

```json
{ "server_time": "2026-09-21T09:14:05Z",
  "results": [
    { "op_id": "018f…", "outcome": "applied",
      "record": { "id": "018f…", "delivery_number": "DLV-2026-004412",
                  "status": "UNDER_REVIEW", "rate": "52.000000",
                  "amount": "23400.0000",
                  "flags": [ { "type": "TONNAGE_ANOMALY", "severity": "WARNING",
                               "message": "22.0t exceeds 16.0t maximum for 10-wheeler" } ] } },
    { "op_id": "018e…", "outcome": "duplicate", "record": { … } },
    { "op_id": "018d…", "outcome": "rejected", "error": {
        "code": "site_not_permitted", "message": "You are not assigned to this site",
        "retryable": false } }
  ] }
```

Outcomes: `applied` · `duplicate` (already present — treated as success, record replayed) ·
`conflict` (server state moved on; server record returned and wins) · `rejected` (permanent;
op moves to DEAD and surfaces in the queue for the user) · `deferred` (transient server issue; retry).

Server processing per op: authorise site → resolve **rate** and **conversion factor** server-side
from `captured_at` → compute geofence distance → run the rule set → persist delivery + items +
flags + audit + outbox event. All inside one transaction per op, so a poisoned op cannot take the
batch down.

### Pull

```http
GET /api/v1/sync/pull?entities=materials,vendors,sites,open_pos,units,truck_types,rules,my_deliveries
                     &since=418823&site_id=…
```

Returns changed rows since the cursor plus a new `server_seq`, page by page. Every syncable table
carries `server_seq bigint DEFAULT nextval('global_change_seq')` bumped on write, so the cursor is
monotonic and a missed page is never silently skipped. Deletions arrive as tombstones.

`my_deliveries` is the fate of what *this* phone sent: the caller's own captures from the last 30
days with status, open flags, the latest review's action and note, and `can_correct` (a reviewer
asked for a correction). It is how "head office sent this back" reaches the phone. No prices.

Reference data is refreshed on login, on app foreground when older than 30 minutes, and after every
successful push.

### Media

Photos upload separately: `POST /api/v1/attachments/presign` → direct PUT to object storage →
`POST /api/v1/attachments/confirm` with `op_id`. The delivery is never held hostage to its photo;
an unsent photo is its own outbox row with `depends_on` set to the delivery's op.

---

## 5. Retry, ordering and failure

- **Backoff:** 2s, 5s, 15s, 60s, 5m, 15m, 1h, then hourly, with ±20% jitter. Capped at 72 hours,
  after which the op goes `DEAD` and the user sees it in the queue with a "Contact head office"
  action. It is never discarded (§34).
- **Ordering:** ops are pushed oldest-first, and `depends_on` is honoured. Batches are capped at 50
  ops / 512 KB so a 2G connection can actually finish one.
- **Single-flight:** a mutex prevents concurrent syncs when foreground and background trigger
  together.
- **Triggers:** app foreground · connectivity regained (NetInfo) · after each save · manual "Sync
  now" · background fetch (~15 min, opportunistic).
- **Auth during sync:** a 401 refreshes the token once and retries the batch; a failed refresh
  parks the queue and prompts re-login **without losing anything** — the queue is independent of
  the session.

## 6. Conflict policy

Deliveries are **insert-only from the device**, which eliminates the hard conflicts:

| Case | Resolution |
|---|---|
| Same op pushed twice | `duplicate`; stored response replayed (idempotency key = record UUID) |
| Device edits a delivery already reviewed at head office | `conflict`; **server wins**, local row updated to server state, user notified "This entry was already reviewed" |
| Head office requests a correction | Arrives via pull; the delivery reappears in the device's queue as editable with the reviewer's note; the correction is a new op |
| Reference data changed after the draft was made (e.g. vendor deactivated) | Server rejects with a clear code; the row stays in the queue for the user to fix, not silently dropped |
| Device clock wrong | Both `captured_at` and server `received_at` stored; skew computed and flagged, never "corrected" |

**Rates and conversion factors are never sent by the device**, so they can never conflict. This is
the prototype's rule, kept.

---

## 7. Security on device

- Refresh token in `expo-secure-store` (Keychain / Keystore); access token in memory only.
- SQLite encrypted at rest where the platform supports it; the local DB holds no salary or
  financial master data — only the site's operational reference set.
- Sign-out wipes tokens and reference data but **retains the outbox**, which is re-attached to the
  same user on next login. Signing out must not destroy unsynced work.
- Device registration on first login; head office can revoke a device, which invalidates its
  refresh chain on next contact.
- A jailbreak/root signal is recorded on the delivery as provenance, not used to block.

---

## 8. Offline UX contract

At all times the user can see:

| Indicator | States |
|---|---|
| Connectivity | Online · Offline · Weak |
| Queue | `All synced` · `3 pending` · `1 needs attention` |
| Per-entry | `Saved on device` · `Syncing…` · `Synced` · `Under review` · `Approved` · `Rejected` · `Needs correction` |

The save confirmation never claims more than is true: offline it says **"Saved on device — will
sync when you're online"**, online it says **"Delivery DLV-2026-004412 recorded"**, and when the
server flagged it, it says so immediately with the reason.

---

## 9. Extension points designed in (§37)

| Future feature | Already provided for |
|---|---|
| Challan OCR | attachment pipeline + `challan_number` field; OCR becomes a server-side worker that proposes values |
| QR / barcode | `truck_number` and `material` accept a scanned payload; no schema change |
| Digital signature | `attachments` with `document_type = 'signature'` on the delivery |
| GPS attendance | `attendance_records` already has lat/lng/source columns |
| Push notifications | `user_devices.push_token` captured from v1; only the channel adapter is missing |
| Weighbridge integration | `deliveries.quantity_source` enum (MANUAL/WEIGHBRIDGE/OCR) reserved |

---

## 10. Testing

- Unit: outbox DAO, backoff schedule, dependency ordering.
- Integration (Jest + an in-memory SQLite): capture 20 deliveries offline, restore connectivity,
  assert exactly 20 server rows, no duplicates, correct order.
- Integration: kill the app mid-push; on restart assert no op is lost and no op is applied twice.
- Integration: server returns 409 for an op; assert the record enters CONFLICT and reflects server
  state.
- Detox (or Maestro) e2e on Android: login → new delivery offline → queue shows 1 pending → network
  on → synced → flagged badge appears.
- A **72-hour field soak** on a real device with airplane-mode cycling before any site rollout.
