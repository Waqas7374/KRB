/**
 * The shapes the app and the server agree on (docs/06 §3-4). Nothing here is a price, a
 * conversion factor or a rule outcome: the device never supplies those, so it can never
 * disagree with the server about them.
 */

export interface DeliveryLine {
  material_id: string;
  unit_id: string;
  /** A decimal string, up to 4 places: never a float. */
  quantity: string;
  remarks?: string | null;
}

/** A delivery as captured on the phone. `id` is generated here and is the idempotency key. */
export interface DeliveryDraft {
  id: string;
  site_id: string;
  vendor_id: string;
  purchase_order_id?: string | null;
  po_item_id?: string | null;
  truck_number?: string | null;
  truck_type_id?: string | null;
  driver_name?: string | null;
  driver_phone?: string | null;
  challan_number?: string | null;
  challan_date?: string | null;
  captured_at: string;
  latitude?: string | null;
  longitude?: string | null;
  gps_accuracy_m?: string | null;
  location_source: "GPS" | "MANUAL";
  remarks?: string | null;
  items: DeliveryLine[];
}

export type OpEntity = "delivery" | "delivery_correction";
export type OpKind = "create" | "update";

/**
 * PENDING  waiting to be sent (or to be sent again)
 * INFLIGHT in a request right now; on restart these are simply sent again, which is safe
 * DONE     the server has it (applied, a replay it already had, or a conflict it settled)
 * FAILED   the server had a transient problem with it; retried with backoff
 * DEAD     will not go without a person: the server refused it for good, or it is 72 h old
 */
export type OpStatus = "PENDING" | "INFLIGHT" | "DONE" | "FAILED" | "DEAD";

export interface OpError {
  code: string;
  message: string;
  retryable: boolean;
  fields?: { field: string; message: string }[];
}

export interface OutboxOp {
  op_id: string;
  entity: OpEntity;
  entity_id: string;
  op: OpKind;
  payload: DeliveryDraft;
  depends_on: string | null;
  status: OpStatus;
  attempt_count: number;
  next_attempt_at: string;
  last_error: OpError | null;
  created_at: string;
}

/** Where an entry stands from the person's point of view (docs/06 §8). */
export type LocalStatus =
  | "QUEUED"
  | "SYNCING"
  | "SYNCED"
  | "REJECTED"
  | "CONFLICT"
  | "NEEDS_CORRECTION";

export interface OpenFlag {
  flag_type: string;
  severity: string;
  message: string;
}

export interface ReviewNote {
  action: string;
  comments: string | null;
  reviewer_name: string | null;
  reviewed_at: string;
}

/** The phone's local mirror of one of its own deliveries. */
export interface LocalDelivery {
  id: string;
  payload: DeliveryDraft;
  local_status: LocalStatus;
  /** What head office says: UNDER_REVIEW, APPROVED, ... null until the server has answered. */
  server_status: string | null;
  server_number: string | null;
  open_flags: OpenFlag[];
  review: ReviewNote | null;
  last_error: OpError | null;
  created_at: string;
  updated_at: string;
}

// -- Wire ------------------------------------------------------------------------------------

export type PushOutcome = "applied" | "duplicate" | "conflict" | "rejected" | "deferred";

export interface PushRecord {
  id: string;
  delivery_number: string;
  status: string;
  flags: { type: string; severity: string; message: string }[];
}

export interface PushResult {
  op_id: string;
  outcome: PushOutcome;
  record: PushRecord | null;
  error: OpError | null;
}

export interface PushResponse {
  server_time: string;
  results: PushResult[];
}

export interface PullChange {
  entity: string;
  id: string;
  deleted: boolean;
  server_seq: number;
  data: Record<string, unknown>;
}

export interface PullResponse {
  server_time: string;
  server_seq: number;
  has_more: boolean;
  not_permitted: string[];
  changes: PullChange[];
}

/** What the sync badge shows (docs/06 §8). */
export interface SyncStatus {
  running: boolean;
  online: boolean | null;
  pending: number;
  needsAttention: number;
  lastSyncedAt: string | null;
  authRequired: boolean;
  deviceRevoked: boolean;
}
