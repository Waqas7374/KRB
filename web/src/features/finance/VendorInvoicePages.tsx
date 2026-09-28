import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { CheckCircle2, Plus, Trash2 } from "lucide-react";
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
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Input, Select } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api, type Page } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import {
  useAccountOptions,
  usePagedList,
  useTaxCodeOptions,
  useUnitOptions,
  useVendorOptions,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatMoney, humanize } from "@/lib/utils";
import type {
  GrnListItem,
  GrnRead,
  InvoiceItemIn,
  VendorInvoiceCreate,
  VendorInvoiceListItem,
  VendorInvoiceRead,
} from "@/types/models";

const STATUS_OPTIONS = [
  "DRAFT",
  "PENDING_MATCH",
  "MATCHED",
  "DISPUTED",
  "APPROVED",
  "PARTIALLY_PAID",
  "PAID",
  "CANCELLED",
].map((value) => ({ value, label: humanize(value) }));

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<VendorInvoiceListItem>();

export function VendorInvoiceListPage() {
  const list = useListParams({ sort: "-invoice_date" });
  const query = usePagedList<VendorInvoiceListItem>(
    "vendor-invoices",
    "/finance/vendor-invoices",
    list.query,
  );
  const vendors = useVendorOptions();

  const columns = useMemo(
    () => [
      col.accessor("invoice_number", {
        header: "Invoice",
        meta: { alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/finance/vendor-invoices/${c.row.original.id}`}
            className="font-mono text-primary hover:underline"
          >
            {c.getValue()}
          </Link>
        ),
      }),
      col.display({
        id: "vendor",
        header: "Vendor",
        cell: (c) => c.row.original.vendor_name ?? "—",
      }),
      col.accessor("vendor_invoice_ref", { header: "Their ref" }),
      col.accessor("status", {
        header: "Status",
        cell: (c) => <StatusBadge status={c.getValue()} />,
      }),
      col.accessor("invoice_date", { header: "Date", cell: (c) => formatDate(c.getValue()) }),
      col.accessor("due_date", { header: "Due", cell: (c) => formatDate(c.getValue()) }),
      col.accessor("total_amount", {
        header: "Total",
        meta: { numeric: true },
        cell: (c) => formatMoney(c.getValue()),
      }),
      col.accessor("outstanding_amount", {
        header: "Outstanding",
        meta: { numeric: true },
        cell: (c) => formatMoney(c.getValue()),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Vendor invoices"
        subtitle="A vendor's bill, checked against what was ordered and received before it ever posts."
        actions={
          <PermissionGate permission="finance.ap.create">
            <Button variant="primary" asChild>
              <Link to="/finance/vendor-invoices/new">
                <Plus /> New invoice
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="vendor-invoices"
          caption="Vendor invoices"
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
          getRowHref={(r) => `/finance/vendor-invoices/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Invoice number or their reference…"
              savedViewsId="vendor-invoices"
              filters={[
                { key: "status", label: "Status", options: STATUS_OPTIONS },
                { key: "vendor_id", label: "Vendor", options: vendors.options },
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
    source: z.enum(["grn", "direct"]),
    grn_item_id: z.string(),
    material_id: z.string(),
    description: z.string(),
    unit_id: z.string(),
    quantity: z.string().regex(DECIMAL_RE, "A number, up to 4 decimals"),
    rate: z.string().regex(DECIMAL_RE, "A number, up to 4 decimals"),
    tax_code_id: z.string(),
    tax_pct: z.string().regex(DECIMAL_RE, "A number, up to 4 decimals").or(z.literal("")),
    account_id: z.string(),
  })
  .superRefine((line, ctx) => {
    if (line.source === "direct" && !line.account_id) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Choose an account",
        path: ["account_id"],
      });
    }
    if (Number(line.quantity || 0) <= 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "More than zero",
        path: ["quantity"],
      });
    }
  });

const schema = z.object({
  vendor_id: z.string().min(1, "Choose a vendor"),
  vendor_invoice_ref: z.string().trim().min(1, "Required").max(60),
  invoice_date: z.string().min(1, "Required"),
  due_date: z.string(),
  withholding_amount: z.string().regex(DECIMAL_RE, "A number, up to 4 decimals").or(z.literal("")),
  items: z.array(lineSchema).min(1, "Add at least one line"),
});
type Values = z.infer<typeof schema>;

const directLine = {
  source: "direct" as const,
  grn_item_id: "",
  material_id: "",
  description: "",
  unit_id: "",
  quantity: "",
  rate: "",
  tax_code_id: "",
  tax_pct: "",
  account_id: "",
};

function lineAmount(line: Values["items"][number]): number {
  const base = Number(line.quantity || 0) * Number(line.rate || 0);
  return base + (base * Number(line.tax_pct || 0)) / 100;
}

export function VendorInvoiceFormPage() {
  const { invoiceId } = useParams();
  const existing = useQuery({
    queryKey: ["vendor-invoices", "detail", invoiceId],
    queryFn: () => api.get<VendorInvoiceRead>(`/finance/vendor-invoices/${invoiceId}`),
    enabled: Boolean(invoiceId),
    staleTime: 0,
  });
  if (invoiceId && existing.isLoading) return <PageSkeleton />;
  if (invoiceId && existing.error) {
    return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />;
  }
  return <VendorInvoiceForm invoice={existing.data} />;
}

function VendorInvoiceForm({ invoice }: { invoice?: VendorInvoiceRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const vendors = useVendorOptions();
  const accounts = useAccountOptions();
  const units = useUnitOptions();
  const taxCodes = useTaxCodeOptions();
  const postable = accounts.rows.filter((a) => a.is_postable && a.is_active);
  const [formError, setFormError] = useState<string | null>(null);
  const [grnPicker, setGrnPicker] = useState(false);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: invoice
      ? {
          vendor_id: invoice.vendor_id,
          vendor_invoice_ref: invoice.vendor_invoice_ref,
          invoice_date: invoice.invoice_date,
          due_date: invoice.due_date,
          withholding_amount: invoice.withholding_amount,
          items: invoice.items.map((i) => ({
            source: i.grn_item_id ? ("grn" as const) : ("direct" as const),
            grn_item_id: i.grn_item_id ?? "",
            material_id: i.material_id ?? "",
            description: i.description ?? "",
            unit_id: i.unit_id ?? "",
            quantity: i.quantity,
            rate: i.rate,
            tax_code_id: i.tax_code_id ?? "",
            tax_pct: i.tax_pct,
            account_id: i.account_id ?? "",
          })),
        }
      : {
          vendor_id: "",
          vendor_invoice_ref: "",
          invoice_date: new Date().toISOString().slice(0, 10),
          due_date: "",
          withholding_amount: "",
          items: [{ ...directLine }],
        },
  });
  const lines = useFieldArray({ control: form.control, name: "items" });
  const errors = form.formState.errors;
  const vendorId = form.watch("vendor_id");
  const items = form.watch("items");

  const save = useMutation({
    mutationFn: () => {
      const values = form.getValues();
      const body: VendorInvoiceCreate = emptyToNull({
        vendor_id: values.vendor_id,
        vendor_invoice_ref: values.vendor_invoice_ref,
        invoice_date: values.invoice_date,
        due_date: values.due_date || null,
        withholding_amount: values.withholding_amount || "0",
        items: values.items.map((l): InvoiceItemIn => ({
          grn_item_id: l.source === "grn" ? l.grn_item_id : null,
          material_id: l.material_id || null,
          description: l.description || null,
          unit_id: l.unit_id || null,
          quantity: l.quantity,
          rate: l.rate,
          tax_code_id: l.tax_code_id || null,
          tax_pct: l.tax_pct || "0",
          account_id: l.source === "direct" ? l.account_id : null,
        })),
      });
      return invoice
        ? api.put<VendorInvoiceRead>(`/finance/vendor-invoices/${invoice.id}`, body, {
            ifMatch: String(invoice.version),
          })
        : api.post<VendorInvoiceRead>("/finance/vendor-invoices", body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(`${saved.invoice_number} saved as a draft.`);
      navigate(`/finance/vendor-invoices/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "vendor_id",
        "vendor_invoice_ref",
        "invoice_date",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  const total = items.reduce((sum, l) => sum + lineAmount(l), 0);

  return (
    <>
      <PageHeader
        title={invoice ? `Edit ${invoice.invoice_number}` : "New vendor invoice"}
        crumbs={[
          { label: "Vendor invoices", to: "/finance/vendor-invoices" },
          ...(invoice
            ? [{ label: invoice.invoice_number, to: `/finance/vendor-invoices/${invoice.id}` }]
            : []),
          { label: invoice ? "Edit" : "New" },
        ]}
      />
      <PageBody className="max-w-5xl">
        <form
          onSubmit={(e) => {
            setFormError(null);
            void form.handleSubmit(() => save.mutate())(e);
          }}
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}
          <FormSection title="What">
            <FormGrid>
              <FormField label="Vendor" required error={errors.vendor_id?.message}>
                <Select {...form.register("vendor_id")} disabled={Boolean(invoice)}>
                  <option value="">Choose…</option>
                  {vendors.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField
                label="Their reference"
                required
                error={errors.vendor_invoice_ref?.message}
                hint="The bill number as the vendor wrote it"
              >
                <Input {...form.register("vendor_invoice_ref")} />
              </FormField>
              <FormField label="Invoice date" required error={errors.invoice_date?.message}>
                <Input type="date" {...form.register("invoice_date")} />
              </FormField>
              <FormField label="Due date" hint="Left blank, the vendor's own payment terms apply.">
                <Input type="date" {...form.register("due_date")} />
              </FormField>
              <FormField
                label="Withholding (informational)"
                hint="Withheld and posted at payment, not here."
              >
                <Input
                  inputMode="decimal"
                  className="tabular"
                  {...form.register("withholding_amount")}
                />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Lines">
            {errors.items?.message && (
              <p className="mb-2 text-xs text-danger">{errors.items.message}</p>
            )}
            <div className="flex flex-col gap-3">
              {lines.fields.map((field, i) => {
                const row = errors.items?.[i];
                const line = items[i];
                const isGrn = line?.source === "grn";
                return (
                  <div key={field.id} className="rounded-md border border-border p-3">
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs font-medium text-fg-muted">
                        Line {i + 1} {isGrn && "· from a GRN"}
                      </span>
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
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-6">
                      <FormField label="Description" className="md:col-span-2">
                        <Input {...form.register(`items.${i}.description`)} />
                      </FormField>
                      <FormField label="Quantity" error={row?.quantity?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`items.${i}.quantity`)}
                        />
                      </FormField>
                      <FormField label="Rate" error={row?.rate?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`items.${i}.rate`)}
                        />
                      </FormField>
                      <FormField label="Tax code">
                        <Select
                          {...form.register(`items.${i}.tax_code_id`)}
                          onChange={(e) => {
                            form.setValue(`items.${i}.tax_code_id`, e.target.value);
                            const found = taxCodes.rows.find((t) => t.id === e.target.value);
                            form.setValue(
                              `items.${i}.tax_pct`,
                              found ? String(found.rate_pct) : "",
                            );
                          }}
                        >
                          <option value="">None</option>
                          {taxCodes.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Amount">
                        <p className="pt-2 text-sm tabular text-fg-muted">
                          {formatMoney(lineAmount(line ?? directLine).toString())}
                        </p>
                      </FormField>
                    </div>
                    {!isGrn && (
                      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
                        <FormField
                          label="Account"
                          required
                          error={row?.account_id?.message}
                          hint="No order or GRN behind this line, so it needs one named by hand."
                        >
                          <Select {...form.register(`items.${i}.account_id`)}>
                            <option value="">Choose…</option>
                            {postable.map((a) => (
                              <option key={a.id} value={a.id}>
                                {a.code} — {a.name}
                              </option>
                            ))}
                          </Select>
                        </FormField>
                        <FormField label="Unit">
                          <Select {...form.register(`items.${i}.unit_id`)}>
                            <option value="">—</option>
                            {units.options.map((o) => (
                              <option key={o.value} value={o.value}>
                                {o.label}
                              </option>
                            ))}
                          </Select>
                        </FormField>
                      </div>
                    )}
                  </div>
                );
              })}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" onClick={() => lines.append({ ...directLine })}>
                  <Plus /> Add direct line
                </Button>
                <Button
                  size="sm"
                  onClick={() => setGrnPicker(true)}
                  disabled={!vendorId}
                  title={vendorId ? undefined : "Choose a vendor first"}
                >
                  <Plus /> Add line from a GRN
                </Button>
              </div>
            </div>
            <div className="mt-4 flex justify-end border-t border-border pt-3 text-sm font-semibold">
              Total: {formatMoney(total.toString())}
            </div>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {invoice ? "Save changes" : "Save draft"}
            </Button>
            <Button variant="ghost" onClick={() => navigate(-1)} disabled={save.isPending}>
              Cancel
            </Button>
          </div>
        </form>
      </PageBody>

      {grnPicker && (
        <GrnLinePicker
          vendorId={vendorId}
          onClose={() => setGrnPicker(false)}
          onPick={(line) => {
            lines.append(line);
            setGrnPicker(false);
          }}
        />
      )}
    </>
  );
}

function GrnLinePicker({
  vendorId,
  onClose,
  onPick,
}: {
  vendorId: string;
  onClose: () => void;
  onPick: (line: Values["items"][number]) => void;
}) {
  const [grnId, setGrnId] = useState("");
  const grns = useQuery({
    queryKey: ["grns", "for-invoice", vendorId],
    queryFn: () =>
      api.get<Page<GrnListItem>>("/grns", {
        query: { vendor_id: vendorId, status: "POSTED", limit: 50 },
      }),
    enabled: Boolean(vendorId),
  });
  const grn = useQuery({
    queryKey: ["grns", "detail-for-invoice", grnId],
    queryFn: () => api.get<GrnRead>(`/grns/${grnId}`),
    enabled: Boolean(grnId),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Add a line from a GRN"
      description="Only this vendor's posted goods received notes — picking a line fills quantity and rate from what was actually received; matching checks them against the order once you match."
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <div className="flex flex-col gap-4">
        <FormField label="Goods received note">
          <Select value={grnId} onChange={(e) => setGrnId(e.target.value)}>
            <option value="">Choose…</option>
            {grns.data?.items.map((g) => (
              <option key={g.id} value={g.id}>
                {g.grn_number} — {formatDate(g.received_date)}
                {g.purchase_order_id ? "" : " (counter purchase)"}
              </option>
            ))}
          </Select>
        </FormField>
        {grns.data?.items.length === 0 && (
          <p className="text-sm text-fg-muted">No posted GRNs for this vendor yet.</p>
        )}
        {grnId && grn.data && (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">Lines on {grn.data.grn_number}</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    Material
                  </th>
                  <th scope="col" data-numeric className="px-3 py-2 font-semibold">
                    Accepted qty
                  </th>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    <span className="sr-only">Action</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {grn.data.items.map((item) => (
                  <tr key={item.id} className="border-t border-border">
                    <td className="px-3 py-2">
                      {item.material_name}
                      <span className="ml-2 text-xs text-fg-subtle">{item.unit_code}</span>
                    </td>
                    <td data-numeric className="px-3 py-2 tabular">
                      {item.accepted_quantity}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() =>
                          onPick({
                            source: "grn",
                            grn_item_id: item.id,
                            material_id: item.material_id,
                            description: item.material_name ?? "",
                            unit_id: item.unit_id,
                            // A GRN line's own rate/quantity columns carry more
                            // decimal places than an invoice line's Numeric(18,4)
                            // allows; round to what the invoice can actually store.
                            quantity: Number(item.accepted_quantity).toFixed(4),
                            rate: Number(item.rate ?? 0).toFixed(4),
                            tax_code_id: "",
                            tax_pct: "",
                            account_id: "",
                          })
                        }
                      >
                        Add
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Dialog>
  );
}

// --- Detail ----------------------------------------------------------------------

type Dialogs = null | "dispute" | "delete";

export function VendorInvoiceDetailPage() {
  const { invoiceId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const query = useQuery({
    queryKey: ["vendor-invoices", "detail", invoiceId],
    queryFn: () => api.get<VendorInvoiceRead>(`/finance/vendor-invoices/${invoiceId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  const match = useMutation({
    mutationFn: (version: number) =>
      api.post(`/finance/vendor-invoices/${invoiceId}/match`, undefined, {
        ifMatch: String(version),
      }),
    onSuccess: async () => {
      await refresh();
      toast.success("Matched.");
    },
    onError: (err) => toast.error(describeError(err)),
  });
  const approve = useMutation({
    mutationFn: (version: number) =>
      api.post(`/finance/vendor-invoices/${invoiceId}/approve`, undefined, {
        ifMatch: String(version),
      }),
    onSuccess: async () => {
      await refresh();
      toast.success("Approved.");
    },
    onError: (err) => toast.error(describeError(err)),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const inv = query.data;

  return (
    <>
      <PageHeader
        title={inv.invoice_number}
        crumbs={[
          { label: "Vendor invoices", to: "/finance/vendor-invoices" },
          { label: inv.invoice_number },
        ]}
        meta={
          <>
            <StatusBadge status={inv.status} />
            <span className="text-sm text-fg-muted">
              {inv.vendor_name} · their ref {inv.vendor_invoice_ref}
            </span>
            <span className="text-sm tabular text-fg-muted">{formatMoney(inv.total_amount)}</span>
          </>
        }
        actions={
          <>
            {inv.can_edit && (
              <Button asChild>
                <Link to={`/finance/vendor-invoices/${inv.id}/edit`}>Edit</Link>
              </Button>
            )}
            {inv.can_match && (
              <Button
                variant="primary"
                loading={match.isPending}
                onClick={() => match.mutate(inv.version)}
              >
                Match
              </Button>
            )}
            {inv.can_dispute && (
              <Button variant="danger" onClick={() => setDialog("dispute")}>
                Dispute
              </Button>
            )}
            {inv.can_approve && (
              <Button
                variant="primary"
                loading={approve.isPending}
                onClick={() => approve.mutate(inv.version)}
              >
                <CheckCircle2 /> Approve
              </Button>
            )}
            {inv.can_delete && (
              <Button variant="danger" onClick={() => setDialog("delete")}>
                Delete
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {inv.decision_reason && (inv.status === "DISPUTED" || inv.status === "DRAFT") && (
          <FormAlert>{inv.decision_reason}</FormAlert>
        )}

        <Section title="Lines">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Invoice lines</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Line
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Qty
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Rate
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Amount
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Account
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Match
                  </th>
                </tr>
              </thead>
              <tbody>
                {inv.items.map((item) => (
                  <tr key={item.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      {item.material_name ?? item.description ?? `Line ${item.line_no}`}
                    </td>
                    <td data-numeric className="px-4 py-2 tabular">
                      {item.quantity}
                    </td>
                    <td data-numeric className="px-4 py-2 tabular">
                      {item.rate}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatMoney((Number(item.amount) + Number(item.tax_amount)).toString())}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-fg-muted">
                      {item.account_code ?? "—"}
                    </td>
                    <td className="px-4 py-2">
                      {item.match_type ? (
                        <div className="flex flex-col gap-0.5">
                          <span
                            className={
                              item.within_tolerance
                                ? "text-xs text-success"
                                : "text-xs font-medium text-danger"
                            }
                          >
                            {humanize(item.match_type)}
                            {item.within_tolerance === false && " · outside tolerance"}
                          </span>
                          {item.qty_variance != null && Number(item.qty_variance) !== 0 && (
                            <span className="text-xs text-fg-subtle">
                              qty {Number(item.qty_variance) > 0 ? "+" : ""}
                              {item.qty_variance}
                            </span>
                          )}
                          {item.rate_variance != null && Number(item.rate_variance) !== 0 && (
                            <span className="text-xs text-fg-subtle">
                              rate {Number(item.rate_variance) > 0 ? "+" : ""}
                              {item.rate_variance}
                            </span>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-fg-subtle">Not yet matched</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border font-semibold">
                  <td className="px-4 py-2" colSpan={3}>
                    Total
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(inv.total_amount)}
                  </td>
                  <td colSpan={2} />
                </tr>
              </tfoot>
            </table>
          </div>
        </Section>

        <Section title="Details">
          <FieldGrid
            items={[
              { label: "Invoice date", value: formatDate(inv.invoice_date) },
              { label: "Due date", value: formatDate(inv.due_date) },
              { label: "Purchase order", value: inv.purchase_order_number },
              { label: "Withholding", value: formatMoney(inv.withholding_amount) },
              { label: "Paid so far", value: formatMoney(inv.paid_amount) },
              { label: "Outstanding", value: formatMoney(inv.outstanding_amount) },
              { label: "Matched", value: inv.matched_at ? formatDate(inv.matched_at) : null },
              { label: "Approved", value: inv.approved_at ? formatDate(inv.approved_at) : null },
            ]}
          />
        </Section>
      </PageBody>

      <ConfirmDialog
        open={dialog === "dispute"}
        onOpenChange={(o) => setDialog(o ? "dispute" : null)}
        title={`Dispute ${inv.invoice_number}?`}
        description="Sends this invoice back for correction. It can be edited and rematched afterward."
        confirmLabel="Dispute"
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(
            `/finance/vendor-invoices/${inv.id}/dispute`,
            { reason },
            { ifMatch: String(inv.version) },
          );
          await refresh();
          toast.success("Disputed.");
        }}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        onOpenChange={(o) => setDialog(o ? "delete" : null)}
        title={`Delete ${inv.invoice_number}?`}
        description="It never happened, so there is nothing to reverse — this removes it entirely."
        confirmLabel="Delete"
        destructive
        onConfirm={async () => {
          await api.delete(`/finance/vendor-invoices/${inv.id}`);
          toast.success("Deleted.");
          navigate("/finance/vendor-invoices");
        }}
      />
    </>
  );
}
