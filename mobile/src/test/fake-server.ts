import type { DeliveryDraft, PullChange, PushOutcome } from "../core/types";

/**
 * A stand-in for the ERP that behaves like the real one where the client cares (idempotent on
 * the delivery id, a separate outcome for every entry, 401 on a stale token) and can be made to
 * misbehave on demand: drop the reply after doing the work, go offline, refuse an entry.
 */
interface Stored {
  id: string;
  number: string;
  draft: DeliveryDraft;
}

interface PushedOp {
  op_id: string;
  entity: string;
  op: string;
  payload: DeliveryDraft;
}

export class FakeServer {
  records = new Map<string, Stored>();
  /** Every push request received, as the list of delivery ids in it, in order. */
  pushes: string[][] = [];
  pulls = 0;
  refreshes = 0;

  online = true;
  /** Do the work, then lose the reply (the phone sees a network error). */
  dropNextReply = false;
  /** Answer every entry for these delivery ids with this outcome instead of recording it. */
  forced = new Map<string, { outcome: PushOutcome; code?: string; message?: string }>();
  /** Reject an entry whose vendor is this. */
  rejectVendor: string | null = null;
  /** The server's answer to the next call is a 5xx. */
  failNext5xx = false;

  validAccess = "access-1";
  validRefresh = "refresh-1";
  refreshWorks = true;

  changes: PullChange[] = [];
  private seq = 0;
  private counter = 0;

  /** Add reference data or a delivery update to be handed out by the next pull. */
  publish(entity: string, id: string, data: object, deleted = false): void {
    this.changes.push({ entity, id, deleted, server_seq: ++this.seq, data: data as never });
  }

  fetch = async (input: string, init?: RequestInit): Promise<Response> => {
    if (!this.online) throw new TypeError("Network request failed");
    const url = new URL(input);
    const path = url.pathname.replace(/^\/api\/v1/, "");
    if (this.failNext5xx) {
      this.failNext5xx = false;
      return json({ title: "Internal server error", status: 500 }, 500);
    }
    if (path === "/auth/login") return this.login(init);
    if (path === "/auth/refresh") return this.refresh(init);
    const token = (init?.headers as { Authorization?: string } | undefined)?.Authorization;
    if (token !== `Bearer ${this.validAccess}`) return json({ title: "Authentication required" }, 401);
    if (path === "/sync/push") return this.push(init);
    if (path === "/sync/pull") return this.pull(url);
    return json({ title: "Not found" }, 404);
  };

  private tokens() {
    return {
      access_token: this.validAccess,
      refresh_token: this.validRefresh,
      user: { id: "u1", full_name: "Site Staff", company_id: "c1", email: null },
    };
  }

  private login(init?: RequestInit): Response {
    const body = JSON.parse(String(init?.body)) as { password: string };
    if (body.password !== "right") {
      return json({ title: "Invalid credentials", detail: "The identifier or password is wrong" }, 401);
    }
    return json(this.tokens());
  }

  private refresh(init?: RequestInit): Response {
    this.refreshes++;
    const body = JSON.parse(String(init?.body)) as { refresh_token: string };
    if (!this.refreshWorks || body.refresh_token !== this.validRefresh) {
      return json({ title: "Token expired", detail: "expired" }, 401);
    }
    this.validAccess = `access-${this.refreshes + 1}`;
    this.validRefresh = `refresh-${this.refreshes + 1}`;
    return json(this.tokens());
  }

  private push(init?: RequestInit): Response {
    const body = JSON.parse(String(init?.body)) as { ops: PushedOp[] };
    this.pushes.push(body.ops.map((o) => o.payload.id));
    const results = body.ops.map((op) => {
      const forced = this.forced.get(op.payload.id);
      if (forced) {
        return {
          op_id: op.op_id,
          outcome: forced.outcome,
          record: forced.outcome === "conflict" ? this.recordFor(op.payload.id) : null,
          error: {
            code: forced.code ?? forced.outcome,
            message: forced.message ?? forced.outcome,
            retryable: forced.outcome === "deferred",
            fields: [],
          },
        };
      }
      if (this.rejectVendor && op.payload.vendor_id === this.rejectVendor) {
        return {
          op_id: op.op_id,
          outcome: "rejected",
          record: null,
          error: {
            code: "validation_failed",
            message: "That vendor is no longer active",
            retryable: false,
            fields: [{ field: "vendor_id", message: "inactive" }],
          },
        };
      }
      const known = this.records.get(op.payload.id);
      if (known && op.op === "create") {
        return { op_id: op.op_id, outcome: "duplicate", record: this.wire(known), error: null };
      }
      const rec = known ?? {
        id: op.payload.id,
        number: `DLV-${String(++this.counter).padStart(5, "0")}`,
        draft: op.payload,
      };
      rec.draft = op.payload;
      this.records.set(rec.id, rec);
      return { op_id: op.op_id, outcome: "applied", record: this.wire(rec), error: null };
    });
    if (this.dropNextReply) {
      this.dropNextReply = false;
      throw new TypeError("Network request failed"); // the work is done; the phone never hears
    }
    return json({ server_time: new Date().toISOString(), results });
  }

  private recordFor(id: string) {
    const rec = this.records.get(id);
    return rec ? this.wire(rec) : null;
  }

  private wire(rec: Stored) {
    return {
      id: rec.id,
      delivery_number: rec.number,
      status: "UNDER_REVIEW",
      flags: [{ type: "NO_PO", severity: "WARNING", message: "No purchase order" }],
    };
  }

  private pull(url: URL): Response {
    this.pulls++;
    const since = Number(url.searchParams.get("since") ?? "0");
    const limit = Number(url.searchParams.get("limit") ?? "200");
    const newer = this.changes.filter((c) => c.server_seq > since);
    const page = newer.slice(0, limit);
    return json({
      server_time: new Date().toISOString(),
      server_seq: page.length ? page[page.length - 1]!.server_seq : since,
      has_more: newer.length > limit,
      not_permitted: [],
      changes: page,
    });
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
