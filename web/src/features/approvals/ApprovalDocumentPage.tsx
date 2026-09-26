import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { PageBody, PageHeader, Section } from "@/components/layout/page";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { formatDateTime, formatMoney, humanize } from "@/lib/utils";
import type { ApprovalRequestRead } from "@/types/models";

import { ApprovalTrail } from "./ApprovalTrail";
import { DecisionActions } from "./DecisionActions";

/**
 * The approval for a document that has no screen of its own (a vendor rate
 * change, for one): what is being asked, the chain, and the decision buttons.
 * Documents with their own detail page host the same trail there instead.
 */
export function ApprovalDocumentPage() {
  const { docType = "", docId = "" } = useParams();
  const queryClient = useQueryClient();
  const trail = useQuery({
    queryKey: ["approvals", "trail", docType, docId],
    queryFn: () =>
      api.get<ApprovalRequestRead[]>("/approvals/requests", {
        query: { doc_type: docType, doc_id: docId },
      }),
  });

  if (trail.isLoading) return <PageSkeleton />;
  if (trail.error) return <ErrorState error={trail.error} onRetry={() => void trail.refetch()} />;
  const [current, ...earlier] = trail.data ?? [];
  if (!current) {
    return <ErrorState error={new Error("There is no approval request for that document.")} />;
  }
  const label = current.doc_number ?? humanize(docType);

  return (
    <>
      <PageHeader
        title={label}
        subtitle={current.doc_summary ?? undefined}
        crumbs={[{ label: "Approvals", to: "/approvals" }, { label }]}
        meta={
          <>
            <StatusBadge status={current.status} />
            <span className="text-sm text-fg-muted">{humanize(docType)}</span>
            {current.amount != null && (
              <span className="tabular text-sm">
                {formatMoney(current.amount, { currency: current.currency_code ?? undefined })}
              </span>
            )}
            <span className="text-sm text-fg-muted">
              Submitted by {current.initiated_by_name ?? "—"} ·{" "}
              {formatDateTime(current.submitted_at)}
            </span>
          </>
        }
      />
      <PageBody className="max-w-4xl">
        <Section title="Approval">
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
        </Section>
        {docType === "vendor_rate" && (
          <p className="text-sm text-fg-muted">
            <Link to="/vendor-rates" className="text-primary hover:underline">
              Back to vendor rates
            </Link>
          </p>
        )}
      </PageBody>
    </>
  );
}
