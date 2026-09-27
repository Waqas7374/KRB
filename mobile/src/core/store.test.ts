import { describe, expect, it } from "vitest";

import { delayMs, isTooOld, nextAttemptAt, SCHEDULE_SECONDS } from "./backoff";
import { uuidv7 } from "./ids";
import { LocalStore } from "./store";
import type { OutboxOp } from "./types";
import { saveMessage, statusLabel } from "./labels";
import { normalisePlate, validateDraft } from "./validate";
import { nodeDb } from "../test/node-db";

const op = (id: string, at: string, extra: Partial<OutboxOp> = {}): OutboxOp => ({
  op_id: id,
  entity: "delivery",
  entity_id: `d-${id}`,
  op: "create",
  payload: { id: `d-${id}`, site_id: "s", vendor_id: "v", captured_at: at, location_source: "GPS", items: [] },
  depends_on: null,
  status: "PENDING",
  attempt_count: 0,
  next_attempt_at: at,
  last_error: null,
  created_at: at,
  ...extra,
});

describe("the local database", () => {
  it("creates its tables once and is safe to open again", async () => {
    const db = nodeDb();
    await new LocalStore(db).migrate();
    await new LocalStore(db).migrate();
    const [row] = await db.all<{ user_version: number }>("PRAGMA user_version");
    expect(row!.user_version).toBe(1);
  });

  it("hands out the oldest due entry first, and holds back what is not yet due", async () => {
    const store = new LocalStore(nodeDb());
    await store.migrate();
    await store.insertOp(op("b", "2026-09-27T08:00:02Z"));
    await store.insertOp(op("a", "2026-09-27T08:00:01Z"));
    await store.insertOp(op("later", "2026-09-27T08:00:00Z", { next_attempt_at: "2026-09-27T09:00:00Z" }));
    const due = await store.dueOps(new Date("2026-09-27T08:30:00Z"), 10);
    expect(due.map((o) => o.op_id)).toEqual(["a", "b"]);
    expect((await store.dueOps(new Date("2026-09-27T08:30:00Z"), 1)).map((o) => o.op_id)).toEqual(["a"]);
  });

  it("holds an entry back until the one it depends on is done", async () => {
    const store = new LocalStore(nodeDb());
    await store.migrate();
    await store.insertOp(op("delivery", "2026-09-27T08:00:00Z"));
    await store.insertOp(op("photo", "2026-09-27T08:00:01Z", { depends_on: "delivery" }));
    const now = new Date("2026-09-27T09:00:00Z");
    expect((await store.dueOps(now, 10)).map((o) => o.op_id)).toEqual(["delivery"]);
    await store.updateOp("delivery", { status: "DONE" });
    expect((await store.dueOps(now, 10)).map((o) => o.op_id)).toEqual(["photo"]);
  });

  it("puts anything left in flight back in the queue", async () => {
    const store = new LocalStore(nodeDb());
    await store.migrate();
    await store.insertOp(op("x", "2026-09-27T08:00:00Z", { status: "INFLIGHT" }));
    expect(await store.recoverInflight()).toBe(1);
    expect((await store.getOp("x"))?.status).toBe("PENDING");
  });

  it("counts what is waiting and what needs a person", async () => {
    const store = new LocalStore(nodeDb());
    await store.migrate();
    await store.insertOp(op("a", "2026-09-27T08:00:00Z"));
    await store.insertOp(op("b", "2026-09-27T08:00:01Z", { status: "FAILED" }));
    await store.insertOp(op("c", "2026-09-27T08:00:02Z", { status: "DEAD" }));
    await store.insertOp(op("d", "2026-09-27T08:00:03Z", { status: "DONE" }));
    expect(await store.counts()).toEqual({ pending: 2, needsAttention: 1 });
  });

  it("rolls a transaction back entirely when something in it fails", async () => {
    const store = new LocalStore(nodeDb());
    await store.migrate();
    await expect(
      store.transaction(async () => {
        await store.insertOp(op("a", "2026-09-27T08:00:00Z"));
        throw new Error("boom");
      }),
    ).rejects.toThrow("boom");
    expect(await store.listOps()).toEqual([]);
  });
});

describe("retry timing", () => {
  it("follows the agreed schedule, then hourly", () => {
    const steady = () => 0.5; // no jitter
    expect([0, 1, 2, 3, 4, 5, 6, 7, 20].map((n) => delayMs(n, steady) / 1000)).toEqual([
      2, 5, 15, 60, 300, 900, 3600, 3600, 3600,
    ]);
    expect(SCHEDULE_SECONDS[0]).toBe(2);
  });

  it("spreads phones out by up to 20 % either way", () => {
    expect(delayMs(3, () => 0)).toBe(48_000);
    expect(delayMs(3, () => 0.999999)).toBeGreaterThan(71_000);
    expect(delayMs(3, () => 0.999999)).toBeLessThanOrEqual(72_000);
  });

  it("knows when 72 hours have passed", () => {
    const now = new Date("2026-09-30T08:00:00Z");
    expect(isTooOld("2026-09-27T09:00:00Z", now)).toBe(false);
    expect(isTooOld("2026-09-27T07:00:00Z", now)).toBe(true);
    expect(nextAttemptAt(new Date("2026-09-27T08:00:00Z"), 0, () => 0.5)).toBe("2026-09-27T08:00:02.000Z");
  });
});

describe("ids", () => {
  it("are UUIDv7: valid, unique and sortable by the time they were made", () => {
    const a = uuidv7(1_700_000_000_000);
    const b = uuidv7(1_700_000_000_001);
    const later = uuidv7(1_800_000_000_000);
    expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(new Set(Array.from({ length: 1000 }, () => uuidv7())).size).toBe(1000);
    expect(a < b && b < later).toBe(true);
  });
});

describe("what the phone checks before saving", () => {
  const draft = {
    id: "i",
    site_id: "s",
    vendor_id: "v",
    captured_at: "2026-09-27T08:00:00Z",
    location_source: "GPS" as const,
    items: [{ material_id: "m", unit_id: "u", quantity: "12.5" }],
  };

  it("passes a complete entry, with or without a position", () => {
    expect(validateDraft(draft)).toEqual([]);
    expect(validateDraft({ ...draft, latitude: "31.411", longitude: "74.2461" })).toEqual([]);
  });

  it("names what is missing, in words a person can act on", () => {
    const fields = validateDraft({ ...draft, site_id: "", vendor_id: "", items: [] }).map((i) => i.field);
    expect(fields).toEqual(["site_id", "vendor_id", "items"]);
    expect(validateDraft({ ...draft, items: [{ material_id: "m", unit_id: "u", quantity: "abc" }] })[0]!.message).toMatch(/above zero/);
    expect(validateDraft({ ...draft, latitude: "95", longitude: "74" })[0]!.field).toBe("latitude");
  });

  it("tidies a plate typed with a thumb", () => {
    expect(normalisePlate("  leb - 1234 ")).toBe("LEB-1234");
    expect(normalisePlate("lea  4411!!")).toBe("LEA 4411");
  });
});

describe("what the person is told", () => {
  const entry = (local: string, server: string | null = null) =>
    ({ local_status: local, server_status: server }) as Parameters<typeof statusLabel>[0];

  it("never claims more than is true", () => {
    expect(statusLabel(entry("QUEUED")).text).toBe("Saved on device");
    expect(statusLabel(entry("SYNCED", "UNDER_REVIEW"))).toMatchObject({ text: "Under review", tone: "warn" });
    expect(statusLabel(entry("SYNCED", "APPROVED")).tone).toBe("good");
    expect(statusLabel(entry("SYNCED", "REJECTED")).tone).toBe("bad");
  });

  it("says what the person can do about an entry that needs them", () => {
    expect(statusLabel(entry("NEEDS_CORRECTION", "CORRECTION_REQUESTED")).action).toBe("correct");
    expect(statusLabel(entry("REJECTED")).action).toBe("fix");
    expect(statusLabel(entry("CONFLICT", "APPROVED")).action).toBe("acknowledge");
    expect(statusLabel(entry("SYNCED", "APPROVED")).action).toBeNull();
  });

  it("words the message after Save by what actually happened", () => {
    expect(saveMessage({ online: false })).toBe("Saved on device. It will sync when you are online.");
    expect(saveMessage({ online: true, number: "DLV-1" })).toBe("Delivery DLV-1 recorded.");
    expect(saveMessage({ online: true, number: "DLV-1", flagged: "No purchase order" })).toMatch(/Flagged for review: No purchase order/);
  });
});
