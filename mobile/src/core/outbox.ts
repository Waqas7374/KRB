import { uuidv7 } from "./ids";
import type { LocalStore } from "./store";
import type { DeliveryDraft, LocalDelivery, OutboxOp } from "./types";
import { validateDraft, type Issue } from "./validate";

export class InvalidEntryError extends Error {
  constructor(readonly issues: Issue[]) {
    super(issues.map((i) => i.message).join("; "));
    this.name = "InvalidEntryError";
  }
}

/**
 * Saving an entry (docs/06 §2): it is written to the phone and the call returns. The network is
 * never on this path, so a save takes the same few milliseconds in a basement as on the roof.
 * An entry is only ever added here, never edited in place: a correction is a new operation.
 */
export class Outbox {
  constructor(
    private readonly store: LocalStore,
    private readonly clock: () => Date = () => new Date(),
    private readonly newId: () => string = () => uuidv7(),
  ) {}

  /** `input` has no id and no capture time unless given: both are set here. */
  async captureDelivery(
    input: Omit<DeliveryDraft, "id" | "captured_at"> & { captured_at?: string },
  ): Promise<{ deliveryId: string; opId: string }> {
    const now = this.clock();
    const draft: DeliveryDraft = {
      ...input,
      id: this.newId(),
      captured_at: input.captured_at ?? now.toISOString(),
    };
    const issues = validateDraft(draft);
    if (issues.length) throw new InvalidEntryError(issues);
    const opId = this.newId();
    await this.store.transaction(async () => {
      await this.store.upsertDelivery(queued(draft, now));
      await this.store.insertOp(pending(opId, "delivery", "create", draft, now));
    });
    return { deliveryId: draft.id, opId };
  }

  /** The person's answer to "please correct this": a new operation on the same delivery. */
  async submitCorrection(deliveryId: string, corrected: DeliveryDraft): Promise<string> {
    const now = this.clock();
    const draft: DeliveryDraft = { ...corrected, id: deliveryId };
    const issues = validateDraft(draft);
    if (issues.length) throw new InvalidEntryError(issues);
    const existing = await this.store.getDelivery(deliveryId);
    const opId = this.newId();
    await this.store.transaction(async () => {
      await this.store.upsertDelivery({
        ...queued(draft, now),
        server_number: existing?.server_number ?? null,
        server_status: existing?.server_status ?? null,
        created_at: existing?.created_at ?? now.toISOString(),
      });
      await this.store.insertOp(pending(opId, "delivery_correction", "update", draft, now));
    });
    return opId;
  }

  /**
   * An entry the server refused for good (a vendor deactivated since it was drafted, say) stays
   * in the queue for the person to fix. Fixing it puts the *same* operation back in line; the
   * server never recorded it, so the same id is still free.
   */
  async fixAndRetry(opId: string, fixed: DeliveryDraft): Promise<void> {
    const op = await this.store.getOp(opId);
    if (!op || op.status !== "DEAD") return;
    const draft: DeliveryDraft = { ...fixed, id: op.entity_id };
    const issues = validateDraft(draft);
    if (issues.length) throw new InvalidEntryError(issues);
    const now = this.clock();
    await this.store.transaction(async () => {
      await this.store.updateOp(opId, {
        status: "PENDING",
        attempt_count: 0,
        next_attempt_at: now.toISOString(),
        last_error: null,
        payload: draft,
      });
      const mirror = await this.store.getDelivery(op.entity_id);
      await this.store.upsertDelivery({
        ...queued(draft, now),
        created_at: mirror?.created_at ?? now.toISOString(),
      });
    });
  }

  /** Someone has read "this entry was already reviewed"; it goes back to being an ordinary entry. */
  async acknowledgeConflict(deliveryId: string): Promise<void> {
    const d = await this.store.getDelivery(deliveryId);
    if (d?.local_status !== "CONFLICT") return;
    await this.store.upsertDelivery({
      ...d,
      local_status: "SYNCED",
      last_error: null,
      updated_at: this.clock().toISOString(),
    });
  }
}

function pending(
  opId: string,
  entity: OutboxOp["entity"],
  op: OutboxOp["op"],
  draft: DeliveryDraft,
  now: Date,
): OutboxOp {
  return {
    op_id: opId,
    entity,
    entity_id: draft.id,
    op,
    payload: draft,
    depends_on: null,
    status: "PENDING",
    attempt_count: 0,
    next_attempt_at: now.toISOString(),
    last_error: null,
    created_at: now.toISOString(),
  };
}

function queued(draft: DeliveryDraft, now: Date): LocalDelivery {
  return {
    id: draft.id,
    payload: draft,
    local_status: "QUEUED",
    server_status: null,
    server_number: null,
    open_flags: [],
    review: null,
    last_error: null,
    created_at: now.toISOString(),
    updated_at: now.toISOString(),
  };
}
