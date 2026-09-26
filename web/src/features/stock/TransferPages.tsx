import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Ban, PackageCheck, Plus, Truck } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router-dom";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import { emptyToNull } from "@/lib/forms";
import { usePagedList, useSiteOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, humanize } from "@/lib/utils";
import type { StockTransferCreate, StockTransferListItem, StockTransferRead } from "@/types/models";

import { emptyLine, lineSchema, useWarehousePicker } from "./stock-helpers";
import { LinesSection, MovementLinesTable } from "./stock-shared";

const STATUS_OPTIONS = ["DRAFT", "IN_TRANSIT", "RECEIVED", "CANCELLED"].map((value) => ({
  value,
  label: humanize(value),
}));

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<StockTransferListItem>();

export function TransferListPage() {
  const list = useListParams({ sort: "-created_at" });
  const query = usePagedList<StockTransferListItem>(
    "stock-transfers",
    "/inventory/transfers",
    list.query,
  );
  const sites = useSiteOptions();

  const columns = useMemo(
    () => [
      col.accessor("transfer_number", {
        header: "Transfer",
        meta: { sortKey: "transfer_number", alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/inventory/transfers/${c.row.original.id}`}
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
      col.display({
        id: "route",
        header: "From → to",
        cell: (c) => (
          <span>
            {c.row.original.from_warehouse_code} → {c.row.original.to_warehouse_code}
          </span>
        ),
      }),
      col.accessor("vehicle_number", { header: "Vehicle", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("transfer_date", {
        header: "Date",
        meta: { sortKey: "transfer_date" },
        cell: (c) => formatDate(c.getValue()),
      }),
      col.accessor("dispatched_at", {
        header: "Dispatched",
        cell: (c) => (c.getValue() ? formatDateTime(c.getValue()) : "—"),
      }),
      col.accessor("received_at", {
        header: "Received",
        cell: (c) => (c.getValue() ? formatDateTime(c.getValue()) : "—"),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Stock transfers"
        subtitle="Stock moving between stores, or between sites. It leaves the source when dispatched and is counted in when received — until then it is in transit."
        actions={
          <PermissionGate permission="inventory.transfer">
            <Button variant="primary" asChild>
              <Link to="/inventory/transfers/new">
                <Plus /> New transfer
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="stock-transfers"
          caption="Stock transfers"
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
          getRowHref={(r) => `/inventory/transfers/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Number or vehicle…  ( / )"
              savedViewsId="stock-transfers"
              filters={[
                { key: "status", label: "Status", options: STATUS_OPTIONS },
                { key: "site_id", label: "From site", options: sites.options },
                { key: "to_site_id", label: "To site", options: sites.options },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Form ------------------------------------------------------------------------

const schema = z
  .object({
    from_warehouse_id: z.string().min(1, "Choose where it leaves from"),
    to_warehouse_id: z.string().min(1, "Choose where it is going"),
    transfer_date: z.string(),
    vehicle_number: z.string().max(30),
    remarks: z.string().max(1000),
    dispatch_now: z.boolean(),
    lines: z.array(lineSchema).min(1, "Add at least one line"),
  })
  .refine((v) => v.from_warehouse_id !== v.to_warehouse_id, {
    path: ["to_warehouse_id"],
    message: "A transfer needs two different stores",
  });
type Values = z.infer<typeof schema>;

export function TransferFormPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const from = useWarehousePicker("transfer_from");
  const to = useWarehousePicker("transfer_to");
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      from_warehouse_id: "",
      to_warehouse_id: "",
      transfer_date: "",
      vehicle_number: "",
      remarks: "",
      dispatch_now: true,
      lines: [{ ...emptyLine }],
    },
  });
  const errors = form.formState.errors;

  const save = useMutation({
    mutationFn: async (values: Values) => {
      const body = {
        ...emptyToNull({
          from_warehouse_id: values.from_warehouse_id,
          to_warehouse_id: values.to_warehouse_id,
          transfer_date: values.transfer_date,
          vehicle_number: values.vehicle_number,
          remarks: values.remarks,
        }),
        lines: values.lines.map((l) => emptyToNull(l)),
      } as unknown as StockTransferCreate;
      const draft = await api.post<StockTransferRead>("/inventory/transfers", body);
      if (!values.dispatch_now) return draft;
      try {
        return await api.post<StockTransferRead>(`/inventory/transfers/${draft.id}/dispatch`);
      } catch (err) {
        // The draft exists; say so, rather than leaving it behind unmentioned.
        toast.error(
          `${draft.transfer_number} was saved as a draft but not dispatched: ${describeError(err)}`,
        );
        return draft;
      }
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(
        saved.status === "IN_TRANSIT"
          ? `${saved.transfer_number} dispatched — the stock is on its way.`
          : `${saved.transfer_number} saved as a draft. Nothing has left the store yet.`,
      );
      navigate(`/inventory/transfers/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "from_warehouse_id",
        "to_warehouse_id",
        "transfer_date",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title="New stock transfer"
        subtitle="Send stock to another store. It arrives at the cost it left at."
        crumbs={[{ label: "Stock transfers", to: "/inventory/transfers" }, { label: "New" }]}
      />
      <PageBody className="max-w-4xl">
        <form
          onSubmit={(e) => {
            setFormError(null);
            void form.handleSubmit((v) => save.mutate(v))(e);
          }}
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}
          <FormSection title="Route">
            <FormGrid>
              <FormField label="From" required error={errors.from_warehouse_id?.message}>
                <Select {...form.register("from_warehouse_id")}>
                  <option value="">Choose…</option>
                  {from.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="To" required error={errors.to_warehouse_id?.message}>
                <Select {...form.register("to_warehouse_id")}>
                  <option value="">Choose…</option>
                  {to.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Date" hint="Left blank, today.">
                <Input type="date" {...form.register("transfer_date")} />
              </FormField>
              <FormField label="Vehicle">
                <Input {...form.register("vehicle_number")} placeholder="e.g. LEA-4411" />
              </FormField>
            </FormGrid>
          </FormSection>

          <LinesSection form={form} error={errors.lines?.message} />

          <FormSection title="Notes">
            <FormField label="Remarks">
              <Textarea rows={2} {...form.register("remarks")} />
            </FormField>
            <label className="mt-3 flex items-center gap-2 text-sm">
              <input type="checkbox" {...form.register("dispatch_now")} />
              Dispatch now — the stock leaves the source store immediately
            </label>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {form.watch("dispatch_now") ? "Dispatch stock" : "Save draft"}
            </Button>
            <Button variant="ghost" onClick={() => navigate(-1)} disabled={save.isPending}>
              Cancel
            </Button>
          </div>
        </form>
      </PageBody>
    </>
  );
}

// --- Detail ----------------------------------------------------------------------

type Dialogs = null | "dispatch" | "receive" | "cancel";

export function TransferDetailPage() {
  const { transferId = "" } = useParams();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const query = useQuery({
    queryKey: ["stock-transfers", "detail", transferId],
    queryFn: () => api.get<StockTransferRead>(`/inventory/transfers/${transferId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const t = query.data;

  return (
    <>
      <PageHeader
        title={t.transfer_number}
        crumbs={[
          { label: "Stock transfers", to: "/inventory/transfers" },
          { label: t.transfer_number },
        ]}
        meta={
          <>
            <StatusBadge status={t.status} />
            <span className="text-sm text-fg-muted">
              {t.from_warehouse_code} → {t.to_warehouse_code}
            </span>
            {t.vehicle_number && (
              <span className="text-sm text-fg-muted">Vehicle {t.vehicle_number}</span>
            )}
          </>
        }
        actions={
          <>
            {t.can_dispatch && (
              <Button variant="primary" onClick={() => setDialog("dispatch")}>
                <Truck /> Dispatch
              </Button>
            )}
            {t.can_receive && (
              <Button variant="primary" onClick={() => setDialog("receive")}>
                <PackageCheck /> Receive
              </Button>
            )}
            {t.can_cancel && (
              <Button variant="danger" onClick={() => setDialog("cancel")}>
                <Ban /> Cancel
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {t.status === "IN_TRANSIT" && (
          <FormAlert>
            On its way. The stock has left {t.from_warehouse_code} and shows as in transit at{" "}
            {t.to_warehouse_code} until it is received.
          </FormAlert>
        )}
        {t.cancel_reason && <FormAlert>Cancelled: {t.cancel_reason}</FormAlert>}
        <Section title="Lines">
          <MovementLinesTable
            lines={t.items}
            hidden={t.prices_hidden}
            caption="Material transferred, line by line"
          />
        </Section>
        <Section title="Details">
          <FieldGrid
            items={[
              { label: "From", value: `${t.from_warehouse_code} (${t.site_code})` },
              { label: "To", value: `${t.to_warehouse_code} (${t.to_site_code})` },
              { label: "Date", value: formatDate(t.transfer_date) },
              { label: "Vehicle", value: t.vehicle_number },
              {
                label: "Dispatched",
                value: t.dispatched_at ? formatDateTime(t.dispatched_at) : null,
              },
              { label: "Received", value: t.received_at ? formatDateTime(t.received_at) : null },
              { label: "Remarks", value: t.remarks, wide: true },
            ]}
          />
        </Section>
      </PageBody>

      <ConfirmDialog
        open={dialog === "dispatch"}
        onOpenChange={(o) => setDialog(o ? "dispatch" : null)}
        title={`Dispatch ${t.transfer_number}?`}
        description="The stock leaves the source store now, at its current average cost, and shows as in transit at the destination."
        confirmLabel="Dispatch"
        onConfirm={async () => {
          await api.post(`/inventory/transfers/${t.id}/dispatch`);
          await refresh();
          toast.success(`${t.transfer_number} dispatched.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "receive"}
        onOpenChange={(o) => setDialog(o ? "receive" : null)}
        title={`Receive ${t.transfer_number}?`}
        description="Confirm the goods arrived. They are counted into this store at the cost they left at."
        confirmLabel="Receive"
        onConfirm={async () => {
          await api.post(`/inventory/transfers/${t.id}/receive`);
          await refresh();
          toast.success(`${t.transfer_number} received.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "cancel"}
        onOpenChange={(o) => setDialog(o ? "cancel" : null)}
        title={`Cancel ${t.transfer_number}?`}
        description={
          t.status === "IN_TRANSIT"
            ? "The stock goes back to the source store at the cost it left at. Nothing is deleted."
            : "The draft is closed. Nothing has left the store."
        }
        confirmLabel={`Cancel ${t.transfer_number}`}
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/inventory/transfers/${t.id}/cancel`, { reason });
          await refresh();
          toast.success(`${t.transfer_number} cancelled.`);
        }}
      />
    </>
  );
}
