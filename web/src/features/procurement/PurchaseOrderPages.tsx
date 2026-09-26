import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import {
  Ban,
  CheckCheck,
  Download,
  FilePenLine,
  Lock,
  Pencil,
  Plus,
  Send,
  Trash2,
  Truck,
} from "lucide-react";
import { useState } from "react";
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
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { ApprovalTrail } from "@/features/approvals/ApprovalTrail";
import { DecisionActions } from "@/features/approvals/DecisionActions";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api } from "@/lib/api";
import { downloadFile } from "@/lib/download";
import { applyServerErrors, describeError } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import {
  usePagedList,
  useMaterialOptions,
  useProjectOptions,
  useSiteOptions,
  useUnitOptions,
  useVendorOptions,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney, formatQuantity } from "@/lib/utils";
import type { ApprovalRequestRead, PurchaseOrderListItem, PurchaseOrderRead } from "@/types/models";

import { plain, PO_STATUS_OPTIONS, previewLine, sumPreviews } from "./sourcing";

// --- List -----------------------------------------------------------------------

const col = createColumnHelper<PurchaseOrderListItem>();

const columns = [
  col.accessor("po_number", {
    header: "Number",
    meta: { sortKey: "po_number", alwaysVisible: true },
    cell: (c) => (
      <Link
        to={`/purchase-orders/${c.row.original.id}`}
        className="font-mono text-primary hover:underline"
      >
        {c.getValue()}
        {c.row.original.revision > 0 && (
          <span className="ml-1 text-xs text-fg-muted">rev {c.row.original.revision}</span>
        )}
      </Link>
    ),
  }),
  col.accessor("status", {
    header: "Status",
    meta: { sortKey: "status" },
    cell: (c) => <StatusBadge status={c.getValue()} />,
  }),
  col.accessor("vendor_name", { header: "Vendor", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("project_code", { header: "Project", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("site_code", { header: "Site", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("po_date", {
    header: "Date",
    meta: { sortKey: "po_date" },
    cell: (c) => formatDate(c.getValue()),
  }),
  col.accessor("expected_delivery_date", {
    header: "Expected",
    meta: { sortKey: "expected_delivery_date" },
    cell: (c) => formatDate(c.getValue()),
  }),
  col.accessor("total_amount", {
    header: "Total",
    meta: { numeric: true, sortKey: "total_amount" },
    cell: (c) =>
      c.getValue() == null ? (
        <span className="text-fg-subtle" title="You do not have permission to see order values">
          Hidden
        </span>
      ) : (
        formatMoney(c.getValue(), { currency: c.row.original.currency_code, decimals: 0 })
      ),
  }),
];

export function PurchaseOrdersListPage() {
  const list = useListParams({ sort: "-created_at" });
  const query = usePagedList<PurchaseOrderListItem>(
    "purchase-orders",
    "/purchase-orders",
    list.query,
  );
  const projects = useProjectOptions();
  const vendors = useVendorOptions();

  return (
    <>
      <PageHeader
        title="Purchase orders"
        subtitle="Commitments to vendors. An order is routed for approval by its total before it can be sent."
        actions={
          <PermissionGate permission="procurement.po.create">
            <Button variant="primary" asChild>
              <Link to="/purchase-orders/new">
                <Plus /> New order
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="purchase-orders"
          caption="Purchase orders"
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
          getRowHref={(r) => `/purchase-orders/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Order number…  ( / )"
              savedViewsId="purchase-orders"
              filters={[
                { key: "status", label: "Status", options: PO_STATUS_OPTIONS },
                { key: "project_id", label: "Project", options: projects.options },
                { key: "vendor_id", label: "Vendor", options: vendors.options },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Form -----------------------------------------------------------------------

const optionalPercent = (message: string) =>
  z
    .string()
    .trim()
    .refine((v) => v === "" || (DECIMAL_RE.test(v) && Number(v) <= 100), message);

const lineSchema = z.object({
  material_id: z.string().min(1, "Choose a material"),
  unit_id: z.string().min(1, "Choose a unit"),
  quantity: z
    .string()
    .trim()
    .refine((v) => DECIMAL_RE.test(v) && Number(v) > 0, "A positive number"),
  rate: z
    .string()
    .trim()
    .refine((v) => DECIMAL_RE.test(v), "A plain amount"),
  discount_pct: optionalPercent("0–100"),
  tax_pct: optionalPercent("0–100"),
  description: z.string(),
  // Set for lines that fulfil a purchase request; carried through edits unseen.
  pr_item_id: z.string(),
  pr_number: z.string(),
});

const schema = z.object({
  vendor_id: z.string().min(1, "Choose a vendor"),
  project_id: z.string().min(1, "Choose a project"),
  site_id: z.string(),
  expected_delivery_date: z.string(),
  delivery_address: z.string().max(500),
  payment_terms: z.string().max(200),
  terms_and_conditions: z.string().max(4000),
  items: z.array(lineSchema).min(1, "Add at least one line"),
});
type Values = z.infer<typeof schema>;

const emptyLine = {
  material_id: "",
  unit_id: "",
  quantity: "",
  rate: "",
  discount_pct: "",
  tax_pct: "",
  description: "",
  pr_item_id: "",
  pr_number: "",
};

export function PurchaseOrderFormPage() {
  const { poId } = useParams();
  const existing = useQuery({
    queryKey: ["purchase-orders", "detail", poId],
    queryFn: () => api.get<PurchaseOrderRead>(`/purchase-orders/${poId}`),
    enabled: Boolean(poId),
    staleTime: 0,
  });
  if (poId && existing.isLoading) return <PageSkeleton />;
  if (poId && existing.error) {
    return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />;
  }
  if (existing.data?.prices_hidden) {
    return (
      <ErrorState
        error={new Error("You do not have permission to see or change prices on orders.")}
      />
    );
  }
  return <PurchaseOrderForm order={existing.data} />;
}

function PurchaseOrderForm({ order }: { order?: PurchaseOrderRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projects = useProjectOptions();
  const vendors = useVendorOptions();
  const materials = useMaterialOptions();
  const units = useUnitOptions();
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: order
      ? {
          vendor_id: order.vendor_id,
          project_id: order.project_id,
          site_id: order.site_id ?? "",
          expected_delivery_date: order.expected_delivery_date ?? "",
          delivery_address: order.delivery_address ?? "",
          payment_terms: order.payment_terms ?? "",
          terms_and_conditions: order.terms_and_conditions ?? "",
          items: order.items.map((i) => ({
            material_id: i.material_id,
            unit_id: i.unit_id,
            quantity: plain(i.quantity),
            rate: plain(i.rate),
            discount_pct: plain(i.discount_pct),
            tax_pct: plain(i.tax_pct),
            description: i.description ?? "",
            pr_item_id: i.pr_item_id ?? "",
            pr_number: i.pr_number ?? "",
          })),
        }
      : {
          vendor_id: "",
          project_id: "",
          site_id: "",
          expected_delivery_date: "",
          delivery_address: "",
          payment_terms: "",
          terms_and_conditions: "",
          items: [{ ...emptyLine }],
        },
  });
  const lines = useFieldArray({ control: form.control, name: "items" });
  const projectId = form.watch("project_id");
  const sites = useSiteOptions(projectId || undefined);
  const errors = form.formState.errors;
  const watched = form.watch("items");

  const previews = watched.map((i) => previewLine(i.quantity, i.rate, i.discount_pct, i.tax_pct));
  const totals = sumPreviews(previews);
  const fixedVendor = Boolean(order?.quotation_id);

  const save = useMutation({
    mutationFn: async ({ values, submit }: { values: Values; submit: boolean }) => {
      const body = {
        ...emptyToNull({
          vendor_id: values.vendor_id,
          project_id: values.project_id,
          site_id: values.site_id,
          expected_delivery_date: values.expected_delivery_date,
          delivery_address: values.delivery_address,
          payment_terms: values.payment_terms,
          terms_and_conditions: values.terms_and_conditions,
        }),
        items: values.items.map((i) =>
          emptyToNull({
            material_id: i.material_id,
            unit_id: i.unit_id,
            quantity: i.quantity,
            rate: i.rate,
            discount_pct: i.discount_pct || "0",
            tax_pct: i.tax_pct || "0",
            description: i.description,
            pr_item_id: i.pr_item_id,
          }),
        ),
      };
      const saved = order
        ? await api.put<PurchaseOrderRead>(`/purchase-orders/${order.id}`, body, {
            ifMatch: String(order.version),
          })
        : await api.post<PurchaseOrderRead>("/purchase-orders", body);
      if (!submit) return { saved, submitError: null as string | null };
      try {
        const submitted = await api.post<PurchaseOrderRead>(
          `/purchase-orders/${saved.id}/submit`,
          undefined,
          { ifMatch: String(saved.version) },
        );
        return { saved: submitted, submitError: null };
      } catch (err) {
        return { saved, submitError: describeError(err) };
      }
    },
    onSuccess: async ({ saved, submitError }) => {
      await queryClient.invalidateQueries();
      if (submitError) toast.error(`Saved as a draft, but not submitted: ${submitError}`);
      else
        toast.success(
          saved.status === "PENDING_APPROVAL"
            ? `${saved.po_number} submitted.`
            : `Saved ${saved.po_number}.`,
        );
      navigate(`/purchase-orders/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "vendor_id",
        "project_id",
        "site_id",
        "expected_delivery_date",
        "delivery_address",
        "payment_terms",
        "terms_and_conditions",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  const run = (submit: boolean) =>
    form.handleSubmit((values) => {
      setFormError(null);
      save.mutate({ values, submit });
    });

  return (
    <>
      <PageHeader
        title={order ? `Edit ${order.po_number}` : "New purchase order"}
        subtitle={
          order?.quotation_number
            ? `Raised from ${order.quotation_number}${order.revision ? ` · amendment ${order.revision}` : ""}`
            : "An order without an RFQ. Prefer raising it from a selected quotation."
        }
        crumbs={[
          { label: "Purchase orders", to: "/purchase-orders" },
          ...(order ? [{ label: order.po_number, to: `/purchase-orders/${order.id}` }] : []),
          { label: order ? "Edit" : "New" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <form onSubmit={(e) => void run(false)(e)} className="flex flex-col gap-5" noValidate>
          {formError && <FormAlert>{formError}</FormAlert>}
          {order?.decision_reason && order.status !== "DRAFT" && (
            <FormAlert>
              {order.status.replace(/_/g, " ").toLowerCase()}: {order.decision_reason}
            </FormAlert>
          )}
          {order?.amendment_reason && order.status === "DRAFT" && (
            <FormAlert>Amending: {order.amendment_reason}</FormAlert>
          )}

          <FormSection title="Order">
            <FormGrid>
              <FormField
                label="Vendor"
                required
                error={errors.vendor_id?.message}
                hint={
                  fixedVendor ? "Fixed by the quotation this order was raised from." : undefined
                }
              >
                <Select {...form.register("vendor_id")} disabled={fixedVendor}>
                  <option value="">Choose…</option>
                  {vendors.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Project" required error={errors.project_id?.message}>
                <Select
                  {...form.register("project_id", { onChange: () => form.setValue("site_id", "") })}
                >
                  <option value="">Choose…</option>
                  {projects.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Deliver to site" error={errors.site_id?.message}>
                <Select {...form.register("site_id")} disabled={!projectId}>
                  <option value="">Whole project</option>
                  {sites.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Expected delivery" error={errors.expected_delivery_date?.message}>
                <Input type="date" {...form.register("expected_delivery_date")} />
              </FormField>
              <FormField label="Payment terms" error={errors.payment_terms?.message}>
                <Input {...form.register("payment_terms")} />
              </FormField>
              <FormField label="Delivery address" error={errors.delivery_address?.message}>
                <Input {...form.register("delivery_address")} />
              </FormField>
              <FormField
                label="Terms and conditions"
                className="sm:col-span-2"
                error={errors.terms_and_conditions?.message}
              >
                <Textarea {...form.register("terms_and_conditions")} />
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
                const preview = previews[i];
                return (
                  <div key={field.id} className="rounded-md border border-border p-3">
                    {field.pr_number && (
                      <p className="mb-2 text-xs text-fg-muted">
                        Fulfils a line of{" "}
                        <span className="font-mono text-fg">{field.pr_number}</span>
                      </p>
                    )}
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr_auto]">
                      <FormField
                        label={`Line ${i + 1} material`}
                        error={row?.material_id?.message}
                        className="col-span-2 md:col-span-1"
                      >
                        <Select
                          {...form.register(`items.${i}.material_id`, {
                            onChange: (e: { target: { value: string } }) => {
                              const picked = materials.rows.find((m) => m.id === e.target.value);
                              if (picked) form.setValue(`items.${i}.unit_id`, picked.base_unit_id);
                            },
                          })}
                          disabled={Boolean(field.pr_item_id)}
                        >
                          <option value="">Choose…</option>
                          {materials.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Quantity" error={row?.quantity?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`items.${i}.quantity`)}
                        />
                      </FormField>
                      <FormField label="Unit" error={row?.unit_id?.message}>
                        <Select
                          {...form.register(`items.${i}.unit_id`)}
                          disabled={Boolean(field.pr_item_id)}
                        >
                          <option value="">Choose…</option>
                          {units.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Rate" error={row?.rate?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          aria-label={`Line ${i + 1} rate`}
                          {...form.register(`items.${i}.rate`)}
                        />
                      </FormField>
                      <FormField label="Discount %" error={row?.discount_pct?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`items.${i}.discount_pct`)}
                        />
                      </FormField>
                      <FormField label="Tax %" error={row?.tax_pct?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`items.${i}.tax_pct`)}
                        />
                      </FormField>
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
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <Input
                        className="max-w-md"
                        placeholder="Note for this line (optional)"
                        aria-label={`Line ${i + 1} note`}
                        {...form.register(`items.${i}.description`)}
                      />
                      {preview && (
                        <span className="ml-auto text-sm text-fg-muted">
                          Line total{" "}
                          <span className="tabular font-medium text-fg">
                            {formatMoney(preview.total)}
                          </span>
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
              <div className="flex flex-wrap items-start justify-between gap-3">
                <Button size="sm" onClick={() => lines.append({ ...emptyLine })}>
                  <Plus /> Add line
                </Button>
                <dl className="grid grid-cols-[auto_auto] gap-x-6 gap-y-0.5 text-sm">
                  <dt className="text-fg-muted">Subtotal</dt>
                  <dd className="tabular text-right">{formatMoney(totals.gross)}</dd>
                  <dt className="text-fg-muted">Discount</dt>
                  <dd className="tabular text-right">− {formatMoney(totals.discount)}</dd>
                  <dt className="text-fg-muted">Tax</dt>
                  <dd className="tabular text-right">{formatMoney(totals.tax)}</dd>
                  <dt className="font-semibold">Total</dt>
                  <dd className="tabular text-right text-base font-semibold">
                    {formatMoney(totals.total)}
                  </dd>
                  <dd className="col-span-2 text-right text-xs text-fg-subtle">
                    decides who must approve
                  </dd>
                </dl>
              </div>
            </div>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" loading={save.isPending && !save.variables?.submit}>
              Save draft
            </Button>
            <Button
              variant="primary"
              loading={save.isPending && save.variables?.submit}
              onClick={() => void run(true)()}
            >
              <Send /> Save and submit for approval
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

// --- Detail ---------------------------------------------------------------------

type Dialogs = null | "amend" | "cancel" | "close";

export function PurchaseOrderDetailPage() {
  const { poId = "" } = useParams();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);

  const po = useQuery({
    queryKey: ["purchase-orders", "detail", poId],
    queryFn: () => api.get<PurchaseOrderRead>(`/purchase-orders/${poId}`),
  });
  const trail = useQuery({
    queryKey: ["approvals", "trail", "purchase_order", poId],
    queryFn: () =>
      api.get<ApprovalRequestRead[]>("/approvals/requests", {
        query: { doc_type: "purchase_order", doc_id: poId },
      }),
  });

  const refresh = () => queryClient.invalidateQueries();
  const action = (verb: string, message: string) => ({
    mutationFn: () =>
      api.post<PurchaseOrderRead>(`/purchase-orders/${poId}/${verb}`, undefined, {
        ifMatch: String(po.data?.version),
      }),
    onSuccess: async () => {
      await refresh();
      toast.success(message);
    },
    onError: (err: unknown) => toast.error(describeError(err)),
  });
  const submit = useMutation(action("submit", "Submitted for approval."));
  const send = useMutation(action("send", "Marked as sent to the vendor."));
  const acknowledge = useMutation(action("acknowledge", "Vendor's acknowledgement recorded."));

  if (po.isLoading) return <PageSkeleton />;
  if (po.error || !po.data)
    return <ErrorState error={po.error} onRetry={() => void po.refetch()} />;
  const p = po.data;
  const [current, ...earlier] = trail.data ?? [];
  const money = (value: string | null | undefined) =>
    p.prices_hidden ? (
      <span className="text-fg-subtle">Hidden</span>
    ) : (
      formatMoney(value, { currency: p.currency_code })
    );

  return (
    <>
      <PageHeader
        title={
          <>
            {p.po_number}
            {p.revision > 0 && (
              <span className="ml-2 text-base font-normal text-fg-muted">rev {p.revision}</span>
            )}
          </>
        }
        subtitle={p.vendor_name ?? undefined}
        crumbs={[{ label: "Purchase orders", to: "/purchase-orders" }, { label: p.po_number }]}
        meta={
          <>
            <StatusBadge status={p.status} />
            <span className="text-sm text-fg-muted">
              {p.project_code}
              {p.site_code && ` / ${p.site_code}`}
            </span>
            {!p.prices_hidden && (
              <span className="text-sm text-fg-muted">
                Total <span className="tabular font-medium text-fg">{money(p.total_amount)}</span>
              </span>
            )}
            {p.prices_hidden && (
              <Tag>
                <Lock className="mr-1 size-3" aria-hidden /> Prices hidden for your role
              </Tag>
            )}
          </>
        }
        actions={
          <>
            {!p.prices_hidden && (
              <Button
                onClick={() =>
                  void downloadFile(`/purchase-orders/${p.id}/pdf`, `${p.po_number}.pdf`).catch(
                    (err: unknown) => toast.error(describeError(err)),
                  )
                }
              >
                <Download /> Download PDF
              </Button>
            )}
            {p.can_edit && (
              <Button asChild>
                <Link to={`/purchase-orders/${p.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {p.can_submit && (
              <Button variant="primary" onClick={() => submit.mutate()} loading={submit.isPending}>
                <Send /> Submit for approval
              </Button>
            )}
            {p.can_send && (
              <Button variant="primary" onClick={() => send.mutate()} loading={send.isPending}>
                <Truck /> Mark as sent
              </Button>
            )}
            {p.can_acknowledge && (
              <Button onClick={() => acknowledge.mutate()} loading={acknowledge.isPending}>
                <CheckCheck /> Vendor acknowledged
              </Button>
            )}
            {p.can_amend && (
              <Button onClick={() => setDialog("amend")}>
                <FilePenLine /> Amend
              </Button>
            )}
            {p.can_close && <Button onClick={() => setDialog("close")}>Close order</Button>}
            {p.can_cancel && (
              <Button variant="danger" onClick={() => setDialog("cancel")}>
                <Ban /> Cancel order
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {p.decision_reason && ["REJECTED", "CHANGES_REQUESTED"].includes(p.status) && (
          <FormAlert>
            {p.status === "REJECTED" ? "Rejected" : "Changes requested"}: {p.decision_reason}
          </FormAlert>
        )}
        {p.amendment_reason && p.status === "DRAFT" && (
          <FormAlert>
            Reopened for amendment {p.revision}: {p.amendment_reason}. It must be approved again.
          </FormAlert>
        )}
        {p.cancel_reason && <FormAlert>Cancelled: {p.cancel_reason}</FormAlert>}
        {p.close_reason && <FormAlert>Closed: {p.close_reason}</FormAlert>}

        <Section title="Details">
          <FieldGrid
            items={[
              {
                label: "Vendor",
                value: p.vendor_name ? (
                  <Link to={`/vendors/${p.vendor_id}`} className="text-primary hover:underline">
                    {p.vendor_code} — {p.vendor_name}
                  </Link>
                ) : null,
              },
              {
                label: "Project",
                value: p.project_name ? `${p.project_code} — ${p.project_name}` : p.project_code,
              },
              {
                label: "Deliver to",
                value: p.site_name ? `${p.site_code} — ${p.site_name}` : null,
              },
              { label: "Order date", value: formatDate(p.po_date) },
              {
                label: "Expected delivery",
                value: p.expected_delivery_date ? formatDate(p.expected_delivery_date) : null,
              },
              { label: "Payment terms", value: p.payment_terms },
              {
                label: "Raised from",
                value: p.quotation_id ? (
                  <span>
                    <Link
                      to={`/quotations/${p.quotation_id}`}
                      className="font-mono text-primary hover:underline"
                    >
                      {p.quotation_number}
                    </Link>
                    {p.rfq_id && (
                      <>
                        {" "}
                        on{" "}
                        <Link
                          to={`/rfqs/${p.rfq_id}`}
                          className="font-mono text-primary hover:underline"
                        >
                          {p.rfq_number}
                        </Link>
                      </>
                    )}
                  </span>
                ) : (
                  "Direct order (no RFQ)"
                ),
              },
              { label: "Sent", value: p.sent_at ? formatDateTime(p.sent_at) : null },
              {
                label: "Acknowledged",
                value: p.acknowledged_at ? formatDateTime(p.acknowledged_at) : null,
              },
              { label: "Delivery address", value: p.delivery_address, wide: true },
              { label: "Terms and conditions", value: p.terms_and_conditions, wide: true },
            ]}
          />
        </Section>

        <Section title="Lines">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Ordered lines</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Material
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Ordered
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Received
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Rate
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Disc.
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Tax
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Line total
                  </th>
                </tr>
              </thead>
              <tbody>
                {p.items.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{i.material_sku}</span>{" "}
                      <span className="font-medium">{i.material_name}</span>
                      {i.pr_number && <p className="text-xs text-fg-muted">For {i.pr_number}</p>}
                      {i.description && <p className="text-xs text-fg-muted">{i.description}</p>}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.quantity, i.unit_code ?? undefined)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.received_quantity)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {money(i.rate)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {p.prices_hidden
                        ? ""
                        : Number(i.discount_pct)
                          ? `${plain(i.discount_pct)}%`
                          : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {p.prices_hidden ? "" : Number(i.tax_pct) ? `${plain(i.tax_pct)}%` : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {money(i.line_total)}
                    </td>
                  </tr>
                ))}
              </tbody>
              {!p.prices_hidden && (
                <tfoot>
                  <tr className="border-t border-border-strong bg-surface">
                    <td colSpan={6} className="px-4 py-1 text-right text-fg-muted">
                      Subtotal
                    </td>
                    <td data-numeric className="px-4 py-1">
                      {money(p.subtotal)}
                    </td>
                  </tr>
                  <tr className="bg-surface">
                    <td colSpan={6} className="px-4 py-1 text-right text-fg-muted">
                      Discount
                    </td>
                    <td data-numeric className="px-4 py-1">
                      − {money(p.discount_amount)}
                    </td>
                  </tr>
                  <tr className="bg-surface">
                    <td colSpan={6} className="px-4 py-1 text-right text-fg-muted">
                      Tax
                    </td>
                    <td data-numeric className="px-4 py-1">
                      {money(p.tax_amount)}
                    </td>
                  </tr>
                  <tr className="bg-surface">
                    <td colSpan={6} className="px-4 py-2 text-right font-semibold">
                      Total
                    </td>
                    <td data-numeric className="px-4 py-2 font-semibold">
                      {money(p.total_amount)}
                    </td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </Section>

        <Section title="Approval">
          {trail.isLoading ? (
            <p className="px-4 py-4 text-sm text-fg-muted">Loading…</p>
          ) : trail.isError ? (
            <ErrorState error={trail.error} onRetry={() => void trail.refetch()} />
          ) : !current ? (
            <p className="px-4 py-4 text-sm text-fg-muted">
              Not submitted yet. When it is, the approval chain and each decision appear here.
            </p>
          ) : (
            <>
              <ApprovalTrail request={current} />
              <DecisionActions
                request={current}
                label={p.po_number}
                onDone={() => void refresh()}
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
      </PageBody>

      <ConfirmDialog
        open={dialog === "amend"}
        onOpenChange={(o) => setDialog(o ? "amend" : null)}
        title={`Amend ${p.po_number}?`}
        description="The order goes back to draft and must be approved again. The quantities it claimed on the purchase request are released until then."
        confirmLabel="Reopen for amendment"
        reason={{ label: "What is changing and why", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(
            `/purchase-orders/${p.id}/amend`,
            { reason },
            { ifMatch: String(p.version) },
          );
          await refresh();
          toast.success(`${p.po_number} reopened for amendment.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "cancel"}
        onOpenChange={(o) => setDialog(o ? "cancel" : null)}
        title={`Cancel ${p.po_number}?`}
        description="A cancelled order cannot be reopened. Its quantities return to the purchase request."
        confirmLabel={`Cancel ${p.po_number}`}
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/purchase-orders/${p.id}/cancel`, { reason });
          await refresh();
          toast.success(`${p.po_number} cancelled.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "close"}
        onOpenChange={(o) => setDialog(o ? "close" : null)}
        title={`Close ${p.po_number}?`}
        description="Ends the order with goods still owing. Anything not received returns to the purchase request so it can be sourced elsewhere."
        confirmLabel="Close order"
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/purchase-orders/${p.id}/close`, { reason });
          await refresh();
          toast.success(`${p.po_number} closed.`);
        }}
      />
    </>
  );
}
