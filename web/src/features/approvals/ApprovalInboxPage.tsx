import { createColumnHelper } from "@tanstack/react-table";
import { Clock } from "lucide-react";
import { Link } from "react-router-dom";

import { DataTable } from "@/components/data-table/data-table";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { EmptyState } from "@/components/ui/states";
import { Tag } from "@/components/ui/status-badge";
import { usePagedList } from "@/lib/queries";
import { formatDateTime, formatMoney } from "@/lib/utils";
import type { ApprovalInboxItem } from "@/types/models";

const col = createColumnHelper<ApprovalInboxItem>();

const columns = [
  col.accessor("doc_number", {
    header: "Document",
    meta: { alwaysVisible: true, csv: (r) => r.doc_number },
    cell: (c) => (
      <Link to={c.row.original.link_path ?? "#"} className="font-mono text-primary hover:underline">
        {c.getValue() ?? "—"}
      </Link>
    ),
  }),
  col.accessor("doc_label", { header: "Type" }),
  col.accessor("doc_summary", {
    header: "Summary",
    cell: (c) => <span className="line-clamp-1 max-w-md">{c.getValue() ?? ""}</span>,
  }),
  col.accessor("amount", {
    header: "Amount",
    meta: { numeric: true },
    cell: (c) =>
      formatMoney(c.getValue(), {
        currency: c.row.original.currency_code ?? undefined,
        decimals: 0,
      }),
  }),
  col.accessor("initiated_by_name", { header: "From", cell: (c) => c.getValue() ?? "—" }),
  col.display({
    id: "step",
    header: "Your step",
    meta: { csv: (r) => `${r.step_no}/${r.total_steps} ${r.step_name}` },
    cell: (c) => (
      <span>
        {c.row.original.step_name}{" "}
        <span className="text-fg-subtle">
          ({c.row.original.step_no}/{c.row.original.total_steps})
        </span>
      </span>
    ),
  }),
  col.accessor("due_at", {
    header: "Due",
    cell: (c) => {
      const row = c.row.original;
      return (
        <span
          className={
            row.is_overdue ? "inline-flex items-center gap-1 font-medium text-danger" : undefined
          }
        >
          {row.is_overdue && <Clock className="size-3" aria-hidden />}
          {c.getValue() ? formatDateTime(c.getValue()) : "—"}
          {row.escalated && <Tag className="ml-1.5">Escalated</Tag>}
        </span>
      );
    },
  }),
];

/**
 * One screen, one query (docs/04 §2): every pending step the signed-in user
 * can decide, across all document types, most urgent first. Row → the
 * document, where the trail and the decision buttons live.
 */
export function ApprovalInboxPage() {
  const list = useListParams({ limit: 50 });
  // The inbox is ordered by urgency server-side; it takes no sort parameter.
  const query = usePagedList<ApprovalInboxItem>("approvals-inbox", "/approvals/inbox", {
    offset: list.params.offset,
    limit: list.params.limit,
  });

  return (
    <>
      <PageHeader
        title="Approvals"
        subtitle="Everything waiting for your decision, most urgent first."
      />
      <PageBody>
        <DataTable
          tableId="approvals-inbox"
          caption="Pending approvals"
          columns={columns}
          rows={query.data?.items}
          total={query.data?.page.total}
          isLoading={query.isLoading}
          isFetching={query.isFetching}
          error={query.error}
          onRetry={() => void query.refetch()}
          offset={list.params.offset}
          limit={list.params.limit}
          onOffsetChange={list.setOffset}
          onLimitChange={list.setLimit}
          getRowId={(r) => r.request_id}
          getRowHref={(r) => r.link_path ?? "/approvals"}
          empty={
            <EmptyState
              title="Nothing is waiting for you"
              description="When a document reaches a step you can approve, it appears here and you are notified."
            />
          }
        />
      </PageBody>
    </>
  );
}
