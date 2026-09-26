import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Ban, Pencil, Plus, Send, Trash2, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
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
import { ApprovalSection } from "@/features/approvals/ApprovalSection";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api, type Page } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import { useMaterialOptions, usePagedList, useSiteOptions, useUnitOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney, formatQuantity, humanize } from "@/lib/utils";
import type {
  StockAdjustmentCreate,
  StockAdjustmentListItem,
  StockAdjustmentRead,
  StockBalance,
} from "@/types/models";

import { plain, useWarehousePicker } from "./stock-helpers";

const REASONS = [
  "COUNT_CORRECTION",
  "DAMAGE",
  "WASTAGE",
  "LOSS",
  "THEFT",
  "EXPIRED",
  "OPENING_BALANCE",
  "OTHER",
];
const STATUS_OPTIONS = ["DRAFT", "PENDING_APPROVAL", "POSTED", "REJECTED", "CANCELLED"].map(
  (value) => ({ value, label: humanize(value) }),
);
const REASON_OPTIONS = REASONS.map((value) => ({ value, label: humanize(value) }));

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<StockAdjustmentListItem>();

export function AdjustmentListPage() {
  const list = useListParams({ sort: "-created_at" });
  const query = usePagedList<StockAdjustmentListItem>(
    "stock-adjustments",
    "/inventory/adjustments",
    list.query,
  );
  const sites = useSiteOptions();

  const columns = useMemo(
    () => [
      col.accessor("adjustment_number", {
        header: "Adjustment",
        meta: { sortKey: "adjustment_number", alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/inventory/adjustments/${c.row.original.id}`}
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
      col.accessor("reason_code", { header: "Reason", cell: (c) => humanize(c.getValue()) }),
      col.accessor("warehouse_code", { header: "Store", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("site_code", { header: "Site", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("adjustment_date", {
        header: "Date",
        meta: { sortKey: "adjustment_date" },
        cell: (c) => formatDate(c.getValue()),
      }),
      col.accessor("posted_at", {
        header: "Posted",
        cell: (c) => (c.getValue() ? formatDateTime(c.getValue()) : "—"),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Stock adjustments"
        subtitle="Correcting what the books say is on the shelf. Every adjustment needs a reason and is signed before it moves any stock."
        actions={
          <PermissionGate permission="inventory.adjust">
            <Button variant="primary" asChild>
              <Link to="/inventory/adjustments/new">
                <Plus /> New adjustment
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="stock-adjustments"
          caption="Stock adjustments"
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
          getRowHref={(r) => `/inventory/adjustments/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Number or note…  ( / )"
              savedViewsId="stock-adjustments"
              filters={[
                { key: "status", label: "Status", options: STATUS_OPTIONS },
                { key: "reason_code", label: "Reason", options: REASON_OPTIONS },
                { key: "site_id", label: "Site", options: sites.options },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Form ------------------------------------------------------------------------

const lineSchema = z
  .object({
    material_id: z.string().min(1, "Choose a material"),
    direction: z.enum(["decrease", "increase"]),
    quantity: z
      .string()
      .regex(DECIMAL_RE, "A number, up to 4 decimals")
      .refine((v) => Number(v) > 0, "More than zero"),
    unit_cost: z.string(),
    remarks: z.string().max(300),
  })
  .refine((l) => l.unit_cost === "" || /^\d+(\.\d{1,6})?$/.test(l.unit_cost), {
    path: ["unit_cost"],
    message: "A number, up to 6 decimals",
  });

const schema = z.object({
  warehouse_id: z.string().min(1, "Choose the store"),
  reason_code: z.string().min(1, "Choose a reason"),
  reason_note: z.string().trim().min(5, "Explain what happened, in a few words").max(500),
  adjustment_date: z.string(),
  lines: z.array(lineSchema).min(1, "Add at least one line"),
});
type Values = z.infer<typeof schema>;

const emptyLine = {
  material_id: "",
  direction: "decrease" as const,
  quantity: "",
  unit_cost: "",
  remarks: "",
};

export function AdjustmentFormPage() {
  const { adjustmentId } = useParams();
  const existing = useQuery({
    queryKey: ["stock-adjustments", "detail", adjustmentId],
    queryFn: () => api.get<StockAdjustmentRead>(`/inventory/adjustments/${adjustmentId}`),
    enabled: Boolean(adjustmentId),
    staleTime: 0,
  });
  if (adjustmentId && existing.isLoading) return <PageSkeleton />;
  if (adjustmentId && existing.error) {
    return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />;
  }
  return <AdjustmentForm adjustment={existing.data} />;
}

function AdjustmentForm({ adjustment }: { adjustment?: StockAdjustmentRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const stores = useWarehousePicker("adjust");
  const materials = useMaterialOptions();
  const units = useUnitOptions();
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: adjustment
      ? {
          warehouse_id: adjustment.warehouse_id,
          reason_code: adjustment.reason_code,
          reason_note: adjustment.reason_note,
          adjustment_date: adjustment.adjustment_date,
          lines: adjustment.items.map((i) => ({
            material_id: i.material_id,
            direction: Number(i.quantity_delta) < 0 ? ("decrease" as const) : ("increase" as const),
            quantity: plain(String(Math.abs(Number(i.quantity_delta)))),
            unit_cost: Number(i.quantity_delta) > 0 ? plain(i.unit_cost) : "",
            remarks: i.remarks ?? "",
          })),
        }
      : {
          warehouse_id: "",
          reason_code: "COUNT_CORRECTION",
          reason_note: "",
          adjustment_date: "",
          lines: [{ ...emptyLine }],
        },
  });
  const lines = useFieldArray({ control: form.control, name: "lines" });
  const errors = form.formState.errors;
  const warehouseId = form.watch("warehouse_id");
  const watched = form.watch("lines");

  // What the books say is on hand, so the person correcting them can see it.
  const onHand = useQuery({
    queryKey: ["inventory", "balances", "for-adjustment", warehouseId],
    queryFn: () =>
      api.get<Page<StockBalance>>("/inventory/balances", {
        query: { warehouse_id: warehouseId, limit: 200 },
      }),
    enabled: Boolean(warehouseId),
  });
  const stock = (materialId: string) =>
    onHand.data?.items.find((b) => b.material_id === materialId);
  const baseUnitOf = (materialId: string) => {
    const m = materials.rows.find((r) => r.id === materialId);
    return m ? units.rows.find((u) => u.id === m.base_unit_id)?.code : undefined;
  };

  const save = useMutation({
    mutationFn: (values: Values) => {
      const body = {
        ...emptyToNull({
          warehouse_id: values.warehouse_id,
          reason_code: values.reason_code,
          reason_note: values.reason_note,
          adjustment_date: values.adjustment_date,
        }),
        lines: values.lines.map((l) => ({
          material_id: l.material_id,
          quantity_delta: `${l.direction === "decrease" ? "-" : ""}${l.quantity}`,
          unit_cost: l.direction === "increase" && l.unit_cost ? l.unit_cost : null,
          remarks: l.remarks.trim() || null,
        })),
      } as unknown as StockAdjustmentCreate;
      return adjustment
        ? api.put<StockAdjustmentRead>(`/inventory/adjustments/${adjustment.id}`, body, {
            ifMatch: String(adjustment.version),
          })
        : api.post<StockAdjustmentRead>("/inventory/adjustments", body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(`${saved.adjustment_number} saved as a draft. Submit it for approval.`);
      navigate(`/inventory/adjustments/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["warehouse_id", "reason_code"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title={adjustment ? `Edit ${adjustment.adjustment_number}` : "New stock adjustment"}
        subtitle="It moves no stock until it is approved."
        crumbs={[
          { label: "Stock adjustments", to: "/inventory/adjustments" },
          ...(adjustment
            ? [
                {
                  label: adjustment.adjustment_number,
                  to: `/inventory/adjustments/${adjustment.id}`,
                },
              ]
            : []),
          { label: adjustment ? "Edit" : "New" },
        ]}
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
          {adjustment?.decision_reason && adjustment.status === "REJECTED" && (
            <FormAlert>Rejected: {adjustment.decision_reason}</FormAlert>
          )}
          <FormSection title="Why">
            <FormGrid>
              <FormField label="Store" required error={errors.warehouse_id?.message}>
                <Select {...form.register("warehouse_id")}>
                  <option value="">Choose…</option>
                  {stores.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Reason" required error={errors.reason_code?.message}>
                <Select {...form.register("reason_code")}>
                  {REASONS.map((r) => (
                    <option key={r} value={r}>
                      {humanize(r)}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField
                label="What happened"
                required
                error={errors.reason_note?.message}
                className="sm:col-span-2"
              >
                <Textarea rows={2} {...form.register("reason_note")} />
              </FormField>
              <FormField label="Date" hint="Left blank, today.">
                <Input type="date" {...form.register("adjustment_date")} />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="What changes">
            {errors.lines?.message && (
              <p className="mb-2 text-xs text-danger">{errors.lines.message}</p>
            )}
            <div className="flex flex-col gap-3">
              {lines.fields.map((field, i) => {
                const row = errors.lines?.[i];
                const material = watched[i]?.material_id ?? "";
                const balance = stock(material);
                const base = baseUnitOf(material);
                return (
                  <div key={field.id} className="rounded-md border border-border p-3">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_1fr_1fr_auto]">
                      <FormField
                        label={`Line ${i + 1} material`}
                        error={row?.material_id?.message}
                        hint={
                          material && warehouseId
                            ? `On hand now: ${formatQuantity(balance?.quantity_on_hand ?? "0", base)}`
                            : undefined
                        }
                      >
                        <Select {...form.register(`lines.${i}.material_id`)}>
                          <option value="">Choose…</option>
                          {materials.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Change">
                        <Select {...form.register(`lines.${i}.direction`)}>
                          <option value="decrease">Take out</option>
                          <option value="increase">Put in</option>
                        </Select>
                      </FormField>
                      <FormField
                        label={base ? `Quantity (${base})` : "Quantity"}
                        error={row?.quantity?.message}
                      >
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`lines.${i}.quantity`)}
                        />
                      </FormField>
                      {watched[i]?.direction === "increase" ? (
                        <FormField
                          label="Cost per unit"
                          error={row?.unit_cost?.message}
                          hint="Blank: the current average."
                        >
                          <Input
                            inputMode="decimal"
                            className="tabular"
                            {...form.register(`lines.${i}.unit_cost`)}
                          />
                        </FormField>
                      ) : (
                        <div />
                      )}
                      <div className="flex items-end pb-0.5">
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Remove line ${i + 1}`}
                          onClick={() => lines.remove(i)}
                          disabled={lines.fields.length === 1}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
              <div>
                <Button size="sm" onClick={() => lines.append({ ...emptyLine })}>
                  <Plus /> Add line
                </Button>
              </div>
            </div>
            <p className="mt-2 text-xs text-fg-subtle">
              Quantities are in the material's base unit, the one the stock ledger holds.
            </p>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {adjustment ? "Save changes" : "Save draft"}
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

type Dialogs = null | "submit" | "withdraw" | "cancel";

export function AdjustmentDetailPage() {
  const { adjustmentId = "" } = useParams();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const query = useQuery({
    queryKey: ["stock-adjustments", "detail", adjustmentId],
    queryFn: () => api.get<StockAdjustmentRead>(`/inventory/adjustments/${adjustmentId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const a = query.data;

  return (
    <>
      <PageHeader
        title={a.adjustment_number}
        crumbs={[
          { label: "Stock adjustments", to: "/inventory/adjustments" },
          { label: a.adjustment_number },
        ]}
        meta={
          <>
            <StatusBadge status={a.status} />
            <span className="text-sm text-fg-muted">
              {humanize(a.reason_code)} · {a.warehouse_code}
            </span>
            {!a.prices_hidden && a.net_value != null && (
              <span className="text-sm text-fg-muted">
                Net value{" "}
                <span className="tabular font-medium text-fg">{formatMoney(a.net_value)}</span>
              </span>
            )}
          </>
        }
        actions={
          <>
            {a.can_edit && (
              <Button asChild>
                <Link to={`/inventory/adjustments/${a.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {a.can_submit && (
              <Button variant="primary" onClick={() => setDialog("submit")}>
                <Send /> Submit for approval
              </Button>
            )}
            {a.can_withdraw && (
              <Button onClick={() => setDialog("withdraw")}>
                <Undo2 /> Withdraw
              </Button>
            )}
            {a.can_cancel && (
              <Button variant="danger" onClick={() => setDialog("cancel")}>
                <Ban /> Cancel
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {a.status === "REJECTED" && a.decision_reason && (
          <FormAlert>
            Rejected: {a.decision_reason}. Edit it and submit again, or cancel it.
          </FormAlert>
        )}
        {a.cancel_reason && <FormAlert>Cancelled: {a.cancel_reason}</FormAlert>}
        <Section title="Lines">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Adjustment lines</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Material
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Books said
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Change
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Cost per unit
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Value
                  </th>
                </tr>
              </thead>
              <tbody>
                {a.items.map((l) => (
                  <tr key={l.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{l.material_sku}</span>{" "}
                      <span className="font-medium">{l.material_name}</span>
                      {l.remarks && <p className="text-xs text-fg-muted">{l.remarks}</p>}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {l.system_quantity
                        ? formatQuantity(l.system_quantity, l.unit_code ?? undefined)
                        : "—"}
                    </td>
                    <td
                      data-numeric
                      className={
                        Number(l.quantity_delta) < 0
                          ? "px-4 py-2 text-danger"
                          : "px-4 py-2 text-success"
                      }
                    >
                      {Number(l.quantity_delta) > 0 ? "+" : ""}
                      {formatQuantity(l.quantity_delta, l.unit_code ?? undefined)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {a.prices_hidden ? (
                        <span className="text-fg-subtle">Hidden</span>
                      ) : l.unit_cost ? (
                        formatMoney(l.unit_cost, { decimals: 2 })
                      ) : (
                        "—"
                      )}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {a.prices_hidden ? (
                        <span className="text-fg-subtle">Hidden</span>
                      ) : l.value_delta ? (
                        formatMoney(l.value_delta)
                      ) : (
                        "—"
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
              { label: "Store", value: `${a.warehouse_code} — ${a.warehouse_name}` },
              { label: "Site", value: a.site_code },
              { label: "Reason", value: humanize(a.reason_code) },
              { label: "Date", value: formatDate(a.adjustment_date) },
              { label: "Submitted", value: a.submitted_at ? formatDateTime(a.submitted_at) : null },
              { label: "Posted", value: a.posted_at ? formatDateTime(a.posted_at) : null },
              { label: "What happened", value: a.reason_note, wide: true },
            ]}
          />
        </Section>

        <ApprovalSection
          docType="stock_adjustment"
          docId={a.id}
          label={a.adjustment_number}
          empty="Not submitted yet. Once it is, the people who must sign it and each decision appear here."
        />
      </PageBody>

      <ConfirmDialog
        open={dialog === "submit"}
        onOpenChange={(o) => setDialog(o ? "submit" : null)}
        title={`Submit ${a.adjustment_number}?`}
        description="It goes to the people who must sign it. No stock moves until every step is approved."
        confirmLabel="Submit for approval"
        onConfirm={async () => {
          await api.post(`/inventory/adjustments/${a.id}/submit`, undefined, {
            ifMatch: String(a.version),
          });
          await refresh();
          toast.success(`${a.adjustment_number} submitted for approval.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "withdraw"}
        onOpenChange={(o) => setDialog(o ? "withdraw" : null)}
        title={`Withdraw ${a.adjustment_number}?`}
        description="It goes back to a draft so you can change it. Only possible before anyone has approved a step."
        confirmLabel="Withdraw"
        onConfirm={async () => {
          await api.post(`/inventory/adjustments/${a.id}/withdraw`);
          await refresh();
          toast.success(`${a.adjustment_number} withdrawn.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "cancel"}
        onOpenChange={(o) => setDialog(o ? "cancel" : null)}
        title={`Cancel ${a.adjustment_number}?`}
        description="The adjustment is closed. It never moved any stock."
        confirmLabel={`Cancel ${a.adjustment_number}`}
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/inventory/adjustments/${a.id}/cancel`, { reason });
          await refresh();
          toast.success(`${a.adjustment_number} cancelled.`);
        }}
      />
    </>
  );
}
