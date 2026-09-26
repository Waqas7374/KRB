# Site Ledger — the mobile app

The site-operations app: record truck deliveries at a site, offline, and let them sync.
It lives in this repository and talks to **the one ERP backend** (`../backend`) — the same
API the web app uses. There is no separate mobile backend.

- What the app must do, and how sync works: [docs/06 — Mobile architecture](../docs/06-mobile-architecture.md)
- What the server already provides for it (built and tested): `POST /sync/push`,
  `GET /sync/pull`, and the ordinary auth endpoints — see *3f* in
  [docs/18](../docs/18-phase-3-delivery.md).

## What is implemented

Be clear-eyed: this is still the original scaffold, carried over unchanged.

- Login screen: UI only, no real auth call.
- Truck entry screen: UI and location capture work; there is no local SQLite queue and no
  background sync.

The sync engine, outbox, rate-free capture form, queue screen and photo capture described in
docs/06 are **not built**, and nothing here has been verified on a device.

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
`http://localhost:8000/api/v1`.

| Where the app runs | API base URL |
|---|---|
| Android emulator | `http://10.0.2.2:8000/api/v1` (the emulator cannot see your machine's `localhost`) |
| Real phone on the same Wi-Fi | `http://<your PC's LAN address>:8000/api/v1` |

Sign in with any seeded user (for example `staff.gvh1@krb.example`, password in the root README).
