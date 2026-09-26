import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Section } from "@/components/layout/page";
import { ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import type { ApprovalRequestRead } from "@/types/models";

import { ApprovalTrail } from "./ApprovalTrail";
import { DecisionActions } from "./DecisionActions";

/**
 * The approval block a document's own page hosts: the chain, each decision, and
 * the buttons for whoever may decide it now. Documents without a page of their
 * own use ApprovalDocumentPage, which shows the same thing.
 */
export function ApprovalSection({
  docType,
  docId,
  label,
  empty = "Not submitted yet. When it is, the approval chain and each decision appear here.",
}: {
  docType: string;
  docId: string;
  label: string;
  empty?: string;
}) {
  const queryClient = useQueryClient();
  const trail = useQuery({
    queryKey: ["approvals", "trail", docType, docId],
    queryFn: () =>
      api.get<ApprovalRequestRead[]>("/approvals/requests", {
        query: { doc_type: docType, doc_id: docId },
      }),
  });
  const [current, ...earlier] = trail.data ?? [];

  return (
    <Section title="Approval">
      {trail.isLoading ? (
        <p className="px-4 py-4 text-sm text-fg-muted">Loading…</p>
      ) : trail.isError ? (
        <ErrorState error={trail.error} onRetry={() => void trail.refetch()} />
      ) : !current ? (
        <p className="px-4 py-4 text-sm text-fg-muted">{empty}</p>
      ) : (
        <>
          <ApprovalTrail request={current} />
          <DecisionActions
            request={current}
            label={label}
            onDone={() => void queryClient.invalidateQueries()}
          />
          {earlier.length > 0 && (
            <details className="border-t border-border">
              <summary className="cursor-pointer px-4 py-2 text-xs text-fg-muted">
                {earlier.length} earlier attempt{earlier.length === 1 ? "" : "s"}
              </summary>
              {earlier.map((r) => (
                <div key={r.id} className="border-t border-border">
                  <ApprovalTrail request={r} />
                </div>
              ))}
            </details>
          )}
        </>
      )}
    </Section>
  );
}
