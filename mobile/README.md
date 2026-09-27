# Site Ledger — the mobile app

The site-operations app: record truck deliveries at a site, **offline**, and let them sync. It
lives in this repository and talks to **the one ERP backend** (`../backend`) — the same API the
web app uses. There is no separate mobile backend.

- What the app must do, and how sync works: [docs/06 — Mobile architecture](../docs/06-mobile-architecture.md)
- What the server provides for it (built and tested): `POST /sync/push`, `GET /sync/pull`, and
  the ordinary auth endpoints — see *3f* and *3j* in [docs/18](../docs/18-phase-3-delivery.md).

## What is built, and how far it has been checked

| Piece | State |
|---|---|
| **Offline outbox and sync engine** (`src/core`) | Built and tested. Saving writes to the phone and returns; entries are sent oldest first in batches of ≤ 50 / 512 KB, each with its own outcome; retried with backoff (2 s … 1 h, then hourly, ±20 %); parked — never emptied — when the session ends; `DEAD` (waiting for a person, never discarded) after 72 h; one run at a time. |
| **Local database** | One `LocalStore` over a small SQL interface: expo-sqlite on the phone, Node's SQLite in tests, the same SQL in both. |
| **Session** | Login by phone or email, refresh token in the platform secure store, access token in memory; offline at start-up is *not* signed out; signing out keeps the queue. |
| **Pull** | Reference data (materials with their units, vendors, truck types, sites with geofence, rules, open orders) with tombstones and a cursor; and the fate of the phone's *own* deliveries (status, flags, the reviewer's note, "please correct this"). |
| **Screens** | Login, Site Home (today's count, sync badge), New Delivery (material pills, plate chips, vendor, order picker with balance, quantity/unit, location chip, on-device advice), Delivery Detail (status, flags, reviewer's note, correct / fix / acknowledge), Queue. |
| **Advice on the device** | Distance from the site and weight against the truck type, as warnings only. The server re-checks everything; a delivery is always saved. |

**Checked, from a computer:** 56 unit tests of the client logic in Node; the same engine and SQL
run against the real API in `src/live` (twenty offline captures land exactly once even when a
reply is lost; a refused entry does not hold up the rest; a reviewer's "please correct" comes
back and the correction is checked afresh; a revoked device is refused with its queue intact);
the whole app type-checks and **bundles for Android** with Metro.

**Not checked, and not claimed:** the screens have never been run on an emulator or a phone, so
layout, touch behaviour, GPS, permissions and the real radio are untested; there is no Detox /
airplane-mode test; the 72-hour field soak (docs/06 §10) has not been done.

**Not built yet:** photo capture and upload (the delivery is never held hostage to its photo;
photos will be their own outbox entries that depend on the delivery — the server side, presign
→ upload → confirm, exists and works); the head-office review queue on mobile; history filters;
language switch; push notifications (the token is captured, there is no channel yet).

## Run it (Windows)

Prerequisites: Node.js LTS, Android Studio (for the emulator), Docker Desktop (for the backend).

- **Android emulator:** Android Studio → More Actions → Virtual Device Manager → Create Device
  (a mid-range profile such as Pixel 6, system image API 34) and boot it once.
- **iPhone:** there is no iOS simulator on Windows. Use the free *Expo Go* app on a real iPhone.

```bash
cd mobile
npm install
npx expo start      # press "a" for the Android emulator, or scan the QR code with Expo Go
```

## Point it at the backend

Start the stack from the repository root (`make up`; see the root README). The API is then at
`http://localhost:8000/api/v1`. Tell the app where it is with `EXPO_PUBLIC_API_URL`:

| Where the app runs | `EXPO_PUBLIC_API_URL` |
|---|---|
| Android emulator | `http://10.0.2.2:8000/api/v1` (the default; the emulator cannot see your machine's `localhost`) |
| Real phone on the same Wi-Fi | `http://<your PC's LAN address>:8000/api/v1` |

Uploads and downloads go to storage on a separate address the API hands out
(`STORAGE_PUBLIC_BASE_URL`, see `.env.example`); a phone needs that to be reachable too.

Sign in with a seeded user, for example `staff.gvh1@krb.example` (site staff, GVH-S1). The
development password is printed by `python -m app.seeds` and listed in `dev-credentials.csv`
(which is deliberately not committed).

## Test it

```bash
npm run typecheck   # tsc --noEmit
npm test            # 56 client tests, no network needed (the live test skips itself)
npm run test:live   # the real-server scenario: needs `docker compose up -d` and the seeded users
```
