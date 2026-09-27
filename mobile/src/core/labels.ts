import type { LocalDelivery } from "./types";

/**
 * What the person is told about an entry (docs/06 §8). The wording never claims more than is
 * true: an entry that has not reached the server is "saved on device", never "recorded".
 */
export type Tone = "neutral" | "good" | "warn" | "bad";

export interface StatusLabel {
  text: string;
  tone: Tone;
  /** Something the person can do about it. */
  action: "correct" | "fix" | "acknowledge" | null;
}

export function statusLabel(d: LocalDelivery): StatusLabel {
  switch (d.local_status) {
    case "QUEUED":
      return { text: "Saved on device", tone: "neutral", action: null };
    case "SYNCING":
      return { text: "Syncing…", tone: "neutral", action: null };
    case "REJECTED":
      return { text: "Not accepted: fix and resend", tone: "bad", action: "fix" };
    case "CONFLICT":
      return { text: "Already reviewed at head office", tone: "warn", action: "acknowledge" };
    case "NEEDS_CORRECTION":
      return { text: "Needs correction", tone: "warn", action: "correct" };
    case "SYNCED":
      break;
  }
  switch (d.server_status) {
    case "APPROVED":
    case "RECEIVED":
    case "PARTIALLY_RECEIVED":
      return { text: "Approved", tone: "good", action: null };
    case "REJECTED":
      return { text: "Rejected", tone: "bad", action: null };
    case "UNDER_REVIEW":
      return { text: "Under review", tone: "warn", action: null };
    case "SUBMITTED":
      return { text: "Recorded", tone: "good", action: null };
    default:
      return { text: "Recorded", tone: "good", action: null };
  }
}

/** The line shown right after Save: true offline, true online, and honest about flags. */
export function saveMessage(opts: { online: boolean; number?: string | null; flagged?: string | null }): string {
  if (!opts.online || !opts.number) return "Saved on device. It will sync when you are online.";
  return opts.flagged
    ? `Delivery ${opts.number} recorded. Flagged for review: ${opts.flagged}`
    : `Delivery ${opts.number} recorded.`;
}
