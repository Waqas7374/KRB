import { useQueryClient } from "@tanstack/react-query";
import { Check, CornerUpLeft, Undo2, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import type { ApprovalDecisionResponse, ApprovalRequestRead } from "@/types/models";

type Dialog = null | "approve" | "reject" | "changes" | "recall";

/**
 * What the caller may do with a request right now comes from the server
 * (`can_decide`, `can_recall`), so this component never re-derives approval
 * rules. Reject and request-changes demand a reason, exactly as the API does.
 *
 * `onDone` runs after any successful action so the owning page can refetch
 * the document itself: an approval changes the document's status too.
 */
export function DecisionActions({
  request,
  label,
  onDone,
}: {
  request: ApprovalRequestRead;
  /** "PR-2026-27-00012" — names the action in confirm buttons. */
  label: string;
  onDone?: () => void;
}) {
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialog>(null);

  if (!request.can_decide && !request.can_recall) return null;

  const post = async (verb: string, comments: string) => {
    const result = await api.post<ApprovalDecisionResponse | ApprovalRequestRead>(
      `/approvals/requests/${request.id}/${verb}`,
      { comments: comments || null },
    );
    await queryClient.invalidateQueries();
    onDone?.();
    return result;
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-t border-border bg-surface px-4 py-2.5">
        {request.can_decide && (
          <>
            {request.decision_blocked_reason ? (
              <p role="note" className="basis-full text-sm text-warning">
                {request.decision_blocked_reason}
              </p>
            ) : (
              <Button variant="primary" onClick={() => setDialog("approve")}>
                <Check /> Approve
              </Button>
            )}
            <Button onClick={() => setDialog("changes")}>
              <CornerUpLeft /> Request changes
            </Button>
            <Button variant="danger" onClick={() => setDialog("reject")}>
              <X /> Reject
            </Button>
          </>
        )}
        {request.can_recall && (
          <Button onClick={() => setDialog("recall")}>
            <Undo2 /> Recall
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={dialog === "approve"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`Approve ${label}?`}
        description="Your approval is recorded against your name."
        confirmLabel={`Approve ${label}`}
        reason={{ label: "Comment (optional)" }}
        onConfirm={async (comments) => {
          const result = (await post("approve", comments)) as ApprovalDecisionResponse;
          toast.success(result.message ?? "Approved.");
        }}
      />
      <ConfirmDialog
        open={dialog === "changes"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`Return ${label} for changes?`}
        description="The author edits it and submits again; the new submission is routed afresh."
        confirmLabel="Return for changes"
        reason={{ label: "What needs to change", required: true }}
        onConfirm={async (comments) => {
          await post("request-changes", comments);
          toast.success("Returned to the author.");
        }}
      />
      <ConfirmDialog
        open={dialog === "reject"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`Reject ${label}?`}
        description="Rejection ends this approval. The author can still edit and submit it again."
        confirmLabel={`Reject ${label}`}
        destructive
        reason={{ label: "Reason", required: true }}
        onConfirm={async (comments) => {
          await post("reject", comments);
          toast.success("Rejected.");
        }}
      />
      <ConfirmDialog
        open={dialog === "recall"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`Recall ${label}?`}
        description="It goes back to draft. Only possible before anyone has approved a step."
        confirmLabel="Recall"
        reason={{ label: "Reason (optional)" }}
        onConfirm={async (comments) => {
          await post("recall", comments);
          toast.success("Recalled to draft.");
        }}
      />
    </>
  );
}
