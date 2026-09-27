import { beforeEach, describe, expect, it } from "vitest";

import { DEAD_AFTER_HOURS } from "./backoff";
import { InvalidEntryError } from "./outbox";
import { SyncEngine } from "./sync";
import { FakeServer } from "../test/fake-server";
import { Phone } from "../test/harness";

let server: FakeServer;
let phone: Phone;

beforeEach(async () => {
  server = new FakeServer();
  phone = new Phone(server.fetch);
  await phone.start();
  await phone.signIn();
});

const statuses = async () =>
  (await phone.store.listOps()).reduce<Record<string, number>>((n, op) => {
    n[op.status] = (n[op.status] ?? 0) + 1;
    return n;
  }, {});

describe("twenty deliveries captured offline", () => {
  it("land exactly once, in the order they were made, when the phone reconnects", async () => {
    server.online = false;
    const ids = await phone.capture(20);

    // Offline: nothing is lost, nothing is sent, and the person is told the truth.
    const offline = await phone.sync();
    expect(offline.offline).toBe(true);
    expect(server.records.size).toBe(0);
    expect((await phone.store.counts()).pending).toBe(20);
    expect((await phone.store.getDelivery(ids[0]!))?.local_status).toBe("QUEUED");

    // Back online: the backoff has elapsed, and one run sends them all.
    server.online = true;
    phone.tick(10);
    const report = await phone.sync();
    expect(report.applied).toBe(20);
    expect(server.records.size).toBe(20);
    expect([...server.records.keys()]).toEqual(ids); // creation order preserved
    expect(await statuses()).toEqual({ DONE: 20 });
    expect((await phone.store.getDelivery(ids[7]!))?.local_status).toBe("SYNCED");
    expect((await phone.store.getDelivery(ids[7]!))?.server_number).toMatch(/^DLV-/);
    expect((await phone.store.counts()).pending).toBe(0);
  });

  it("are never applied twice, however many times they are sent", async () => {
    const ids = await phone.capture(5);
    await phone.sync();
    // The phone is told nothing was received (say, a restored backup) and sends everything again.
    for (const op of await phone.store.listOps()) {
      await phone.store.updateOp(op.op_id, { status: "PENDING", next_attempt_at: phone.now.toISOString() });
    }
    const again = await phone.sync();
    expect(again.duplicates).toBe(5);
    expect(again.applied).toBe(0);
    expect(server.records.size).toBe(5);
    expect([...server.records.keys()]).toEqual(ids);
  });
});

describe("a connection that fails at the worst moment", () => {
  it("loses nothing when the reply to a push never arrives", async () => {
    const ids = await phone.capture(6);
    server.dropNextReply = true; // the server records all six; the phone hears nothing

    const first = await phone.sync();
    expect(first.offline).toBe(true);
    expect(server.records.size).toBe(6);
    expect(await statuses()).toEqual({ PENDING: 6 }); // still queued: it never learned they arrived

    phone.tick(10);
    const second = await phone.sync();
    expect(second.duplicates).toBe(6); // the server recognises them and replays its answer
    expect(server.records.size).toBe(6);
    expect(await statuses()).toEqual({ DONE: 6 });
    expect((await phone.store.getDelivery(ids[0]!))?.server_number).toMatch(/^DLV-/);
  });

  it("recovers what a killed app left in flight, and does not double it", async () => {
    const ids = await phone.capture(4);
    // The app died mid-push: the entries were marked in flight and the answer never came.
    for (const op of await phone.store.listOps()) await phone.store.updateOp(op.op_id, { status: "INFLIGHT" });
    server.records.set(ids[0]!, { id: ids[0]!, number: "DLV-00001", draft: (await phone.store.getOp((await phone.store.listOps())[0]!.op_id))!.payload });

    // A new process: same database, fresh engine.
    const restarted = phone.newEngine();
    expect(await restarted.recover()).toBe(4);
    expect(await statuses()).toEqual({ PENDING: 4 });

    const report = await restarted.run();
    expect(report.duplicates).toBe(1); // the one the server already had
    expect(report.applied).toBe(3);
    expect(server.records.size).toBe(4);
  });

  it("backs off, then tries again when the wait is over", async () => {
    await phone.capture(1);
    server.online = false;
    await phone.sync(); // fails at t0
    const [op] = await phone.store.listOps();
    expect(op!.attempt_count).toBe(1);
    expect(op!.last_error?.code).toBe("offline");
    // Retry after 2 s (the first step of the schedule): not before.
    server.online = true;
    expect((await phone.sync()).sent).toBe(0);
    phone.tick(3);
    expect((await phone.sync()).applied).toBe(1);
  });

  it("treats a server error as the server's problem, not the entry's", async () => {
    await phone.capture(3);
    server.failNext5xx = true;
    const report = await phone.sync();
    expect(report.deferred).toBe(3);
    expect(server.records.size).toBe(0);
    expect((await statuses()).PENDING).toBe(3);
    phone.tick(10);
    expect((await phone.sync()).applied).toBe(3);
  });

  it("stops retrying after 72 hours and waits for a person, without discarding anything", async () => {
    const [id] = await phone.capture(1);
    server.online = false;
    await phone.sync();
    phone.tick((DEAD_AFTER_HOURS + 1) * 3600);
    await phone.sync();
    const [op] = await phone.store.listOps();
    expect(op!.status).toBe("DEAD");
    expect(op!.last_error?.message).toMatch(/Contact head office/);
    expect(await phone.store.getDelivery(id!)).not.toBeNull(); // still there
    expect((await phone.store.counts()).needsAttention).toBe(1);
  });
});

describe("batches", () => {
  it("send at most 50 entries per request, oldest first, without reordering", async () => {
    const ids = await phone.capture(120);
    await phone.sync();
    expect(server.pushes.map((p) => p.length)).toEqual([50, 50, 20]);
    expect(server.pushes.flat()).toEqual(ids);
    expect(server.records.size).toBe(120);
  });

  it("also respect a byte budget, so one request can finish on a poor connection", async () => {
    const small = new Phone(server.fetch);
    await small.start();
    await small.signIn();
    (small as unknown as { engine: unknown }).engine = new SyncEngine({
      api: small.api,
      store: small.store,
      device: { device_id: "d", app_version: "1", platform: "ANDROID" },
      clock: () => small.now,
      random: () => 0.5,
      maxBytes: 900, // a couple of entries per request
    });
    await small.capture(7);
    await small.sync();
    expect(server.pushes.every((p) => p.length >= 1 && p.length <= 4)).toBe(true);
    expect(server.pushes.flat().length).toBe(7);
  });
});

describe("each entry has its own fate", () => {
  it("one refused entry does not hold up the others, and can be fixed and resent", async () => {
    const ids = await phone.capture(3);
    const bad = await phone.outbox.captureDelivery(phone.draft(9, { vendor_id: "vendor-gone" }));
    server.rejectVendor = "vendor-gone";
    const report = await phone.sync();
    expect(report.applied).toBe(3);
    expect(report.rejected).toBe(1);
    expect(server.records.size).toBe(3);

    const dead = await phone.store.getOp(bad.opId);
    expect(dead?.status).toBe("DEAD");
    expect(dead?.last_error?.fields?.[0]?.field).toBe("vendor_id");
    const mirror = await phone.store.getDelivery(bad.deliveryId);
    expect(mirror?.local_status).toBe("REJECTED");
    expect(mirror?.last_error?.message).toMatch(/no longer active/);
    expect((await phone.store.counts()).needsAttention).toBe(1);
    expect(ids).toHaveLength(3);

    // The person picks another vendor: the same entry goes back in line, under the same id.
    await phone.outbox.fixAndRetry(bad.opId, { ...phone.draft(9, { vendor_id: "vendor-2" }), id: "x", captured_at: dead!.payload.captured_at });
    const again = await phone.sync();
    expect(again.applied).toBe(1);
    expect(server.records.has(bad.deliveryId)).toBe(true);
    expect((await phone.store.getDelivery(bad.deliveryId))?.local_status).toBe("SYNCED");
  });

  it("marks an entry head office already decided as a conflict: the server's version stands", async () => {
    const [id] = await phone.capture(1);
    server.forced.set(id!, {
      outcome: "conflict",
      code: "already_reviewed",
      message: "This entry was already handled at head office; the server's version stands.",
    });
    server.records.set(id!, { id: id!, number: "DLV-00042", draft: (await phone.store.getDelivery(id!))!.payload });
    const report = await phone.sync();
    expect(report.conflicts).toBe(1);
    const mirror = await phone.store.getDelivery(id!);
    expect(mirror?.local_status).toBe("CONFLICT");
    expect(mirror?.server_number).toBe("DLV-00042");
    expect(mirror?.last_error?.code).toBe("already_reviewed");
    expect((await phone.store.getOp((await phone.store.listOps())[0]!.op_id))?.status).toBe("DONE");

    await phone.outbox.acknowledgeConflict(id!);
    expect((await phone.store.getDelivery(id!))?.local_status).toBe("SYNCED");
  });

  it("retries a transient 'try again shortly' later, with a delay", async () => {
    const [id] = await phone.capture(1);
    server.forced.set(id!, { outcome: "deferred", code: "server_error", message: "Try again shortly" });
    expect((await phone.sync()).deferred).toBe(1);
    const [op] = await phone.store.listOps();
    expect(op!.status).toBe("FAILED");
    expect(new Date(op!.next_attempt_at).getTime()).toBeGreaterThan(phone.now.getTime());
    server.forced.clear();
    phone.tick(10);
    expect((await phone.sync()).applied).toBe(1);
  });

  it("refuses to save an entry that is not complete, before it can be lost in the queue", async () => {
    await expect(
      phone.outbox.captureDelivery({ ...phone.draft(0), items: [] }),
    ).rejects.toBeInstanceOf(InvalidEntryError);
    await expect(
      phone.outbox.captureDelivery({ ...phone.draft(0), items: [{ material_id: "m", unit_id: "u", quantity: "0" }] }),
    ).rejects.toThrow(/above zero/);
    expect((await phone.store.counts()).pending).toBe(0);
  });
});

describe("the session", () => {
  it("renews an expired access token once, quietly, and carries on", async () => {
    await phone.capture(2);
    server.validAccess = "rotated-elsewhere"; // the phone's token is now stale
    const report = await phone.sync();
    expect(server.refreshes).toBe(1);
    expect(report.applied).toBe(2);
  });

  it("parks the queue when the session is truly over, and resumes after signing in again", async () => {
    await phone.capture(3);
    server.validAccess = "stale";
    server.refreshWorks = false;
    const report = await phone.sync();
    expect(report.authRequired).toBe(true);
    // Nothing lost, nothing penalised: the entries wait as they were.
    expect(await statuses()).toEqual({ PENDING: 3 });
    expect((await phone.store.listOps()).every((o) => o.attempt_count === 0)).toBe(true);
    expect((await phone.engine.status()).authRequired).toBe(true);

    server.refreshWorks = true;
    server.validAccess = "access-1";
    await phone.signIn();
    expect((await phone.sync()).applied).toBe(3);
  });

  it("survives being offline at start-up: a refresh token on the phone is still a session", async () => {
    server.online = false;
    const fresh = new Phone(server.fetch, phone.db);
    fresh.secure.values = new Map(phone.secure.values);
    expect(await fresh.session.restore()).toBe("offline");
    // ...and the person can still capture.
    await fresh.outbox.captureDelivery(fresh.draft(1));
    expect((await fresh.store.counts()).pending).toBe(1);
  });

  it("a wrong password is refused and leaves nothing behind", async () => {
    const other = new Phone(server.fetch);
    await other.start();
    await expect(other.session.login("staff", "wrong")).rejects.toThrow(/wrong/);
    expect(await other.session.restore()).toBe("signed_out");
  });

  it("signing out keeps the queue", async () => {
    await phone.capture(2);
    await phone.session.signOut();
    expect((await phone.store.counts()).pending).toBe(2);
    expect(await phone.session.restore()).toBe("signed_out");
  });
});

describe("one run at a time", () => {
  it("shares a single run between callers that ask together", async () => {
    await phone.capture(5);
    const [a, b, c] = await Promise.all([phone.sync(), phone.sync(), phone.sync()]);
    expect(a).toBe(b);
    expect(b).toBe(c);
    expect(server.pushes).toHaveLength(1);
    expect(server.records.size).toBe(5);
  });
});

describe("pulling", () => {
  it("brings down reference data and removes what was withdrawn", async () => {
    server.publish("materials", "m1", { sku: "AGG-1", name: "Crush" });
    server.publish("vendors", "v1", { code: "V1", name: "Shree Stone" });
    server.publish("open_pos", "po1", { po_number: "PO-1", items: [] });
    const first = await phone.sync();
    expect(first.pulled).toBe(3);
    expect((await phone.store.reference("materials")).map((r) => r.id)).toEqual(["m1"]);
    expect(await phone.store.cursor()).toBe(3);

    server.publish("open_pos", "po1", {}, true); // the order was closed
    server.publish("materials", "m1", { sku: "AGG-1", name: "Crush 20mm" });
    await phone.sync();
    expect(await phone.store.reference("open_pos")).toEqual([]);
    expect((await phone.store.reference<{ name: string }>("materials"))[0]!.data.name).toBe("Crush 20mm");
    expect(await phone.store.cursor()).toBe(5);
  });

  it("follows the cursor across pages without skipping or repeating", async () => {
    for (let i = 0; i < 450; i++) server.publish("materials", `m${i}`, { sku: `S${i}` });
    await phone.sync();
    expect((await phone.store.reference("materials")).length).toBe(450);
    expect(server.pulls).toBe(3); // 200 + 200 + 50
    expect(await phone.store.cursor()).toBe(450);
    server.pulls = 0;
    await phone.sync();
    expect(server.pulls).toBe(1); // nothing new: one cheap call
  });

  it("tells the person what head office decided about an entry, with the reviewer's words", async () => {
    const [id] = await phone.capture(1);
    await phone.sync();
    server.publish("my_deliveries", id!, {
      delivery_number: "DLV-00001",
      status: "CORRECTION_REQUESTED",
      can_correct: true,
      open_flags: [{ flag_type: "NO_PO", severity: "WARNING", message: "No purchase order" }],
      review: { action: "REQUEST_CORRECTION", comments: "Wrong truck number", reviewer_name: "Rashid", reviewed_at: "2026-09-27T09:00:00Z" },
      captured_at: "2026-09-27T08:00:00Z",
      entry: (await phone.store.getDelivery(id!))!.payload,
    });
    await phone.sync();
    const d = await phone.store.getDelivery(id!);
    expect(d?.local_status).toBe("NEEDS_CORRECTION");
    expect(d?.server_status).toBe("CORRECTION_REQUESTED");
    expect(d?.review?.comments).toBe("Wrong truck number");

    // The person corrects it: a new operation on the same delivery.
    const corrected = { ...d!.payload, truck_number: "LEB-9999" };
    await phone.outbox.submitCorrection(id!, corrected);
    expect((await phone.store.getDelivery(id!))?.local_status).toBe("QUEUED");
    const report = await phone.sync();
    expect(report.sent).toBe(1);
    expect(server.records.get(id!)?.draft.truck_number).toBe("LEB-9999");
  });

  it("does not overwrite an edit that is still waiting to be sent", async () => {
    const [id] = await phone.capture(1);
    server.online = false;
    await phone.sync();
    server.online = true;
    // Head office's view arrives while the person's own edit is still queued.
    server.publish("my_deliveries", id!, {
      delivery_number: "DLV-00001", status: "UNDER_REVIEW", can_correct: false, open_flags: [], review: null,
      captured_at: "2026-09-27T08:00:00Z", entry: (await phone.store.getDelivery(id!))!.payload,
    });
    // (a separate pull, without pushing first)
    await phone.api.get("/sync/pull", { since: 0 }).then(async (page) => {
      await phone.store.transaction(async () => {
        await (phone.engine as unknown as { applyMyDeliveries: (c: unknown) => Promise<void> }).applyMyDeliveries((page as { changes: unknown[] }).changes);
      });
    });
    expect((await phone.store.getDelivery(id!))?.local_status).toBe("QUEUED");
  });

  it("can start again from nothing, as the once-a-day safety net", async () => {
    server.publish("materials", "m1", { sku: "A" });
    await phone.sync();
    server.pulls = 0;
    await phone.engine.refreshEverything();
    expect(await phone.store.cursor()).toBe(1);
    expect((await phone.store.reference("materials")).length).toBe(1);
  });
});
