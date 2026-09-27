import { describe, expect, it } from "vitest";

import type { Fetch } from "../core/api";
import { uuidv7 } from "../core/ids";
import type { DeliveryDraft } from "../core/types";
import { nodeDb } from "../test/node-db";
import { DEVICE, Phone } from "../test/harness";

/**
 * The Phase 3 "done when", for the phone's side of it: twenty deliveries captured with no
 * connection land exactly once on the real server after reconnection, even when a reply is
 * lost, even when the same batch is sent again — and what head office decides comes back down.
 *
 * This runs the app's real sync code (the same LocalStore SQL, the same engine) against the
 * running development stack. It is skipped when that stack is not up:
 *
 *     docker compose up -d && cd mobile && npm run test:live
 *
 * What it cannot prove is the radio: airplane mode on a physical phone (see docs/06 §10).
 */
const BASE = process.env.MOBILE_LIVE_API ?? "http://localhost:8000/api/v1";
const PASSWORD = "KrbDev!Passw0rd";
const STAFF = "staff.gvh1@krb.example";
const MANAGER = "sm.gvh1@krb.example";

async function reachable(): Promise<boolean> {
  try {
    const r = await fetch(`${BASE}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    return r.status > 0;
  } catch {
    return false;
  }
}

/** A network we can cut: while `online` is false nothing gets through. `dropReply` makes the
 *  server do the work and the reply vanish. */
class Wire {
  online = true;
  dropPushReply = false;
  pushes = 0;
  fetch: Fetch = async (input, init) => {
    if (!this.online) throw new TypeError("Network request failed");
    const response = await fetch(input, init);
    if (input.includes("/sync/push")) {
      this.pushes++;
      if (this.dropPushReply) {
        this.dropPushReply = false;
        throw new TypeError("Network request failed"); // the server has recorded them all
      }
    }
    return response;
  };
}

async function asPerson(email: string): Promise<(path: string, init?: RequestInit) => Promise<Response>> {
  const login = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier: email, password: PASSWORD }),
  });
  const { access_token: token } = (await login.json()) as { access_token: string };
  return (path, init) =>
    fetch(`${BASE}${path}`, {
      ...init,
      headers: { ...init?.headers, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    });
}

interface Reference<T> {
  id: string;
  data: T;
}

describe("the phone against the real server", () => {
  it("twenty offline captures land exactly once, and the decisions come back", async (ctx) => {
    if (!(await reachable())) ctx.skip(); // the development stack is not running
    const wire = new Wire();
    const uid = `live-${Date.now()}`;
    // A device of our own, so revoking it at the end touches nothing else.
    const phone = new Phone(wire.fetch, nodeDb(), BASE, { ...DEVICE, device_uid: uid });
    phone.now = new Date(); // a real clock: the server checks the device's against its own
    phone.idFactory = () => uuidv7(); // real ids: this server remembers every run
    await phone.start();
    await phone.session.login(STAFF, PASSWORD);

    // -- Reference data comes down on the first sync ---------------------------------------------------
    const first = await phone.sync();
    expect(first.offline).toBe(false);
    const sites = await phone.store.reference<{ code: string; latitude: number; longitude: number }>("sites");
    const site = sites.find((s: Reference<{ code: string }>) => s.data.code === "GVH-S1");
    expect(site, "the site this person may capture at").toBeDefined();
    expect(sites.map((s) => s.data.code)).toEqual(["GVH-S1"]); // only where they are assigned
    const materials = await phone.store.reference<{ sku: string; base_unit_id: string }>("materials");
    const crush = materials.find((m) => m.data.sku === "AGG-CRUSH-12")!;
    const vendors = await phone.store.reference<{ code: string; status: string }>("vendors");
    const vendor = vendors.find((v) => v.data.status === "ACTIVE")!;
    expect((await phone.store.reference("units")).length).toBeGreaterThan(3);
    expect((await phone.store.reference("truck_types")).length).toBeGreaterThan(2);
    expect((await phone.store.reference<{ rule_type: string }>("rules")).some((r) => r.data.rule_type === "GEOFENCE_RADIUS")).toBe(true);

    // -- No connection: twenty loads are captured, and the one with a vendor that does not exist ----
    wire.online = false;
    const marker = `LV${Date.now() % 1_000_000}`;
    const entry = (n: number, vendorId = vendor.id): Omit<DeliveryDraft, "id" | "captured_at"> => ({
      site_id: site!.id,
      vendor_id: vendorId,
      truck_number: `${marker}-${n}`,
      location_source: "GPS",
      latitude: site!.data.latitude.toFixed(7),
      longitude: site!.data.longitude.toFixed(7),
      gps_accuracy_m: "8.0",
      items: [{ material_id: crush.id, unit_id: crush.data.base_unit_id, quantity: "10" }],
    });
    const ids: string[] = [];
    for (let n = 0; n < 20; n++) {
      phone.tick(1);
      ids.push((await phone.outbox.captureDelivery(entry(n))).deliveryId);
    }
    const stray = await phone.outbox.captureDelivery(entry(99, "00000000-0000-7000-8000-000000000000"));
    expect((await phone.sync()).offline).toBe(true);
    expect((await phone.store.counts()).pending).toBe(21);

    // -- The signal returns, but the very first reply is lost on the way back ---------------------------------
    wire.online = true;
    wire.dropPushReply = true;
    phone.tick(10);
    expect((await phone.sync()).offline).toBe(true); // the phone cannot know what happened
    phone.tick(10);
    const second = await phone.sync();
    expect(second.duplicates).toBe(20); // the server had them all; it says so
    expect(second.rejected).toBe(1); // ...and refused the one with no such vendor

    // -- The server holds exactly twenty, once each ---------------------------------------------------------------
    const api = await asPerson(STAFF);
    const listed = (await (await api(`/deliveries?q=${marker}&limit=100`)).json()) as {
      items: { id: string; delivery_number: string; truck_number: string }[];
      page: { total: number };
    };
    expect(listed.page.total).toBe(20);
    expect(new Set(listed.items.map((d) => d.id))).toEqual(new Set(ids));
    expect(new Set(listed.items.map((d) => d.delivery_number)).size).toBe(20);
    const one = (await (await api(`/deliveries/${ids[3]}`)).json()) as Record<string, unknown>;
    expect(one.was_offline).toBe(true);
    expect(one.device_id).toBe(uid);
    expect(one.status).toBe("UNDER_REVIEW"); // no order: it is saved and flagged, never refused

    // -- The refused entry is waiting for the person, with a reason they can act on ----------------------------------
    const dead = await phone.store.getOp(stray.opId);
    expect(dead?.status).toBe("DEAD");
    expect((await phone.store.getDelivery(stray.deliveryId))?.local_status).toBe("REJECTED");
    expect((await phone.store.counts()).needsAttention).toBe(1);

    // -- Sending it all again changes nothing ---------------------------------------------------------------------------------
    for (const op of await phone.store.listOps(["DONE"])) {
      await phone.store.updateOp(op.op_id, { status: "PENDING", next_attempt_at: phone.now.toISOString() });
    }
    const replay = await phone.sync();
    expect(replay.duplicates).toBe(20);
    expect(replay.applied).toBe(0);
    const again = (await (await api(`/deliveries?q=${marker}&limit=100`)).json()) as { page: { total: number } };
    expect(again.page.total).toBe(20);

    // -- Head office sends one back; the phone hears why, corrects it, and it is checked afresh ------------------------------
    const manager = await asPerson(MANAGER);
    const target = ids[5]!;
    const sentBack = await manager(`/deliveries/${target}/request-correction`, {
      method: "POST",
      body: JSON.stringify({ comments: "Please re-enter the truck number" }),
    });
    expect(sentBack.status).toBe(200);
    await phone.sync();
    const mirrored = await phone.store.getDelivery(target);
    expect(mirrored?.local_status).toBe("NEEDS_CORRECTION");
    expect(mirrored?.server_status).toBe("CORRECTION_REQUESTED");
    expect(mirrored?.review?.comments).toBe("Please re-enter the truck number");
    expect(mirrored?.server_number).toMatch(/^DLV-/);

    await phone.outbox.submitCorrection(target, { ...mirrored!.payload, truck_number: `${marker}-FIXED` });
    phone.tick(1);
    const fixed = await phone.sync();
    expect(fixed.applied).toBe(1);
    const afterFix = (await (await api(`/deliveries/${target}`)).json()) as { truck_number: string; status: string };
    expect(afterFix.truck_number).toBe(`${marker}-FIXED`);
    expect(afterFix.status).not.toBe("CORRECTION_REQUESTED");
    await phone.sync();
    expect((await phone.store.getDelivery(target))?.local_status).toBe("SYNCED");

    // -- A phone that is lost: revoked, and the queue is untouched ------------------------------------------------------------------
    const mine = (await (await api("/auth/devices")).json()) as { id: string; device_uid: string }[];
    const registered = mine.find((d) => d.device_uid === uid);
    expect(registered, "the phone registered itself on its first push").toBeDefined();
    const revoke = await api(`/auth/devices/${registered!.id}/revoke`, { method: "POST" });
    expect(revoke.status).toBe(200);
    phone.tick(1);
    await phone.outbox.captureDelivery(entry(50));
    const blocked = await phone.sync();
    expect(blocked.deviceRevoked).toBe(true);
    expect((await phone.store.counts()).pending).toBe(1); // still there, waiting
  });
});
