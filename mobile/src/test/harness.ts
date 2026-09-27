import { ApiClient, type Fetch } from "../core/api";
import { Outbox } from "../core/outbox";
import { Session, type DeviceInfo, type SecureStorage } from "../core/session";
import { LocalStore, type SqlDatabase } from "../core/store";
import { SyncEngine, type SyncReport } from "../core/sync";
import type { DeliveryDraft } from "../core/types";
import { nodeDb } from "./node-db";

export class MemorySecureStorage implements SecureStorage {
  values = new Map<string, string>();
  get = (key: string) => Promise.resolve(this.values.get(key) ?? null);
  set = (key: string, value: string) => {
    this.values.set(key, value);
    return Promise.resolve();
  };
  delete = (key: string) => {
    this.values.delete(key);
    return Promise.resolve();
  };
}

export const DEVICE: DeviceInfo = {
  device_uid: "test-phone-0001",
  platform: "ANDROID",
  app_version: "1.0.0",
};

/** A phone: its database, secure storage, session, outbox and sync engine, on a controllable clock. */
export class Phone {
  now = new Date("2026-09-27T08:00:00Z");
  readonly secure = new MemorySecureStorage();
  readonly store: LocalStore;
  readonly session: Session;
  readonly api: ApiClient;
  readonly outbox: Outbox;
  engine: SyncEngine;
  private counter = 0;
  /** Sequential and predictable for unit tests; a live run swaps in real UUIDv7s. */
  idFactory: () => string = () => this.nextId();

  constructor(
    readonly fetchImpl: Fetch,
    readonly db: SqlDatabase = nodeDb(),
    baseUrl = "http://server.test/api/v1",
    readonly device: DeviceInfo = DEVICE,
  ) {
    this.store = new LocalStore(db);
    this.session = new Session(baseUrl, this.secure, device, fetchImpl);
    this.api = new ApiClient({
      baseUrl,
      fetch: fetchImpl,
      accessToken: this.session.accessToken,
      refresh: this.session.refresh,
    });
    // Deterministic ids and no jitter: tests assert order and timing.
    this.outbox = new Outbox(this.store, () => this.now, () => this.idFactory());
    this.engine = this.newEngine();
  }

  newEngine(): SyncEngine {
    return new SyncEngine({
      api: this.api,
      store: this.store,
      device: {
        device_id: this.device.device_uid,
        app_version: this.device.app_version,
        platform: this.device.platform,
      },
      clock: () => this.now,
      random: () => 0.5, // jitter factor exactly 1.0
    });
  }

  async start(): Promise<void> {
    await this.store.migrate();
  }

  /** Time passes; each capture also moves the clock so created_at orders strictly. */
  tick(seconds = 1): void {
    this.now = new Date(this.now.getTime() + seconds * 1000);
  }

  private nextId(): string {
    this.counter++;
    return `00000000-0000-7000-8000-${String(this.counter).padStart(12, "0")}`;
  }

  draft(n: number, extra: Partial<DeliveryDraft> = {}): Omit<DeliveryDraft, "id" | "captured_at"> {
    return {
      site_id: "site-1",
      vendor_id: "vendor-1",
      truck_number: `LEB-${1000 + n}`,
      location_source: "GPS",
      latitude: "31.4110000",
      longitude: "74.2461000",
      gps_accuracy_m: "8.0",
      items: [{ material_id: "mat-1", unit_id: "unit-1", quantity: "12.5" }],
      ...extra,
    };
  }

  async capture(count: number, extra: Partial<DeliveryDraft> = {}): Promise<string[]> {
    const ids: string[] = [];
    for (let i = 0; i < count; i++) {
      this.tick();
      ids.push((await this.outbox.captureDelivery(this.draft(i, extra))).deliveryId);
    }
    return ids;
  }

  async signIn(): Promise<void> {
    await this.session.login("staff", "right");
  }

  sync(): Promise<SyncReport> {
    return this.engine.run();
  }
}
