import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Plus, Trash2 } from "lucide-react";
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
import { useBankAccountOptions, usePagedList, useVendorOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatMoney, humanize } from "@/lib/utils";
import type {
  PaymentCreate,
  PaymentListItem,
  PaymentRead,
  PaymentRequestListItem,
  VendorInvoiceListItem,
} from "@/types/models";

const METHODS = ["BANK_TRANSFER", "CHEQUE", "CASH", "ONLINE"];
const STATUS_OPTIONS = ["ISSUED", "CLEARED", "CANCELLED"].map((value) => ({
  value,
  label: humanize(value),
}));

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<PaymentListItem>();

export function PaymentListPage() {
  const list = useListParams({ sort: "-payment_date" });
  const query = usePagedList<PaymentListItem>("payments", "/finance/payments", list.query);
  const vendors = useVendorOptions();

  const columns = useMemo(
    () => [
      col.accessor("payment_number", {
        header: "Payment",
        meta: { alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/finance/payments/${c.row.original.id}`}
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
      col.accessor("method", { header: "Method", cell: (c) => humanize(c.getValue()) }),
      col.accessor("status", {
        header: "Status",
        cell: (c) => <StatusBadge status={c.getValue()} />,
      }),
      col.accessor("gross_amount", {
        header: "Gross",
        meta: { numeric: true },
        cell: (c) => formatMoney(c.getValue()),
      }),
      col.accessor("net_amount", {
        header: "Net",
        meta: { numeric: true },
        cell: (c) => formatMoney(c.getValue()),
      }),
      col.accessor("allocated_amount", {
        header: "Allocated",
        meta: { numeric: true },
        cell: (c) => formatMoney(c.getValue()),
      }),
      col.accessor("payment_date", { header: "Date", cell: (c) => formatDate(c.getValue()) }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Payments"
        subtitle="Money that has actually left the door — executed against an approved payment
          request, and posted to the GL the moment it is issued."
        actions={
          <PermissionGate permission="finance.payment.execute">
            <Button variant="primary" asChild>
              <Link to="/finance/payments/new">
                <Plus /> New payment
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="payments"
          caption="Payments"
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
          getRowHref={(r) => `/finance/payments/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Payment number or instrument…"
              savedViewsId="payments"
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

// --- Form (execute) ----------------------------------------------------------------

const schema = z.object({
  payment_request_id: z.string().min(1, "Choose an approved request"),
  payment_date: z.string().min(1, "Required"),
  method: z.enum(["BANK_TRANSFER", "CHEQUE", "CASH", "ONLINE"]),
  bank_account_id: z.string().min(1, "Choose an account"),
  instrument_no: z.string(),
  withholding_amount: z.string().regex(DECIMAL_RE, "A number, up to 4 decimals").or(z.literal("")),
});
type Values = z.infer<typeof schema>;

export function PaymentFormPage() {
  const navigate = useNavigate();
  const bankAccounts = useBankAccountOptions();
  const [formError, setFormError] = useState<string | null>(null);
  const approved = useQuery({
    queryKey: ["payment-requests", "approved-for-execution"],
    queryFn: () =>
      api.get<Page<PaymentRequestListItem>>("/finance/payment-requests", {
        query: { status: "APPROVED", limit: 100 },
      }),
  });

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      payment_request_id: "",
      payment_date: new Date().toISOString().slice(0, 10),
      method: "BANK_TRANSFER",
      bank_account_id: "",
      instrument_no: "",
      withholding_amount: "",
    },
  });
  const errors = form.formState.errors;
  const requestId = form.watch("payment_request_id");
  const request = approved.data?.items.find((r) => r.id === requestId);

  const save = useMutation({
    mutationFn: (values: Values) => {
      const body: PaymentCreate = emptyToNull({
        payment_request_id: values.payment_request_id,
        payment_date: values.payment_date,
        method: values.method,
        bank_account_id: values.bank_account_id,
        instrument_no: values.instrument_no || null,
        withholding_amount: values.withholding_amount || "0",
      });
      return api.post<PaymentRead>("/finance/payments", body);
    },
    onSuccess: (saved) => {
      toast.success(`${saved.payment_number} issued.`);
      navigate(`/finance/payments/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["payment_request_id", "bank_account_id"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title="New payment"
        crumbs={[{ label: "Payments", to: "/finance/payments" }, { label: "New" }]}
      />
      <PageBody className="max-w-2xl">
        <form
          onSubmit={(e) => {
            setFormError(null);
            void form.handleSubmit((v) => save.mutate(v))(e);
          }}
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}
          <FormSection title="What">
            <FormGrid>
              <FormField
                label="Approved payment request"
                required
                error={errors.payment_request_id?.message}
                className="sm:col-span-2"
                hint={
                  approved.data?.items.length === 0
                    ? "No approved requests are waiting to be paid."
                    : undefined
                }
              >
                <Select {...form.register("payment_request_id")}>
                  <option value="">Choose…</option>
                  {approved.data?.items.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.request_number} — {r.vendor_name} — {formatMoney(r.amount)}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Payment date" required error={errors.payment_date?.message}>
                <Input type="date" {...form.register("payment_date")} />
              </FormField>
              <FormField label="Method" required>
                <Select {...form.register("method")}>
                  {METHODS.map((m) => (
                    <option key={m} value={m}>
                      {humanize(m)}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField
                label="From account"
                required
                error={errors.bank_account_id?.message}
                hint="A cash till is kept as its own account too."
              >
                <Select {...form.register("bank_account_id")}>
                  <option value="">Choose…</option>
                  {bankAccounts.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Instrument no." hint="Cheque number or transfer reference">
                <Input {...form.register("instrument_no")} />
              </FormField>
              <FormField
                label="Withholding"
                error={errors.withholding_amount?.message}
                hint="Retained from the gross amount, posted to Withholding Tax Payable."
              >
                <Input
                  inputMode="decimal"
                  className="tabular"
                  {...form.register("withholding_amount")}
                />
              </FormField>
            </FormGrid>
          </FormSection>

          {request && (
            <Section title="Summary">
              <FieldGrid
                items={[
                  { label: "Gross amount", value: formatMoney(request.amount) },
                  {
                    label: "Net (after withholding)",
                    value: formatMoney(
                      (
                        Number(request.amount) - Number(form.watch("withholding_amount") || 0)
                      ).toString(),
                    ),
                  },
                ]}
              />
            </Section>
          )}

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              Issue payment
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

type Dialogs = null | "allocate" | "cancel";

export function PaymentDetailPage() {
  const { paymentId = "" } = useParams();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const query = useQuery({
    queryKey: ["payments", "detail", paymentId],
    queryFn: () => api.get<PaymentRead>(`/finance/payments/${paymentId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  const markCleared = useMutation({
    mutationFn: () => api.post(`/finance/payments/${paymentId}/mark-cleared`),
    onSuccess: async () => {
      await refresh();
      toast.success("Marked cleared.");
    },
    onError: (err) => toast.error(describeError(err)),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const p = query.data;

  return (
    <>
      <PageHeader
        title={p.payment_number}
        crumbs={[{ label: "Payments", to: "/finance/payments" }, { label: p.payment_number }]}
        meta={
          <>
            <StatusBadge status={p.status} />
            <span className="text-sm text-fg-muted">{p.vendor_name}</span>
            <span className="text-sm text-fg-muted">{humanize(p.method)}</span>
            <span className="text-sm tabular text-fg-muted">{formatMoney(p.net_amount)}</span>
          </>
        }
        actions={
          <>
            {p.can_allocate && (
              <Button variant="primary" onClick={() => setDialog("allocate")}>
                Allocate
              </Button>
            )}
            {p.can_mark_cleared && (
              <Button loading={markCleared.isPending} onClick={() => markCleared.mutate()}>
                Mark cleared
              </Button>
            )}
            {p.can_cancel && (
              <Button variant="danger" onClick={() => setDialog("cancel")}>
                Cancel
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        <Section title="Allocations">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">What this payment settled</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Invoice
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Allocated
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Date
                  </th>
                </tr>
              </thead>
              <tbody>
                {p.allocations.map((a) => (
                  <tr key={a.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link
                        to={`/finance/vendor-invoices/${a.invoice_id}`}
                        className="font-mono text-primary hover:underline"
                      >
                        {a.invoice_number ?? a.invoice_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td data-numeric className="px-4 py-2 tabular">
                      {formatMoney(a.allocated_amount)}
                    </td>
                    <td className="px-4 py-2 text-fg-muted">{formatDate(a.created_at)}</td>
                  </tr>
                ))}
                {p.allocations.length === 0 && (
                  <tr>
                    <td colSpan={3} className="px-4 py-6 text-center text-fg-muted">
                      Nothing allocated yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Section>

        <Section title="Details">
          <FieldGrid
            items={[
              { label: "Payment request", value: p.payment_request_number },
              { label: "Instrument no.", value: p.instrument_no },
              { label: "Gross amount", value: formatMoney(p.gross_amount) },
              { label: "Withholding", value: formatMoney(p.withholding_amount) },
              { label: "Net amount", value: formatMoney(p.net_amount) },
              { label: "Allocated", value: formatMoney(p.allocated_amount) },
              { label: "Cleared", value: p.cleared_at ? formatDate(p.cleared_at) : null },
              { label: "Cancelled", value: p.cancelled_at ? formatDate(p.cancelled_at) : null },
            ]}
          />
        </Section>
      </PageBody>

      <ConfirmDialog
        open={dialog === "cancel"}
        onOpenChange={(o) => setDialog(o ? "cancel" : null)}
        title={`Cancel ${p.payment_number}?`}
        description="Reverses its GL entry, gives back anything it allocated, and reopens its payment
          request. Only possible before it clears."
        confirmLabel="Cancel payment"
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/finance/payments/${p.id}/cancel`, { reason });
          await refresh();
          toast.success(`${p.payment_number} cancelled.`);
        }}
      />

      {dialog === "allocate" && (
        <AllocateDialog payment={p} onClose={() => setDialog(null)} onDone={() => void refresh()} />
      )}
    </>
  );
}

function AllocateDialog({
  payment,
  onClose,
  onDone,
}: {
  payment: PaymentRead;
  onClose: () => void;
  onDone: () => void;
}) {
  const [rows, setRows] = useState<{ invoice_id: string; allocated_amount: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const invoices = useQuery({
    queryKey: ["vendor-invoices", "payable", payment.vendor_id],
    queryFn: () =>
      api.get<Page<VendorInvoiceListItem>>("/finance/vendor-invoices", {
        query: {
          vendor_id: payment.vendor_id ?? undefined,
          status: ["APPROVED", "PARTIALLY_PAID"],
          limit: 100,
        },
      }),
    enabled: Boolean(payment.vendor_id),
  });
  const already = new Set(payment.allocations.map((a) => a.invoice_id));
  const candidates = (invoices.data?.items ?? []).filter((i) => !already.has(i.id));
  const remaining =
    Number(payment.net_amount) -
    Number(payment.allocated_amount) -
    rows.reduce((sum, r) => sum + Number(r.allocated_amount || 0), 0);

  const save = useMutation({
    mutationFn: () =>
      api.post<PaymentRead>(`/finance/payments/${payment.id}/allocate`, {
        items: rows
          .filter((r) => r.invoice_id && Number(r.allocated_amount) > 0)
          .map((r) => ({ invoice_id: r.invoice_id, allocated_amount: r.allocated_amount })),
      }),
    onSuccess: () => {
      onDone();
      toast.success("Allocated.");
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={`Allocate ${payment.payment_number}`}
      description={`Up to ${formatMoney(payment.net_amount)} net, ${formatMoney(remaining.toString())} left to allocate.`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={rows.length === 0}
            onClick={() => {
              setError(null);
              save.mutate();
            }}
          >
            Save allocation
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {rows.map((row, i) => (
          <div key={i} className="grid grid-cols-[2fr_1fr_auto] gap-2">
            <Select
              value={row.invoice_id}
              onChange={(e) => {
                const next = [...rows];
                next[i] = { ...row, invoice_id: e.target.value };
                setRows(next);
              }}
            >
              <option value="">Choose an invoice…</option>
              {candidates.map((inv) => (
                <option key={inv.id} value={inv.id}>
                  {inv.invoice_number} — {formatMoney(inv.outstanding_amount)} outstanding
                </option>
              ))}
            </Select>
            <Input
              inputMode="decimal"
              className="tabular"
              placeholder="Amount"
              value={row.allocated_amount}
              onChange={(e) => {
                const next = [...rows];
                next[i] = { ...row, allocated_amount: e.target.value };
                setRows(next);
              }}
            />
            <Button
              size="icon"
              variant="ghost"
              aria-label="Remove line"
              onClick={() => setRows(rows.filter((_, j) => j !== i))}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
        <div>
          <Button
            size="sm"
            onClick={() => setRows([...rows, { invoice_id: "", allocated_amount: "" }])}
            disabled={candidates.length === 0}
          >
            <Plus /> Add invoice
          </Button>
        </div>
        {candidates.length === 0 && (
          <p className="text-sm text-fg-muted">No outstanding invoices for this vendor.</p>
        )}
        {error && (
          <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
