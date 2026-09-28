import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Ban, Check, ClipboardCheck, Download, Plus, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { AttachmentsSection } from "@/features/documents/AttachmentsSection";
import { api } from "@/lib/api";
import { downloadFile } from "@/lib/download";
import { describeError } from "@/lib/errors";
import { DECIMAL_RE } from "@/lib/forms";
import { usePagedList, useSiteOptions, useVendorOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney, formatQuantity, humanize } from "@/lib/utils";
import type { GrnItemRead, GrnListItem, GrnRead } from "@/types/models";

const STATUS_OPTIONS = ["DRAFT", "POSTED", "CANCELLED"].map((value) => ({
  value,
  label: humanize(value),
}));

const plain = (v: string | null | undefined) =>
  !v ? "" : v.includes(".") ? v.replace(/\.?0+$/, "") || "0" : v;

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<GrnListItem>();

export function GrnListPage() {
  const list = useListParams({ sort: "-created_at" });
  const query = usePagedList<GrnListItem>("grns", "/grns", list.query);
  const sites = useSiteOptions();
  const vendors = useVendorOptions();

  const columns = useMemo(
    () => [
      col.accessor("grn_number", {
        header: "GRN",
        meta: { sortKey: "grn_number", alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/grns/${c.row.original.id}`}
            className="font-mono text-primary hover:underline"
          >
            {c.getValue()}
          </Link>
        ),
      }),
      col.accessor("status", {
        header: "Status",
        meta: { sortKey: "status" },
        cell: (c) => <StatusBadge status={c.getValue()} />,
      }),
      col.accessor("inspection_result", {
        header: "Inspection",
        cell: (c) => humanize(c.getValue()),
      }),
      col.accessor("delivery_number", {
        header: "Delivery",
        cell: (c) =>
          c.getValue() ? (
            <Link
              to={`/deliveries/${c.row.original.delivery_id}`}
              className="font-mono text-primary hover:underline"
            >
              {c.getValue()}
            </Link>
          ) : c.row.original.is_counter_purchase ? (
            <span className="text-fg-muted">Counter · {c.row.original.counter_reference}</span>
          ) : (
            "—"
          ),
      }),
      col.accessor("vendor_name", { header: "Vendor", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("site_code", { header: "Site", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("warehouse_code", { header: "Warehouse", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("received_date", {
        header: "Received",
        meta: { sortKey: "received_date" },
        cell: (c) => formatDate(c.getValue()),
      }),
      col.accessor("net_amount", {
        header: "Value",
        meta: { numeric: true, sortKey: "net_amount" },
        cell: (c) =>
          c.getValue() == null ? (
            <span className="text-fg-subtle">Hidden</span>
          ) : (
            formatMoney(c.getValue(), { decimals: 0 })
          ),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Goods received"
        subtitle="What turns an approved delivery into stock. A GRN is drafted from a delivery, inspected, and posted — posting is the moment stock moves."
        actions={
          <PermissionGate permission="grn.create">
            <Button asChild>
              <Link to="/grns/new">
                <Plus /> Counter purchase
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="grns"
          caption="Goods received notes"
          columns={columns}
          rows={query.data?.items}
          total={query.data?.page.total}
          isLoading={query.isLoading}
          isFetching={query.isFetching}
          error={query.error}
          onRetry={() => void query.refetch()}
          sort={list.params.sort}
          onSortChange={list.setSort}
          offset={list.params.offset}
          limit={list.params.limit}
          onOffsetChange={list.setOffset}
          onLimitChange={list.setLimit}
          getRowId={(r) => r.id}
          getRowHref={(r) => `/grns/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="GRN number…  ( / )"
              savedViewsId="grns"
              filters={[
                { key: "status", label: "Status", options: STATUS_OPTIONS },
                { key: "site_id", label: "Site", options: sites.options },
                { key: "vendor_id", label: "Vendor", options: vendors.options },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Detail ----------------------------------------------------------------------

type Dialogs = null | "inspect" | "post" | "cancel";

export function GrnDetailPage() {
  const { grnId = "" } = useParams();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const canAttach = useCan("grn.create");

  const query = useQuery({
    queryKey: ["grns", "detail", grnId],
    queryFn: () => api.get<GrnRead>(`/grns/${grnId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  const reprice = useMutation({
    mutationFn: () => api.post<GrnRead>(`/grns/${grnId}/reprice`),
    onSuccess: async (saved) => {
      await refresh();
      toast[saved.has_unpriced_lines ? "error" : "success"](
        saved.has_unpriced_lines
          ? "Still no rate for some lines. Set the vendor's rate first."
          : "Priced.",
      );
    },
    onError: (err) => toast.error(describeError(err)),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const g = query.data;

  return (
    <>
      <PageHeader
        title={g.grn_number}
        crumbs={[{ label: "Goods received", to: "/grns" }, { label: g.grn_number }]}
        meta={
          <>
            <StatusBadge status={g.status} />
            <span className="text-sm text-fg-muted">
              {g.site_code} → {g.warehouse_code}
            </span>
            <span className="text-sm text-fg-muted">
              Inspection: {humanize(g.inspection_result)}
            </span>
            {!g.prices_hidden && g.net_amount && (
              <span className="text-sm text-fg-muted">
                Value{" "}
                <span className="tabular font-medium text-fg">{formatMoney(g.net_amount)}</span>
              </span>
            )}
          </>
        }
        actions={
          <>
            <Button
              onClick={() =>
                void downloadFile(`/grns/${g.id}/pdf`, `${g.grn_number}.pdf`).catch(
                  (err: unknown) => toast.error(describeError(err)),
                )
              }
            >
              <Download /> Download PDF
            </Button>
            {g.can_inspect && (
              <Button onClick={() => setDialog("inspect")}>
                <ClipboardCheck /> Record inspection
              </Button>
            )}
            {g.can_reprice && (
              <Button onClick={() => reprice.mutate()} loading={reprice.isPending}>
                <RefreshCw /> Price now
              </Button>
            )}
            {g.can_post && (
              <Button variant="primary" onClick={() => setDialog("post")}>
                <Check /> Post to stock
              </Button>
            )}
            {g.can_cancel && (
              <Button variant="danger" onClick={() => setDialog("cancel")}>
                <Ban /> Cancel
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {g.has_unpriced_lines && g.status === "DRAFT" && (
          <FormAlert>
            Some accepted lines have no price yet, so this GRN cannot be posted: stock is valued at
            what it cost. Set the vendor's rate, then choose <b>Price now</b>.
          </FormAlert>
        )}
        {g.cancel_reason && <FormAlert>Cancelled: {g.cancel_reason}</FormAlert>}

        <Section title="Lines">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Goods received, line by line</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Material
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Ordered
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Delivered
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Accepted
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Rejected
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Into stock
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {g.items.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{i.material_sku}</span>{" "}
                      <span className="font-medium">{i.material_name}</span>
                      {i.batch_no && <p className="text-xs text-fg-muted">Batch {i.batch_no}</p>}
                      {i.rejection_reason && (
                        <p className="text-xs text-warning">Rejected: {i.rejection_reason}</p>
                      )}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {i.ordered_quantity
                        ? formatQuantity(i.ordered_quantity, i.unit_code ?? undefined)
                        : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.delivered_quantity, i.unit_code ?? undefined)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.accepted_quantity, i.unit_code ?? undefined)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(i.rejected_quantity) ? formatQuantity(i.rejected_quantity) : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {i.base_quantity
                        ? formatQuantity(i.base_quantity, i.base_unit_code ?? undefined)
                        : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {g.prices_hidden ? (
                        <span className="text-fg-subtle">Hidden</span>
                      ) : i.amount ? (
                        formatMoney(i.amount)
                      ) : (
                        <span className="text-warning">Not priced</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section title="Details">
          <FieldGrid
            items={[
              {
                label: g.is_counter_purchase ? "Source" : "Delivery",
                value: g.delivery_id ? (
                  <Link
                    to={`/deliveries/${g.delivery_id}`}
                    className="font-mono text-primary hover:underline"
                  >
                    {g.delivery_number}
                  </Link>
                ) : g.is_counter_purchase ? (
                  `Counter purchase · bill ${g.counter_reference}`
                ) : null,
              },
              {
                label: "Purchase order",
                value: g.purchase_order_id ? (
                  <Link
                    to={`/purchase-orders/${g.purchase_order_id}`}
                    className="font-mono text-primary hover:underline"
                  >
                    {g.purchase_order_number}
                  </Link>
                ) : (
                  "None"
                ),
              },
              { label: "Vendor", value: g.vendor_name },
              { label: "Warehouse", value: `${g.warehouse_code} — ${g.warehouse_name}` },
              { label: "Received", value: formatDate(g.received_date) },
              { label: "Posted", value: g.posted_at ? formatDateTime(g.posted_at) : null },
              {
                label: "Journal entry",
                value: g.journal_entry_id ? (
                  <Link
                    to={`/finance/journal-entries/${g.journal_entry_id}`}
                    className="font-mono text-primary hover:underline"
                  >
                    View posting
                  </Link>
                ) : null,
              },
              { label: "Remarks", value: g.remarks, wide: true },
            ]}
          />
        </Section>
      </PageBody>

      <AttachmentsSection
        entityType="grn"
        entityId={g.id}
        canUpload={canAttach && g.status !== "CANCELLED"}
        documentTypes={
          g.is_counter_purchase ? ["BILL", "PHOTO", "OTHER"] : ["PHOTO", "BILL", "OTHER"]
        }
      />

      {dialog === "inspect" && <InspectionDialog grn={g} onClose={() => setDialog(null)} />}
      <ConfirmDialog
        open={dialog === "post"}
        onOpenChange={(o) => setDialog(o ? "post" : null)}
        title={`Post ${g.grn_number}?`}
        description="The accepted quantities go into stock at their priced cost, the order line records what arrived, and the delivery is marked received. Cancelling later reverses it by contra entries."
        confirmLabel={`Post ${g.grn_number}`}
        onConfirm={async () => {
          await api.post(`/grns/${g.id}/approve`, undefined, { ifMatch: String(g.version) });
          await refresh();
          toast.success(`${g.grn_number} posted to stock.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "cancel"}
        onOpenChange={(o) => setDialog(o ? "cancel" : null)}
        title={`Cancel ${g.grn_number}?`}
        description={
          g.status === "POSTED"
            ? "The stock is reversed with contra entries — nothing is deleted. It cannot be cancelled if the stock has since been used."
            : "The delivery returns to approved so it can be received again."
        }
        confirmLabel={`Cancel ${g.grn_number}`}
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/grns/${g.id}/cancel`, { reason });
          await refresh();
          toast.success(`${g.grn_number} cancelled.`);
        }}
      />
    </>
  );
}

// --- Inspection ------------------------------------------------------------------

interface LineState {
  accepted: string;
  reason: string;
  batch: string;
}

function InspectionDialog({ grn, onClose }: { grn: GrnRead; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [lines, setLines] = useState<Record<string, LineState>>(() =>
    Object.fromEntries(
      grn.items.map((i: GrnItemRead) => [
        i.id,
        {
          accepted: plain(i.accepted_quantity),
          reason: i.rejection_reason ?? "",
          batch: i.batch_no ?? "",
        },
      ]),
    ),
  );
  const [error, setError] = useState<string | null>(null);
  const set = (id: string, patch: Partial<LineState>) =>
    setLines((l) => ({ ...l, [id]: { ...l[id]!, ...patch } }));

  const save = useMutation({
    mutationFn: () => {
      const body = grn.items.map((i) => {
        const line = lines[i.id]!;
        if (
          !DECIMAL_RE.test(line.accepted) ||
          Number(line.accepted) > Number(i.delivered_quantity)
        ) {
          throw new Error(
            `Accepted quantity for ${i.material_name} must be between 0 and ${plain(i.delivered_quantity)}.`,
          );
        }
        return {
          grn_item_id: i.id,
          accepted_quantity: line.accepted,
          rejection_reason: line.reason.trim() || null,
          batch_no: line.batch.trim() || null,
        };
      });
      return api.patch<GrnRead>(
        `/grns/${grn.id}/inspection`,
        { lines: body },
        { ifMatch: String(grn.version) },
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries();
      toast.success("Inspection recorded.");
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      variant="drawer"
      title="Record inspection"
      description="How much of each line is taken. Whatever is not taken is rejected, and needs a reason."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => {
              setError(null);
              save.mutate();
            }}
          >
            Save inspection
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {grn.items.map((i) => {
          const line = lines[i.id]!;
          const rejected =
            DECIMAL_RE.test(line.accepted) && Number(line.accepted) < Number(i.delivered_quantity);
          return (
            <fieldset key={i.id} className="rounded-md border border-border p-3">
              <legend className="px-1 text-sm font-medium">{i.material_name}</legend>
              <p className="mb-2 text-xs text-fg-muted">
                {formatQuantity(i.delivered_quantity, i.unit_code ?? undefined)} delivered
              </p>
              <div className="grid grid-cols-2 gap-3">
                <label className="text-xs text-fg-muted">
                  Accepted
                  <Input
                    inputMode="decimal"
                    className="tabular mt-1"
                    aria-label={`${i.material_name} accepted quantity`}
                    value={line.accepted}
                    onChange={(e) => set(i.id, { accepted: e.target.value })}
                  />
                </label>
                <label className="text-xs text-fg-muted">
                  Batch
                  <Input
                    className="mt-1"
                    value={line.batch}
                    onChange={(e) => set(i.id, { batch: e.target.value })}
                  />
                </label>
              </div>
              {rejected && (
                <label className="mt-2 block text-xs text-fg-muted">
                  Why the rest was turned away
                  <Input
                    className="mt-1"
                    aria-label={`${i.material_name} rejection reason`}
                    value={line.reason}
                    onChange={(e) => set(i.id, { reason: e.target.value })}
                  />
                </label>
              )}
            </fieldset>
          );
        })}
        {error && (
          <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
