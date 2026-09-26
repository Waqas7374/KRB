import { type ReactNode } from "react";

import { cn, humanize } from "@/lib/utils";

type Tone = "neutral" | "success" | "warning" | "danger" | "info";

/**
 * Status is a dot plus a label, never colour alone (docs/08 §3). Unknown
 * statuses render neutral rather than guessing a tone.
 */
const TONES: Record<string, Tone> = {
  ACTIVE: "success",
  APPROVED: "success",
  COMPLETED: "success",
  APPLIED: "success",
  DRAFT: "neutral",
  NOT_STARTED: "neutral",
  INACTIVE: "neutral",
  CLOSED: "neutral",
  PENDING: "warning",
  PENDING_APPROVAL: "warning",
  ON_HOLD: "warning",
  INVITED: "info",
  IN_PROGRESS: "info",
  SUSPENDED: "danger",
  BLACKLISTED: "danger",
  LOCKED: "danger",
  CANCELLED: "danger",
  DISCARDED: "neutral",
  ISSUED: "info",
  POSTED: "success",
  IN_TRANSIT: "warning",
  SUBMITTED: "info",
  UNDER_REVIEW: "warning",
  CRITICAL: "danger",
  WARNING: "warning",
  INFO: "info",
  OPEN: "warning",
  WAIVED: "neutral",
  CORRECTED: "neutral",
  SENT: "info",
  SHORTLISTED: "info",
  RECEIVED: "info",
  PARTIALLY_RECEIVED: "info",
  PARTIALLY_SOURCED: "info",
  ACKNOWLEDGED: "success",
  QUOTED: "success",
  SELECTED: "success",
  SOURCED: "success",
  REJECTED: "danger",
  DECLINED: "danger",
  CHANGES_REQUESTED: "warning",
  DEACTIVATED: "neutral",
};

const toneClass: Record<Tone, { dot: string; text: string }> = {
  neutral: { dot: "bg-fg-subtle", text: "text-fg-muted" },
  success: { dot: "bg-success", text: "text-success" },
  warning: { dot: "bg-warning", text: "text-warning" },
  danger: { dot: "bg-danger", text: "text-danger" },
  info: { dot: "bg-info", text: "text-info" },
};

export function StatusBadge({ status, tone }: { status: string; tone?: Tone }) {
  const t = toneClass[tone ?? TONES[status] ?? "neutral"];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm font-medium", t.text)}>
      <span className={cn("size-1.5 shrink-0 rounded-full", t.dot)} aria-hidden />
      {humanize(status)}
    </span>
  );
}

/** A small neutral tag for types and codes (not status). */
export function Tag({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border border-border bg-surface px-1.5 py-px text-xs text-fg-muted",
        className,
      )}
    >
      {children}
    </span>
  );
}
