import type {
  DeliveryDraft,
  LocalDelivery,
  LocalStatus,
  OpError,
  OpStatus,
  OutboxOp,
  PullChange,
} from "./types";

/**
 * The phone's local database (docs/06 §3). There is exactly one implementation, over this
 * small SQL interface, so the very same SQL runs on the device (expo-sqlite) and in the Node
 * tests (node:sqlite): what is tested is what ships.
 */
export type SqlValue = string | number | null;

export interface SqlDatabase {
  exec(sql: string): Promise<void>;
  run(sql: string, params?: SqlValue[]): Promise<void>;
  all<T>(sql: string, params?: SqlValue[]): Promise<T[]>;
  /** Everything inside commits together or not at all. Not re-entrant. */
  transaction<T>(fn: () => Promise<T>): Promise<T>;
}

const SCHEMA_VERSION = 1;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS deliveries (
  id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  local_status TEXT NOT NULL,
  server_status TEXT,
  server_number TEXT,
  open_flags TEXT NOT NULL DEFAULT '[]',
  review TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_deliveries_created ON deliveries (created_at);

CREATE TABLE IF NOT EXISTS outbox (
  op_id TEXT PRIMARY KEY,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  op TEXT NOT NULL,
  payload TEXT NOT NULL,
  depends_on TEXT,
  status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  last_error TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outbox_due ON outbox (status, next_attempt_at);

CREATE TABLE IF NOT EXISTS reference_data (
  entity TEXT NOT NULL,
  id TEXT NOT NULL,
  data TEXT NOT NULL,
  server_seq INTEGER NOT NULL,
  PRIMARY KEY (entity, id)
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
`;

const parse = <T>(value: string | null): T | null =>
  value === null ? null : (JSON.parse(value) as T);

interface OutboxRow {
  op_id: string;
  entity: string;
  entity_id: string;
  op: string;
  payload: string;
  depends_on: string | null;
  status: string;
  attempt_count: number;
  next_attempt_at: string;
  last_error: string | null;
  created_at: string;
}

interface DeliveryRow {
  id: string;
  payload: string;
  local_status: string;
  server_status: string | null;
  server_number: string | null;
  open_flags: string;
  review: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

const toOp = (r: OutboxRow): OutboxOp => ({
  op_id: r.op_id,
  entity: r.entity as OutboxOp["entity"],
  entity_id: r.entity_id,
  op: r.op as OutboxOp["op"],
  payload: JSON.parse(r.payload) as DeliveryDraft,
  depends_on: r.depends_on,
  status: r.status as OpStatus,
  attempt_count: r.attempt_count,
  next_attempt_at: r.next_attempt_at,
  last_error: parse<OpError>(r.last_error),
  created_at: r.created_at,
});

const toDelivery = (r: DeliveryRow): LocalDelivery => ({
  id: r.id,
  payload: JSON.parse(r.payload) as DeliveryDraft,
  local_status: r.local_status as LocalStatus,
  server_status: r.server_status,
  server_number: r.server_number,
  open_flags: JSON.parse(r.open_flags) as LocalDelivery["open_flags"],
  review: parse<NonNullable<LocalDelivery["review"]>>(r.review),
  last_error: parse<OpError>(r.last_error),
  created_at: r.created_at,
  updated_at: r.updated_at,
});

export type OpPatch = Partial<
  Pick<OutboxOp, "status" | "attempt_count" | "next_attempt_at" | "last_error" | "payload">
>;

export class LocalStore {
  constructor(private readonly db: SqlDatabase) {}

  async migrate(): Promise<void> {
    const [row] = await this.db.all<{ user_version: number }>("PRAGMA user_version");
    if ((row?.user_version ?? 0) >= SCHEMA_VERSION) return;
    await this.db.exec(SCHEMA);
    await this.db.exec(`PRAGMA user_version = ${SCHEMA_VERSION}`);
  }

  transaction<T>(fn: () => Promise<T>): Promise<T> {
    return this.db.transaction(fn);
  }

  // -- Outbox ----------------------------------------------------------------------------------

  async insertOp(op: OutboxOp): Promise<void> {
    await this.db.run(
      `INSERT INTO outbox (op_id, entity, entity_id, op, payload, depends_on, status,
         attempt_count, next_attempt_at, last_error, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        op.op_id,
        op.entity,
        op.entity_id,
        op.op,
        JSON.stringify(op.payload),
        op.depends_on,
        op.status,
        op.attempt_count,
        op.next_attempt_at,
        op.last_error ? JSON.stringify(op.last_error) : null,
        op.created_at,
      ],
    );
  }

  async getOp(opId: string): Promise<OutboxOp | null> {
    const [row] = await this.db.all<OutboxRow>("SELECT * FROM outbox WHERE op_id = ?", [opId]);
    return row ? toOp(row) : null;
  }

  async listOps(statuses?: OpStatus[]): Promise<OutboxOp[]> {
    const where = statuses?.length ? `WHERE status IN (${statuses.map(() => "?").join(",")})` : "";
    const rows = await this.db.all<OutboxRow>(
      `SELECT * FROM outbox ${where} ORDER BY created_at ASC, op_id ASC`,
      statuses ?? [],
    );
    return rows.map(toOp);
  }

  /** Oldest first, and never ahead of the entry it depends on (a photo after its delivery). */
  async dueOps(now: Date, limit: number): Promise<OutboxOp[]> {
    const rows = await this.db.all<OutboxRow>(
      `SELECT * FROM outbox
        WHERE status IN ('PENDING', 'FAILED') AND next_attempt_at <= ?
          AND (depends_on IS NULL
               OR depends_on IN (SELECT op_id FROM outbox WHERE status = 'DONE'))
        ORDER BY created_at ASC, op_id ASC
        LIMIT ?`,
      [now.toISOString(), limit],
    );
    return rows.map(toOp);
  }

  async updateOp(opId: string, patch: OpPatch): Promise<void> {
    const sets: string[] = [];
    const params: SqlValue[] = [];
    if (patch.status !== undefined) {
      sets.push("status = ?");
      params.push(patch.status);
    }
    if (patch.attempt_count !== undefined) {
      sets.push("attempt_count = ?");
      params.push(patch.attempt_count);
    }
    if (patch.next_attempt_at !== undefined) {
      sets.push("next_attempt_at = ?");
      params.push(patch.next_attempt_at);
    }
    if (patch.last_error !== undefined) {
      sets.push("last_error = ?");
      params.push(patch.last_error ? JSON.stringify(patch.last_error) : null);
    }
    if (patch.payload !== undefined) {
      sets.push("payload = ?");
      params.push(JSON.stringify(patch.payload));
    }
    if (sets.length === 0) return;
    await this.db.run(`UPDATE outbox SET ${sets.join(", ")} WHERE op_id = ?`, [...params, opId]);
  }

  /**
   * After a crash or a kill, anything left in flight is sent again. That is safe: the server
   * recognises an id it has already recorded and answers `duplicate`.
   */
  async recoverInflight(): Promise<number> {
    const [row] = await this.db.all<{ n: number }>(
      "SELECT COUNT(*) AS n FROM outbox WHERE status = 'INFLIGHT'",
    );
    await this.db.run("UPDATE outbox SET status = 'PENDING' WHERE status = 'INFLIGHT'");
    return row?.n ?? 0;
  }

  async counts(): Promise<{ pending: number; needsAttention: number }> {
    const [row] = await this.db.all<{ pending: number; dead: number }>(
      `SELECT SUM(CASE WHEN status IN ('PENDING','INFLIGHT','FAILED') THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN status = 'DEAD' THEN 1 ELSE 0 END) AS dead
         FROM outbox`,
    );
    return { pending: row?.pending ?? 0, needsAttention: row?.dead ?? 0 };
  }

  // -- Deliveries (the local mirror) ---------------------------------------------------------------

  async upsertDelivery(d: LocalDelivery): Promise<void> {
    await this.db.run(
      `INSERT INTO deliveries (id, payload, local_status, server_status, server_number, open_flags,
         review, last_error, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         payload = excluded.payload, local_status = excluded.local_status,
         server_status = excluded.server_status, server_number = excluded.server_number,
         open_flags = excluded.open_flags, review = excluded.review,
         last_error = excluded.last_error, updated_at = excluded.updated_at`,
      [
        d.id,
        JSON.stringify(d.payload),
        d.local_status,
        d.server_status,
        d.server_number,
        JSON.stringify(d.open_flags),
        d.review ? JSON.stringify(d.review) : null,
        d.last_error ? JSON.stringify(d.last_error) : null,
        d.created_at,
        d.updated_at,
      ],
    );
  }

  async getDelivery(id: string): Promise<LocalDelivery | null> {
    const [row] = await this.db.all<DeliveryRow>("SELECT * FROM deliveries WHERE id = ?", [id]);
    return row ? toDelivery(row) : null;
  }

  async listDeliveries(limit = 100): Promise<LocalDelivery[]> {
    const rows = await this.db.all<DeliveryRow>(
      "SELECT * FROM deliveries ORDER BY created_at DESC, id DESC LIMIT ?",
      [limit],
    );
    return rows.map(toDelivery);
  }

  // -- Reference data ---------------------------------------------------------------------------------

  /** Upserts and tombstones from one page of `GET /sync/pull`. Call inside a transaction. */
  async applyReference(changes: PullChange[]): Promise<void> {
    for (const c of changes) {
      if (c.entity === "my_deliveries") continue; // those are deliveries, not reference data
      if (c.deleted) {
        await this.db.run("DELETE FROM reference_data WHERE entity = ? AND id = ?", [
          c.entity,
          c.id,
        ]);
      } else {
        await this.db.run(
          `INSERT INTO reference_data (entity, id, data, server_seq) VALUES (?, ?, ?, ?)
           ON CONFLICT(entity, id) DO UPDATE SET data = excluded.data, server_seq = excluded.server_seq`,
          [c.entity, c.id, JSON.stringify(c.data), c.server_seq],
        );
      }
    }
  }

  async reference<T = Record<string, unknown>>(
    entity: string,
  ): Promise<{ id: string; data: T }[]> {
    const rows = await this.db.all<{ id: string; data: string }>(
      "SELECT id, data FROM reference_data WHERE entity = ? ORDER BY id",
      [entity],
    );
    return rows.map((r) => ({ id: r.id, data: JSON.parse(r.data) as T }));
  }

  async clearReference(): Promise<void> {
    await this.db.run("DELETE FROM reference_data");
    await this.setKv("cursor", "0");
  }

  // -- Key-value ------------------------------------------------------------------------------------------

  async getKv(key: string): Promise<string | null> {
    const [row] = await this.db.all<{ value: string }>("SELECT value FROM kv WHERE key = ?", [key]);
    return row?.value ?? null;
  }

  async setKv(key: string, value: string): Promise<void> {
    await this.db.run(
      "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      [key, value],
    );
  }

  async cursor(): Promise<number> {
    return Number((await this.getKv("cursor")) ?? "0");
  }

  async setCursor(seq: number): Promise<void> {
    await this.setKv("cursor", String(seq));
  }
}
