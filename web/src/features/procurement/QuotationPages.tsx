import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FilePlus2, Pencil, Star, Undo2, XCircle } from "lucide-react";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { api } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import { toast } from "@/lib/toast";
import { cn, formatDate, formatDateTime, formatMoney, formatQuantity } from "@/lib/utils";
import type {
  ComparisonColumnRead,
  ComparisonRead,
  PurchaseOrderRead,
  QuotationRead,
  RfqRead,
} from "@/types/models";

import { plain, previewLine, sumPreviews } from "./sourcing";

const OPEN = new Set(["RECEIVED", "SHORTLISTED"]);
const SELECTION_MIN = 10;

// --- Form -----------------------------------------------------------------------

const optionalDecimal = (message: string) =>
  z
    .string()
    .trim()
    .refine((v) => v === "" || DECIMAL_RE.test(v), message);

const lineSchema = z.object({
  include: z.boolean(),
  rfq_item_id: z.string(),
  label: z.string(),
  unit_code: z.string(),
  requested: z.string(),
  estimated_rate: z.string(),
  quantity: z.string().trim(),
  rate: z.string().trim(),
  discount_pct: optionalDecimal("A percentage"),
  tax_pct: optionalDecimal("A percentage"),
  delivery_days: z.string().trim(),
  remarks: z.string(),
});

const schema = z
  .object({
    vendor_id: z.string().min(1, "Choose the vendor that quoted"),
    vendor_reference: z.string().max(80),
    quote_date: z.string().min(1, "Required"),
    valid_until: z.string(),
    delivery_days: z.string().trim(),
    payment_terms: z.string().max(200),
    notes: z.string().max(2000),
    items: z.array(lineSchema),
  })
  .superRefine((v, ctx) => {
    if (v.valid_until && v.valid_until < v.quote_date) {
      ctx.addIssue({
        code: "custom",
        path: ["valid_until"],
        message: "Cannot be before the quotation date",
      });
    }
    if (!v.items.some((i) => i.include)) {
      ctx.addIssue({ code: "custom", path: ["items"], message: "Quote at least one line" });
    }
    v.items.forEach((item, index) => {
      if (!item.include) return;
      const at = (field: string, message: string) =>
        ctx.addIssue({ code: "custom", path: ["items", index, field], message });
      if (!DECIMAL_RE.test(item.rate)) at("rate", "The vendor's rate");
      if (!DECIMAL_RE.test(item.quantity) || Number(item.quantity) <= 0) {
        at("quantity", "A positive number");
      } else if (Number(item.quantity) > Number(item.requested)) {
        at("quantity", `At most ${plain(item.requested)} requested`);
      }
      if (item.discount_pct !== "" && Number(item.discount_pct) > 100) at("discount_pct", "0–100");
      if (item.tax_pct !== "" && Number(item.tax_pct) > 100) at("tax_pct", "0–100");
      if (item.delivery_days !== "" && !/^\d+$/.test(item.delivery_days)) {
        at("delivery_days", "Whole days");
      }
    });
  });
type Values = z.infer<typeof schema>;

const today = () => new Date().toISOString().slice(0, 10);

/** Record a quotation against an issued RFQ, or correct one that has not been selected. */
export function QuotationFormPage() {
  const { rfqId, quotationId } = useParams();
  const quotation = useQuery({
    queryKey: ["quotations", "detail", quotationId],
    queryFn: () => api.get<QuotationRead>(`/quotations/${quotationId}`),
    enabled: Boolean(quotationId),
    staleTime: 0,
  });
  const owningRfq = rfqId ?? quotation.data?.rfq_id;
  const rfq = useQuery({
    queryKey: ["rfqs", "detail", owningRfq],
    queryFn: () => api.get<RfqRead>(`/rfqs/${owningRfq}`),
    enabled: Boolean(owningRfq),
    staleTime: 0,
  });

  if (quotationId && quotation.isLoading) return <PageSkeleton />;
  if (quotationId && quotation.error) {
    return <ErrorState error={quotation.error} onRetry={() => void quotation.refetch()} />;
  }
  if (!owningRfq || rfq.isLoading) return <PageSkeleton />;
  if (rfq.error || !rfq.data) {
    return <ErrorState error={rfq.error} onRetry={() => void rfq.refetch()} />;
  }
  return <QuotationForm rfq={rfq.data} quotation={quotation.data} />;
}

function QuotationForm({ rfq, quotation }: { rfq: RfqRead; quotation?: QuotationRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [search] = useSearchParams();
  const [formError, setFormError] = useState<string | null>(null);

  const quoted = new Map(quotation?.items.map((i) => [i.rfq_item_id, i]));
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      vendor_id: quotation?.vendor_id ?? search.get("vendor") ?? "",
      vendor_reference: quotation?.vendor_reference ?? "",
      quote_date: quotation?.quote_date ?? today(),
      valid_until: quotation?.valid_until ?? "",
      delivery_days: quotation?.delivery_days?.toString() ?? "",
      payment_terms: quotation?.payment_terms ?? "",
      notes: quotation?.notes ?? "",
      items: rfq.items.map((item) => {
        const existing = quoted.get(item.id);
        return {
          include: quotation ? Boolean(existing) : true,
          rfq_item_id: item.id,
          label: `${item.material_sku ?? ""} — ${item.material_name ?? ""}`,
          unit_code: item.unit_code ?? "",
          requested: item.quantity,
          estimated_rate: item.estimated_rate ?? "",
          quantity: plain(existing?.quantity ?? item.quantity),
          rate: plain(existing?.rate),
          discount_pct: plain(existing?.discount_pct),
          tax_pct: plain(existing?.tax_pct),
          delivery_days: existing?.delivery_days?.toString() ?? "",
          remarks: existing?.remarks ?? "",
        };
      }),
    },
  });
  const lines = useFieldArray({ control: form.control, name: "items" });
  const errors = form.formState.errors;
  const watched = form.watch("items");

  const previews = watched.map((i) =>
    i.include ? previewLine(i.quantity, i.rate, i.discount_pct, i.tax_pct) : null,
  );
  const totals = sumPreviews(previews);

  const save = useMutation({
    mutationFn: (values: Values) => {
      const body = {
        ...emptyToNull({
          vendor_id: values.vendor_id,
          vendor_reference: values.vendor_reference,
          quote_date: values.quote_date,
          valid_until: values.valid_until,
          delivery_days: values.delivery_days === "" ? "" : Number(values.delivery_days),
          payment_terms: values.payment_terms,
          notes: values.notes,
        }),
        items: values.items
          .filter((i) => i.include)
          .map((i) =>
            emptyToNull({
              rfq_item_id: i.rfq_item_id,
              rate: i.rate,
              quantity: i.quantity,
              discount_pct: i.discount_pct || "0",
              tax_pct: i.tax_pct || "0",
              delivery_days: i.delivery_days === "" ? "" : Number(i.delivery_days),
              remarks: i.remarks,
            }),
          ),
      };
      return quotation
        ? api.put<QuotationRead>(`/quotations/${quotation.id}`, body, {
            ifMatch: String(quotation.version),
          })
        : api.post<QuotationRead>(`/rfqs/${rfq.id}/quotations`, body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(`Recorded ${saved.quotation_number}.`);
      navigate(`/quotations/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "vendor_id",
        "quote_date",
        "valid_until",
        "delivery_days",
        "payment_terms",
        "notes",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  const vendorChoices = rfq.vendors.filter(
    (v) => v.vendor_id === quotation?.vendor_id || v.status === "INVITED",
  );

  return (
    <>
      <PageHeader
        title={quotation ? `Edit ${quotation.quotation_number}` : "Record a quotation"}
        subtitle={`${rfq.rfq_number} — ${rfq.title}`}
        crumbs={[
          { label: "RFQs", to: "/rfqs" },
          { label: rfq.rfq_number, to: `/rfqs/${rfq.id}` },
          { label: quotation ? quotation.quotation_number : "Record quotation" },
        ]}
      />
      <PageBody className="max-w-6xl">
        <form
          onSubmit={(e) => {
            setFormError(null);
            void form.handleSubmit((v) => save.mutate(v))(e);
          }}
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}

          <FormSection title="What the vendor sent">
            <FormGrid>
              <FormField label="Vendor" required error={errors.vendor_id?.message}>
                <Select {...form.register("vendor_id")} disabled={Boolean(quotation)}>
                  <option value="">Choose…</option>
                  {vendorChoices.map((v) => (
                    <option key={v.vendor_id} value={v.vendor_id}>
                      {v.vendor_code} — {v.vendor_name}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Vendor's reference" error={errors.vendor_reference?.message}>
                <Input {...form.register("vendor_reference")} />
              </FormField>
              <FormField label="Quotation date" required error={errors.quote_date?.message}>
                <Input type="date" {...form.register("quote_date")} />
              </FormField>
              <FormField
                label="Valid until"
                hint="A quotation past its date cannot be selected."
                error={errors.valid_until?.message}
              >
                <Input type="date" {...form.register("valid_until")} />
              </FormField>
              <FormField label="Delivery (days)" error={errors.delivery_days?.message}>
                <Input
                  inputMode="numeric"
                  className="tabular"
                  {...form.register("delivery_days")}
                />
              </FormField>
              <FormField label="Payment terms" error={errors.payment_terms?.message}>
                <Input
                  {...form.register("payment_terms")}
                  placeholder="e.g. 30 days from delivery"
                />
              </FormField>
              <FormField label="Notes" className="sm:col-span-2" error={errors.notes?.message}>
                <Textarea {...form.register("notes")} />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Prices, line by line">
            <p className="mb-2 text-xs text-fg-muted">
              Untick a line the vendor did not quote. Discount comes off before tax.
            </p>
            {errors.items?.message && (
              <p className="mb-2 text-xs text-danger">{errors.items.message}</p>
            )}
            {errors.items?.root?.message && (
              <p className="mb-2 text-xs text-danger">{errors.items.root.message}</p>
            )}
            <div className="flex flex-col gap-3">
              {lines.fields.map((field, i) => {
                const row = errors.items?.[i];
                const preview = previews[i];
                const included = watched[i]?.include;
                return (
                  <div
                    key={field.id}
                    className={cn("rounded-md border border-border p-3", !included && "opacity-60")}
                  >
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <label className="flex items-center gap-2 text-sm font-medium">
                        <Checkbox
                          {...form.register(`items.${i}.include`)}
                          aria-label={`Line ${i + 1} quoted`}
                        />
                        {field.label}
                      </label>
                      <span className="text-xs text-fg-muted">
                        {plain(field.requested)} {field.unit_code} requested
                        {field.estimated_rate &&
                          ` · budgeted at ${formatMoney(field.estimated_rate)}`}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
                      <FormField label="Quantity" error={row?.quantity?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          disabled={!included}
                          {...form.register(`items.${i}.quantity`)}
                        />
                      </FormField>
                      <FormField label="Rate" error={row?.rate?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          disabled={!included}
                          aria-label={`Line ${i + 1} rate`}
                          {...form.register(`items.${i}.rate`)}
                        />
                      </FormField>
                      <FormField label="Discount %" error={row?.discount_pct?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          disabled={!included}
                          {...form.register(`items.${i}.discount_pct`)}
                        />
                      </FormField>
                      <FormField label="Tax %" error={row?.tax_pct?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          disabled={!included}
                          {...form.register(`items.${i}.tax_pct`)}
                        />
                      </FormField>
                      <FormField label="Days" error={row?.delivery_days?.message}>
                        <Input
                          inputMode="numeric"
                          className="tabular"
                          disabled={!included}
                          {...form.register(`items.${i}.delivery_days`)}
                        />
                      </FormField>
                      <div className="flex items-end pb-1 text-right text-sm md:justify-end">
                        {preview && (
                          <span>
                            <span className="block text-xs text-fg-muted">Line total</span>
                            <span className="tabular font-medium">
                              {formatMoney(preview.total)}
                            </span>
                          </span>
                        )}
                      </div>
                    </div>
                    <Input
                      className="mt-2 max-w-md"
                      placeholder="Remarks (optional)"
                      aria-label={`Line ${i + 1} remarks`}
                      disabled={!included}
                      {...form.register(`items.${i}.remarks`)}
                    />
                  </div>
                );
              })}
              <dl className="ml-auto grid grid-cols-[auto_auto] gap-x-6 gap-y-0.5 text-sm">
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
              </dl>
            </div>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {quotation ? "Save changes" : "Record quotation"}
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

type Dialogs = null | "select" | "reject";

export function QuotationDetailPage() {
  const { quotationId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);

  const query = useQuery({
    queryKey: ["quotations", "detail", quotationId],
    queryFn: () => api.get<QuotationRead>(`/quotations/${quotationId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  const shortlist = useMutation({
    mutationFn: () => api.post<QuotationRead>(`/quotations/${quotationId}/shortlist`),
    onSuccess: refresh,
    onError: (err) => toast.error(describeError(err)),
  });
  const withdraw = useMutation({
    mutationFn: () => api.post<QuotationRead>(`/quotations/${quotationId}/withdraw-selection`),
    onSuccess: async () => {
      await refresh();
      toast.success("Selection withdrawn.");
    },
    onError: (err) => toast.error(describeError(err)),
  });
  const order = useMutation({
    mutationFn: () => api.post<PurchaseOrderRead>(`/purchase-orders/from-quotation/${quotationId}`),
    onSuccess: async (po) => {
      await refresh();
      toast.success(`Draft ${po.po_number} raised.`);
      navigate(`/purchase-orders/${po.id}`);
    },
    onError: (err) => toast.error(describeError(err)),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const q = query.data;

  return (
    <>
      <PageHeader
        title={q.quotation_number}
        subtitle={q.vendor_name ?? undefined}
        crumbs={[
          { label: "RFQs", to: "/rfqs" },
          { label: q.rfq_number ?? "RFQ", to: `/rfqs/${q.rfq_id}` },
          { label: q.quotation_number },
        ]}
        meta={
          <>
            <StatusBadge status={q.status} />
            <span className="text-sm text-fg-muted">
              Total{" "}
              <span className="tabular font-medium text-fg">
                {formatMoney(q.total_amount, { currency: q.currency_code })}
              </span>
            </span>
          </>
        }
        actions={
          <>
            <Button asChild>
              <Link to={`/rfqs/${q.rfq_id}/comparison`}>Compare</Link>
            </Button>
            {q.can_edit && (
              <Button asChild>
                <Link to={`/quotations/${q.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {q.can_edit && q.status === "RECEIVED" && (
              <Button onClick={() => shortlist.mutate()} loading={shortlist.isPending}>
                <Star /> Shortlist
              </Button>
            )}
            {q.can_select && (
              <Button variant="primary" onClick={() => setDialog("select")}>
                <CheckCircle2 /> Select this quotation
              </Button>
            )}
            {q.can_select && (
              <Button variant="danger" onClick={() => setDialog("reject")}>
                <XCircle /> Reject
              </Button>
            )}
            {q.can_withdraw && (
              <Button onClick={() => withdraw.mutate()} loading={withdraw.isPending}>
                <Undo2 /> Withdraw selection
              </Button>
            )}
            {q.can_create_order && (
              <Button variant="primary" onClick={() => order.mutate()} loading={order.isPending}>
                <FilePlus2 /> Create purchase order
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {q.status === "SELECTED" && q.selection_reason && (
          <div
            role="note"
            className="rounded-md border border-success/40 bg-success-bg px-4 py-3 text-sm"
          >
            <p className="font-medium text-success">
              Selected by {q.selected_by_name ?? "—"}
              {q.selected_at && ` · ${formatDateTime(q.selected_at)}`}
            </p>
            <p className="mt-1">{q.selection_reason}</p>
          </div>
        )}
        {q.status === "REJECTED" && q.reject_reason && (
          <FormAlert>Rejected: {q.reject_reason}</FormAlert>
        )}
        {q.purchase_order_id && (
          <p className="text-sm">
            Ordered on{" "}
            <Link
              to={`/purchase-orders/${q.purchase_order_id}`}
              className="font-mono text-primary hover:underline"
            >
              {q.purchase_order_number}
            </Link>
            .
          </p>
        )}

        <Section title="Details">
          <FieldGrid
            items={[
              {
                label: "Vendor",
                value: q.vendor_name ? `${q.vendor_code} — ${q.vendor_name}` : null,
              },
              { label: "Vendor's reference", value: q.vendor_reference },
              { label: "Quotation date", value: formatDate(q.quote_date) },
              { label: "Valid until", value: q.valid_until ? formatDate(q.valid_until) : null },
              {
                label: "Delivery",
                value: q.delivery_days != null ? `${q.delivery_days} days` : null,
              },
              { label: "Payment terms", value: q.payment_terms },
              { label: "Notes", value: q.notes, wide: true },
            ]}
          />
        </Section>

        <Section title="Lines">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Quoted lines</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Material
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Quantity
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Rate
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Disc.
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Net rate
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
                {q.items.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{i.material_sku}</span>{" "}
                      <span className="font-medium">{i.material_name}</span>
                      {i.remarks && <p className="text-xs text-fg-muted">{i.remarks}</p>}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.quantity, i.unit_code ?? undefined)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatMoney(i.rate)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(i.discount_pct) ? `${plain(i.discount_pct)}%` : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {i.net_rate ? formatMoney(i.net_rate) : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(i.tax_pct) ? `${plain(i.tax_pct)}%` : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatMoney(i.line_total)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border-strong bg-surface">
                  <td colSpan={6} className="px-4 py-2 text-right font-semibold">
                    Total ({q.currency_code})
                  </td>
                  <td data-numeric className="px-4 py-2 font-semibold">
                    {formatMoney(q.total_amount, { currency: q.currency_code })}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Section>
      </PageBody>

      <SelectDialog
        open={dialog === "select"}
        onOpenChange={(o) => setDialog(o ? "select" : null)}
        quotationId={q.id}
        vendorName={q.vendor_name ?? q.quotation_number}
        version={q.version}
      />
      <ConfirmDialog
        open={dialog === "reject"}
        onOpenChange={(o) => setDialog(o ? "reject" : null)}
        title={`Reject ${q.quotation_number}?`}
        description="A rejected quotation drops out of the comparison."
        confirmLabel="Reject quotation"
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/quotations/${q.id}/reject`, { reason });
          await refresh();
        }}
      />
    </>
  );
}

/** Choosing a vendor is a person's decision, and the reason is part of the record (§10). */
function SelectDialog({
  open,
  onOpenChange,
  quotationId,
  vendorName,
  version,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  quotationId: string;
  vendorName: string;
  /** Sent as If-Match when known, so a quotation edited meanwhile is not selected blindly. */
  version?: number;
}) {
  const queryClient = useQueryClient();
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Select ${vendorName}?`}
      description="Say why this vendor is the right choice — including when it is the cheapest. The reason is stored with the quotation and in the audit log."
      confirmLabel={`Select ${vendorName}`}
      reason={{
        label: "Reason for choosing this quotation",
        required: true,
        minLength: SELECTION_MIN,
        placeholder: "e.g. Lowest total for the full quantity; can deliver within the week",
      }}
      onConfirm={async (reason) => {
        await api.post(
          `/quotations/${quotationId}/select`,
          { reason },
          version === undefined ? undefined : { ifMatch: String(version) },
        );
        await queryClient.invalidateQueries();
        toast.success(`${vendorName} selected.`);
      }}
    />
  );
}

// --- Comparison -----------------------------------------------------------------

export function ComparisonPage() {
  const { rfqId = "" } = useParams();
  const [choosing, setChoosing] = useState<ComparisonColumnRead | null>(null);

  const query = useQuery({
    queryKey: ["rfqs", "comparison", rfqId],
    queryFn: () => api.get<ComparisonRead>(`/rfqs/${rfqId}/comparison`),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const grid = query.data;
  const selected = grid.columns.find((c) => c.quotation_status === "SELECTED");
  const canPick = (c: ComparisonColumnRead) =>
    grid.can_select && c.quotation_id && OPEN.has(c.quotation_status ?? "");

  return (
    <>
      <PageHeader
        title={`Compare quotations — ${grid.rfq_number}`}
        subtitle="Every vendor's answer, line by line. The lowest net rate on each line is marked; the choice is yours, and needs a reason."
        crumbs={[
          { label: "RFQs", to: "/rfqs" },
          { label: grid.rfq_number, to: `/rfqs/${grid.rfq_id}` },
          { label: "Comparison" },
        ]}
        meta={<StatusBadge status={grid.rfq_status} />}
      />
      <PageBody>
        {selected && (
          <div
            role="note"
            className="rounded-md border border-success/40 bg-success-bg px-4 py-3 text-sm"
          >
            <p className="font-medium text-success">
              Selected: {selected.vendor_name} ({selected.quotation_number})
            </p>
            {selected.selection_reason && <p className="mt-1">{selected.selection_reason}</p>}
            <Link
              to={`/quotations/${selected.quotation_id}`}
              className="mt-1 inline-block text-primary hover:underline"
            >
              Open the quotation to raise the purchase order
            </Link>
          </div>
        )}

        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <caption className="sr-only">
              Quotations for {grid.rfq_number}, one column per vendor
            </caption>
            <thead className="bg-surface">
              <tr>
                <th
                  scope="col"
                  className="w-64 px-4 py-2 text-left text-xs font-semibold text-fg-muted"
                >
                  Line
                </th>
                {grid.columns.map((c) => (
                  <th
                    key={c.vendor_id}
                    scope="col"
                    className={cn(
                      "min-w-44 border-l border-border px-4 py-2 text-left align-top",
                      c.quotation_status === "SELECTED" && "bg-success-bg",
                    )}
                  >
                    <p className="font-semibold">{c.vendor_name}</p>
                    <p className="mt-0.5 text-xs font-normal text-fg-muted">
                      {c.quotation_id ? (
                        <>
                          <Link
                            to={`/quotations/${c.quotation_id}`}
                            className="font-mono text-primary hover:underline"
                          >
                            {c.quotation_number}
                          </Link>{" "}
                          <StatusBadge status={c.quotation_status ?? ""} />
                        </>
                      ) : (
                        <StatusBadge status={c.invitation_status} />
                      )}
                    </p>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.rows.map((row) => (
                <tr key={row.rfq_item_id} className="border-t border-border align-top">
                  <th scope="row" className="px-4 py-3 text-left font-normal">
                    <span className="font-mono text-xs text-fg-muted">{row.material_sku}</span>{" "}
                    <span className="font-medium">{row.material_name}</span>
                    <p className="text-xs text-fg-muted">
                      {formatQuantity(row.quantity, row.unit_code ?? undefined)}
                      {row.estimated_rate && ` · budgeted ${formatMoney(row.estimated_rate)}`}
                    </p>
                  </th>
                  {grid.columns.map((c) => {
                    const cell = row.cells[c.vendor_id];
                    return (
                      <td
                        key={c.vendor_id}
                        className={cn(
                          "border-l border-border px-4 py-3",
                          cell?.is_lowest && "bg-success-bg/50",
                        )}
                      >
                        {cell ? (
                          <div className="tabular-nums">
                            <p className="flex items-center gap-1.5">
                              <span className="text-base font-semibold">
                                {formatMoney(cell.net_rate)}
                              </span>
                              {cell.is_lowest && <Tag className="text-success">Lowest</Tag>}
                            </p>
                            <p className="text-xs text-fg-muted">
                              {Number(cell.discount_pct) > 0
                                ? `${formatMoney(cell.rate)} less ${plain(cell.discount_pct)}%`
                                : "no discount"}
                              {Number(cell.tax_pct) > 0 && ` · tax ${plain(cell.tax_pct)}%`}
                            </p>
                            <p className="text-xs text-fg-muted">
                              Line {formatMoney(cell.line_total)}
                              {cell.delivery_days != null && ` · ${cell.delivery_days} d`}
                            </p>
                            {cell.is_partial && (
                              <p className="text-xs font-medium text-warning">
                                Quotes only {formatQuantity(cell.quantity)} of{" "}
                                {formatQuantity(row.quantity)}
                              </p>
                            )}
                            {cell.remarks && (
                              <p className="text-xs italic text-fg-muted">{cell.remarks}</p>
                            )}
                          </div>
                        ) : (
                          <span className="text-fg-subtle">Not quoted</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-border-strong bg-surface">
                <th scope="row" className="px-4 py-3 text-left text-sm font-semibold">
                  Total
                </th>
                {grid.columns.map((c) => (
                  <td key={c.vendor_id} className="border-l border-border px-4 py-3">
                    {c.total_amount ? (
                      <div className="tabular-nums">
                        <p className="flex items-center gap-1.5 text-base font-semibold">
                          {formatMoney(c.total_amount, { currency: grid.currency_code })}
                          {c.is_lowest_total && <Tag className="text-success">Lowest</Tag>}
                        </p>
                        <p className="text-xs text-fg-muted">
                          {c.covers_all
                            ? "Quotes every line in full"
                            : `Quotes ${c.lines_quoted} of ${grid.rows.length} lines — not comparable`}
                        </p>
                      </div>
                    ) : (
                      <span className="text-fg-subtle">—</span>
                    )}
                  </td>
                ))}
              </tr>
              <tr className="border-t border-border">
                <th scope="row" className="px-4 py-2 text-left text-xs font-semibold text-fg-muted">
                  Delivery
                </th>
                {grid.columns.map((c) => (
                  <td key={c.vendor_id} className="border-l border-border px-4 py-2 text-xs">
                    {c.delivery_days != null ? `${c.delivery_days} days` : "—"}
                  </td>
                ))}
              </tr>
              <tr className="border-t border-border">
                <th scope="row" className="px-4 py-2 text-left text-xs font-semibold text-fg-muted">
                  Payment terms
                </th>
                {grid.columns.map((c) => (
                  <td key={c.vendor_id} className="border-l border-border px-4 py-2 text-xs">
                    {c.payment_terms ?? "—"}
                  </td>
                ))}
              </tr>
              <tr className="border-t border-border">
                <th scope="row" className="px-4 py-2 text-left text-xs font-semibold text-fg-muted">
                  Valid until
                </th>
                {grid.columns.map((c) => (
                  <td key={c.vendor_id} className="border-l border-border px-4 py-2 text-xs">
                    {c.valid_until ? formatDate(c.valid_until) : "—"}
                  </td>
                ))}
              </tr>
              {grid.can_select && (
                <tr className="border-t border-border">
                  <th
                    scope="row"
                    className="px-4 py-3 text-left text-xs font-semibold text-fg-muted"
                  >
                    Decision
                  </th>
                  {grid.columns.map((c) => (
                    <td key={c.vendor_id} className="border-l border-border px-4 py-3">
                      {canPick(c) ? (
                        <Button
                          size="sm"
                          variant="primary"
                          onClick={() => setChoosing(c)}
                          aria-label={`Select ${c.vendor_name}`}
                        >
                          Select…
                        </Button>
                      ) : c.quotation_status === "SELECTED" ? (
                        <span className="text-xs font-medium text-success">Selected</span>
                      ) : (
                        <span className="text-xs text-fg-subtle">—</span>
                      )}
                    </td>
                  ))}
                </tr>
              )}
            </tfoot>
          </table>
        </div>
        <p className="text-xs text-fg-subtle">
          Compared on the net rate — the vendor's rate after discount, before tax. A vendor who
          quoted only some lines is never marked as the lowest total.
        </p>
      </PageBody>

      {choosing && (
        <SelectDialog
          open
          onOpenChange={(o) => {
            if (!o) setChoosing(null);
          }}
          quotationId={choosing.quotation_id ?? ""}
          vendorName={choosing.vendor_name ?? "this vendor"}
        />
      )}
    </>
  );
}
