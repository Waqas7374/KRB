import { ApiClient, ApiError, AuthRequiredError, NetworkError } from "./api";
import { isTooOld, nextAttemptAt } from "./backoff";
import type { LocalStore } from "./store";
import type {
  LocalDelivery,
  OpError,
  OutboxOp,
  PullChange,
  PullResponse,
  PushResponse,
  PushResult,
  SyncStatus,
} from "./types";

/**
 * Sending what was captured and fetching what changed (docs/06 §4-6).
 *
 * The rules this file exists to keep:
 *  - **One at a time.** Foreground, a timer and a "sync now" tap can all ask together; they
 *    share one run.
 *  - **Nothing is lost, and nothing is applied twice.** An entry leaves the queue only when the
 *    server says it has it. A dropped connection, a killed app, a lost reply: all lead to the
 *    same entry being sent again, and the server answers `duplicate`.
 *  - **Each entry has its own fate.** One the server refuses does not hold up the forty behind it.
 *  - **A session that ends parks the queue; it does not empty it.**
 */
export interface SyncDeps {
  api: ApiClient;
  store: LocalStore;
  device: { device_id: string; app_version: string; platform: string };
  clock?: () => Date;
  random?: () => number;
  onStatus?: (status: SyncStatus) => void;
  maxOps?: number;
  maxBytes?: number;
}

export interface SyncReport {
  sent: number;
  applied: number;
  duplicates: number;
  conflicts: number;
  rejected: number;
  deferred: number;
  pulled: number;
  offline: boolean;
  authRequired: boolean;
  deviceRevoked: boolean;
}

const emptyReport = (): SyncReport => ({
  sent: 0,
  applied: 0,
  duplicates: 0,
  conflicts: 0,
  rejected: 0,
  deferred: 0,
  pulled: 0,
  offline: false,
  authRequired: false,
  deviceRevoked: false,
});

const PULL_PAGE = 200;

export class SyncEngine {
  private inFlight: Promise<SyncReport> | null = null;
  private last: Pick<SyncStatus, "online" | "authRequired" | "deviceRevoked"> = {
    online: null,
    authRequired: false,
    deviceRevoked: false,
  };
  private readonly clock: () => Date;
  private readonly random: () => number;

  constructor(private readonly deps: SyncDeps) {
    this.clock = deps.clock ?? (() => new Date());
    this.random = deps.random ?? Math.random;
  }

  /** Call once at start-up: anything left in flight by a crash is simply sent again. */
  async recover(): Promise<number> {
    return this.deps.store.recoverInflight();
  }

  /** Safe to call from anywhere, any number of times. */
  run(): Promise<SyncReport> {
    this.inFlight ??= this.execute().finally(() => {
      this.inFlight = null;
      void this.publish(false);
    });
    void this.publish(true);
    return this.inFlight;
  }

  async status(running = this.inFlight !== null): Promise<SyncStatus> {
    const counts = await this.deps.store.counts();
    return {
      running,
      online: this.last.online,
      pending: counts.pending,
      needsAttention: counts.needsAttention,
      lastSyncedAt: await this.deps.store.getKv("last_synced_at"),
      authRequired: this.last.authRequired,
      deviceRevoked: this.last.deviceRevoked,
    };
  }

  /** A full pull from nothing: the once-a-day safety net (docs/18 §3f, known limit). */
  async refreshEverything(): Promise<SyncReport> {
    await this.deps.store.clearReference();
    return this.run();
  }

  private async publish(running: boolean): Promise<void> {
    if (this.deps.onStatus) this.deps.onStatus(await this.status(running));
  }

  // -- The run ------------------------------------------------------------------------------------------

  private async execute(): Promise<SyncReport> {
    const report = emptyReport();
    try {
      await this.push(report);
      if (!report.offline && !report.authRequired && !report.deviceRevoked) {
        await this.pull(report);
      }
    } catch (err) {
      this.classify(err, report);
    }
    this.last = {
      online: !report.offline,
      authRequired: report.authRequired,
      deviceRevoked: report.deviceRevoked,
    };
    if (!report.offline && !report.authRequired && !report.deviceRevoked) {
      await this.deps.store.setKv("last_synced_at", this.clock().toISOString());
    }
    return report;
  }

  private classify(err: unknown, report: SyncReport): void {
    if (err instanceof NetworkError) report.offline = true;
    else if (err instanceof AuthRequiredError) report.authRequired = true;
    else if (err instanceof ApiError && err.status === 403 && /revoked/i.test(err.message)) {
      report.deviceRevoked = true;
    } else throw err;
  }

  // -- Push ---------------------------------------------------------------------------------------------------

  private async push(report: SyncReport): Promise<void> {
    const { store } = this.deps;
    const maxOps = this.deps.maxOps ?? 50;
    const maxBytes = this.deps.maxBytes ?? 512 * 1024;
    for (;;) {
      const due = await store.dueOps(this.clock(), maxOps);
      const batch = fit(due, maxBytes);
      if (batch.length === 0) return;

      await store.transaction(async () => {
        for (const op of batch) await store.updateOp(op.op_id, { status: "INFLIGHT" });
      });
      report.sent += batch.length;

      let response: PushResponse;
      try {
        response = await this.deps.api.post<PushResponse>("/sync/push", {
          device_id: this.deps.device.device_id,
          app_version: this.deps.device.app_version,
          platform: this.deps.device.platform,
          device_time: this.clock().toISOString(),
          ops: batch.map((op) => ({
            op_id: op.op_id,
            entity: op.entity,
            op: op.op,
            client_created_at: op.created_at,
            payload: op.payload,
          })),
        });
      } catch (err) {
        await this.requeue(batch, err, report);
        return;
      }
      await this.settle(batch, response, report);
    }
  }

  /** The request itself failed: nothing was decided about these entries, so none is lost. */
  private async requeue(batch: OutboxOp[], err: unknown, report: SyncReport): Promise<void> {
    const { store } = this.deps;
    const now = this.clock();
    // A session ending or a device revoked is about the person, not the entries: no penalty.
    const penalise = !(
      err instanceof AuthRequiredError ||
      (err instanceof ApiError && err.status === 403 && /revoked/i.test(err.message))
    );
    const message =
      err instanceof NetworkError
        ? "No connection"
        : err instanceof ApiError
          ? err.message
          : "The request failed";
    await store.transaction(async () => {
      for (const op of batch) {
        const attempts = op.attempt_count + (penalise ? 1 : 0);
        const tooOld = penalise && isTooOld(op.created_at, now);
        await store.updateOp(op.op_id, {
          status: tooOld ? "DEAD" : "PENDING",
          attempt_count: attempts,
          next_attempt_at: penalise
            ? nextAttemptAt(now, op.attempt_count, this.random)
            : op.next_attempt_at,
          last_error: penalise
            ? {
                code: tooOld ? "too_old" : err instanceof NetworkError ? "offline" : "server_error",
                message: tooOld
                  ? "This entry could not be sent for 72 hours. Contact head office."
                  : message,
                retryable: !tooOld,
              }
            : op.last_error,
        });
      }
    });
    if (err instanceof NetworkError) report.offline = true;
    else if (err instanceof AuthRequiredError) report.authRequired = true;
    else if (err instanceof ApiError && err.status === 403 && /revoked/i.test(err.message)) {
      report.deviceRevoked = true;
    } else if (err instanceof ApiError) {
      // The server answered with a problem (a 5xx, a rate limit): these entries wait their turn
      // and this run stops rather than hammering a struggling server.
      report.deferred += batch.length;
    } else throw err;
  }

  private async settle(batch: OutboxOp[], response: PushResponse, report: SyncReport): Promise<void> {
    const { store } = this.deps;
    const now = this.clock();
    const byId = new Map<string, PushResult>(response.results.map((r) => [r.op_id, r]));
    await store.transaction(async () => {
      for (const op of batch) {
        const result = byId.get(op.op_id);
        if (!result) {
          // The server did not answer for this one: as if it had said "later".
          await this.defer(op, { code: "no_answer", message: "The server did not answer", retryable: true }, now);
          report.deferred++;
          continue;
        }
        switch (result.outcome) {
          case "applied":
          case "duplicate":
            await store.updateOp(op.op_id, { status: "DONE", last_error: null });
            await this.mirror(op, result, "SYNCED");
            if (result.outcome === "applied") report.applied++;
            else report.duplicates++;
            break;
          case "conflict":
            // Head office already decided: the server's version stands, and the person is told.
            await store.updateOp(op.op_id, { status: "DONE", last_error: result.error });
            await this.mirror(op, result, "CONFLICT");
            report.conflicts++;
            break;
          case "rejected":
            await store.updateOp(op.op_id, { status: "DEAD", last_error: result.error });
            await this.mirror(op, result, "REJECTED");
            report.rejected++;
            break;
          case "deferred":
            await this.defer(op, result.error ?? { code: "deferred", message: "Try again shortly", retryable: true }, now);
            report.deferred++;
            break;
        }
      }
    });
  }

  private async defer(op: OutboxOp, error: OpError, now: Date): Promise<void> {
    const tooOld = isTooOld(op.created_at, now);
    await this.deps.store.updateOp(op.op_id, {
      status: tooOld ? "DEAD" : "FAILED",
      attempt_count: op.attempt_count + 1,
      next_attempt_at: nextAttemptAt(now, op.attempt_count, this.random),
      last_error: tooOld
        ? { code: "too_old", message: "This entry could not be sent for 72 hours. Contact head office.", retryable: false }
        : error,
    });
  }

  /** Reflect the server's answer on the local copy of the entry. */
  private async mirror(op: OutboxOp, result: PushResult, status: LocalDelivery["local_status"]): Promise<void> {
    const { store } = this.deps;
    const now = this.clock().toISOString();
    const existing = await store.getDelivery(op.entity_id);
    const record = result.record;
    await store.upsertDelivery({
      id: op.entity_id,
      payload: op.payload,
      local_status: status,
      server_status: record?.status ?? existing?.server_status ?? null,
      server_number: record?.delivery_number ?? existing?.server_number ?? null,
      open_flags: record
        ? record.flags
            .filter((f) => f.severity === "CRITICAL" || f.severity === "WARNING")
            .map((f) => ({ flag_type: f.type, severity: f.severity, message: f.message }))
        : (existing?.open_flags ?? []),
      review: existing?.review ?? null,
      last_error: result.error,
      created_at: existing?.created_at ?? op.created_at,
      updated_at: now,
    });
  }

  // -- Pull ---------------------------------------------------------------------------------------------------

  private async pull(report: SyncReport): Promise<void> {
    const { store, api } = this.deps;
    for (;;) {
      const since = await store.cursor();
      const page = await api.get<PullResponse>("/sync/pull", {
        since,
        limit: PULL_PAGE,
        device_id: this.deps.device.device_id,
      });
      await store.transaction(async () => {
        await store.applyReference(page.changes);
        await this.applyMyDeliveries(page.changes);
        await store.setCursor(page.server_seq);
      });
      report.pulled += page.changes.length;
      if (!page.has_more || page.changes.length === 0) return;
    }
  }

  /** What head office decided about the entries this phone sent: status, flags, reviewer's note. */
  private async applyMyDeliveries(changes: PullChange[]): Promise<void> {
    const { store } = this.deps;
    const now = this.clock().toISOString();
    for (const c of changes) {
      if (c.entity !== "my_deliveries") continue;
      const d = c.data as unknown as MyDelivery;
      const existing = await store.getDelivery(c.id);
      // An entry with an edit still waiting to go up is the person's latest word: leave it be.
      if (existing && (existing.local_status === "QUEUED" || existing.local_status === "SYNCING")) continue;
      const status: LocalDelivery["local_status"] = d.can_correct
        ? "NEEDS_CORRECTION"
        : existing?.local_status === "CONFLICT" || existing?.local_status === "REJECTED"
          ? existing.local_status
          : "SYNCED";
      await store.upsertDelivery({
        id: c.id,
        payload: existing?.payload ?? { ...d.entry, id: c.id },
        local_status: status,
        server_status: d.status,
        server_number: d.delivery_number,
        open_flags: d.open_flags,
        review: d.review,
        last_error: existing?.last_error ?? null,
        created_at: existing?.created_at ?? d.captured_at,
        updated_at: now,
      });
    }
  }
}

interface MyDelivery {
  delivery_number: string;
  status: string;
  can_correct: boolean;
  open_flags: LocalDelivery["open_flags"];
  review: LocalDelivery["review"];
  captured_at: string;
  entry: Omit<LocalDelivery["payload"], "id">;
}

/** As many due entries as fit, oldest first, within the byte budget: one always goes. */
export function fit(ops: OutboxOp[], maxBytes: number): OutboxOp[] {
  const out: OutboxOp[] = [];
  let bytes = 0;
  for (const op of ops) {
    const size = JSON.stringify(op.payload).length;
    if (out.length > 0 && bytes + size > maxBytes) break;
    out.push(op);
    bytes += size;
  }
  return out;
}
