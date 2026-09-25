import { CheckCircle2, Circle, CircleDot, Clock, XCircle } from "lucide-react";

import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { formatDateTime, humanize } from "@/lib/utils";
import type { ApprovalRequestRead, ApprovalStepRead } from "@/types/models";
import { cn } from "@/lib/utils";

/**
 * The approval trail (docs/08 §4): the chain a document was routed through,
 * who is able to decide each step, and everything that happened, in order.
 * Rendered from the request's frozen snapshot, so it always shows the rules
 * that applied when the document was submitted — not today's workflow.
 */

function StepIcon({ step }: { step: ApprovalStepRead }) {
  const base = "size-4 shrink-0";
  if (step.status === "APPROVED")
    return <CheckCircle2 className={cn(base, "text-success")} aria-hidden />;
  if (step.status === "REJECTED")
    return <XCircle className={cn(base, "text-danger")} aria-hidden />;
  if (step.status === "PENDING")
    return (
      <CircleDot
        className={cn(base, step.is_overdue ? "text-danger" : "text-primary")}
        aria-hidden
      />
    );
  return <Circle className={cn(base, "text-fg-subtle")} aria-hidden />;
}

function StepRow({ step }: { step: ApprovalStepRead }) {
  return (
    <li className="flex gap-3 px-4 py-2.5">
      <StepIcon step={step} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="font-medium">
            {step.step_no}. {step.name}
          </span>
          <StatusBadge status={step.status} />
          {step.quorum_required > 1 && (
            <Tag>
              {step.approvals_count}/{step.quorum_required} approvals
            </Tag>
          )}
          {step.is_overdue && (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-danger">
              <Clock className="size-3" aria-hidden /> Overdue
            </span>
          )}
          {step.escalated_at && <Tag>Escalated</Tag>}
        </div>
        {step.approvers.length > 0 && (
          <p className="mt-0.5 text-sm text-fg-muted">
            {step.approvers.map((a, i) => (
              <span key={a.user_id}>
                {i > 0 && ", "}
                <span className={a.has_approved ? "text-success" : undefined}>
                  {a.full_name ?? "Unknown"}
                  {a.has_approved && " ✓"}
                </span>
                {a.source === "ESCALATION" && " (escalation)"}
              </span>
            ))}
          </p>
        )}
        {step.status === "PENDING" && step.due_at && (
          <p className="mt-0.5 text-xs text-fg-subtle">Due {formatDateTime(step.due_at)}</p>
        )}
      </div>
    </li>
  );
}

export function ApprovalTrail({ request }: { request: ApprovalRequestRead }) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-4 py-2 text-sm">
        <StatusBadge status={request.status} />
        <span className="text-fg-muted">
          Submitted {formatDateTime(request.submitted_at)}
          {request.initiated_by_name && ` by ${request.initiated_by_name}`}
        </span>
        <span className="text-fg-subtle">
          {request.workflow_name} v{request.workflow_version} · {request.rule_name}
        </span>
      </div>
      {request.outcome_reason && (
        <p className="border-b border-border px-4 py-2 text-sm">
          <span className="text-fg-muted">{humanize(request.status)}: </span>
          {request.outcome_reason}
        </p>
      )}
      <ol className="divide-y divide-border" aria-label="Approval steps">
        {request.steps.map((step) => (
          <StepRow key={step.step_no} step={step} />
        ))}
      </ol>
      <details className="border-t border-border">
        <summary className="cursor-pointer px-4 py-2 text-xs text-fg-muted">
          History ({request.actions.length})
        </summary>
        <ul className="divide-y divide-border">
          {request.actions.map((a) => (
            <li key={a.id} className="px-4 py-2 text-sm">
              <span className="font-medium">{humanize(a.action)}</span>
              {a.step_no && <span className="text-fg-muted"> · step {a.step_no}</span>}
              <span className="text-fg-muted">
                {" "}
                · {a.actor_name ?? "System"} · {formatDateTime(a.acted_at)}
              </span>
              {a.comments && <p className="mt-0.5 text-fg-muted">{a.comments}</p>}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
