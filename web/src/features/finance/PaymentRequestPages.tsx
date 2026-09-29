import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Plus, Send } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router-dom";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { ApprovalSection } from "@/features/approvals/ApprovalSection";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { DECIMAL_RE } from "@/lib/forms";
import { usePagedList, useVendorOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatMoney, humanize } from "@/lib/utils";
import type {
  PaymentRequestCreate,
  PaymentRequestListItem,
  PaymentRequestRead,
} from "@/types/models";

const STATUS_OPTIONS = [
  "DRAFT",
  "PENDING_APPROVAL",
  "APPROVED",
  "REJECTED",
  "CHANGES_REQUESTED",
  "PAID",
  "CANCELLED",
].map((value) => ({ value, label: humanize(value) }));

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<PaymentRequestListItem>();

export function PaymentRequestListPage() {
  const list = useListParams({ sort: "-created_at" });
  const query = usePagedList<PaymentRequestListItem>(
    "payment-requests",
    "/finance/payment-requests",
    list.query,
  );
  const vendors = useVendorOptions();

  const columns = useMemo(
    () => [
      col.accessor("request_number", {
        header: "Request",
        meta: { alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/finance/payment-requests/${c.row.original.id}`}
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
      col.accessor("status", {
        header: "Status",
        cell: (c) => <StatusBadge status={c.getValue()} />,
      }),
      col.accessor("priority", { header: "Priority", cell: (c) => humanize(c.getValue()) }),
      col.accessor("is_advance", {
        header: "Advance",
        cell: (c) => (c.getValue() ? "Yes" : "—"),
      }),
      col.accessor("amount", {
        header: "Amount",
        meta: { numeric: true },
        cell: (c) => formatMoney(c.getValue()),
      }),
      col.accessor("submitted_at", {
        header: "Submitted",
        cell: (c) => (c.getValue() ? formatDate(c.getValue()) : "—"),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Payment requests"
        subtitle="A request to pay a vendor, signed by finance (and the executive for larger amounts)
          before any money moves."
        actions={
          <PermissionGate permission="finance.payment.request">
            <Button variant="primary" asChild>
              <Link to="/finance/payment-requests/new">
                <Plus /> New request
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="payment-requests"
          caption="Payment requests"
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
          getRowHref={(r) => `/finance/payment-requests/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Request number or reason…"
              savedViewsId="payment-requests"
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

const schema = z.object({
  vendor_id: z.string().min(1, "Choose a vendor"),
  amount: z
    .string()
    .regex(DECIMAL_RE, "A number, up to 4 decimals")
    .refine((v) => Number(v) > 0, "More than zero"),
  reason: z.string().trim().min(2, "Say what this is for").max(2000),
  priority: z.enum(["NORMAL", "URGENT"]),
  is_advance: z.boolean(),
});
type Values = z.infer<typeof schema>;

export function PaymentRequestFormPage() {
  const { requestId } = useParams();
  const existing = useQuery({
    queryKey: ["payment-requests", "detail", requestId],
    queryFn: () => api.get<PaymentRequestRead>(`/finance/payment-requests/${requestId}`),
    enabled: Boolean(requestId),
    staleTime: 0,
  });
  if (requestId && existing.isLoading) return <PageSkeleton />;
  if (requestId && existing.error) {
    return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />;
  }
  return <PaymentRequestForm request={existing.data} />;
}

function PaymentRequestForm({ request }: { request?: PaymentRequestRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const vendors = useVendorOptions();
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: request
      ? {
          vendor_id: request.vendor_id,
          amount: request.amount,
          reason: request.reason,
          priority: request.priority as "NORMAL" | "URGENT",
          is_advance: request.is_advance,
        }
      : {
          vendor_id: "",
          amount: "",
          reason: "",
          priority: "NORMAL",
          is_advance: false,
        },
  });
  const errors = form.formState.errors;

  const save = useMutation({
    mutationFn: (values: Values) => {
      const body: PaymentRequestCreate = {
        vendor_id: values.vendor_id,
        amount: values.amount,
        reason: values.reason,
        priority: values.priority,
        is_advance: values.is_advance,
        currency_code: "PKR",
      };
      return request
        ? api.put<PaymentRequestRead>(`/finance/payment-requests/${request.id}`, body, {
            ifMatch: String(request.version),
          })
        : api.post<PaymentRequestRead>("/finance/payment-requests", body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(`${saved.request_number} saved as a draft.`);
      navigate(`/finance/payment-requests/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["vendor_id", "amount", "reason"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title={request ? `Edit ${request.request_number}` : "New payment request"}
        crumbs={[
          { label: "Payment requests", to: "/finance/payment-requests" },
          ...(request
            ? [{ label: request.request_number, to: `/finance/payment-requests/${request.id}` }]
            : []),
          { label: request ? "Edit" : "New" },
        ]}
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
              <FormField label="Vendor" required error={errors.vendor_id?.message}>
                <Select {...form.register("vendor_id")} disabled={Boolean(request)}>
                  <option value="">Choose…</option>
                  {vendors.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Amount" required error={errors.amount?.message}>
                <Input inputMode="decimal" className="tabular" {...form.register("amount")} />
              </FormField>
              <FormField label="Priority">
                <Select {...form.register("priority")}>
                  <option value="NORMAL">Normal</option>
                  <option value="URGENT">Urgent</option>
                </Select>
              </FormField>
              <label className="flex items-center gap-2 pt-6 text-sm">
                <Checkbox {...form.register("is_advance")} />
                This is an advance — no invoice behind it yet
              </label>
              <FormField
                label="Reason"
                required
                error={errors.reason?.message}
                className="sm:col-span-2"
              >
                <Textarea rows={3} {...form.register("reason")} />
              </FormField>
            </FormGrid>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {request ? "Save changes" : "Save draft"}
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

type Dialogs = null | "submit" | "delete" | "cancel";

export function PaymentRequestDetailPage() {
  const { requestId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const query = useQuery({
    queryKey: ["payment-requests", "detail", requestId],
    queryFn: () => api.get<PaymentRequestRead>(`/finance/payment-requests/${requestId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const r = query.data;

  return (
    <>
      <PageHeader
        title={r.request_number}
        crumbs={[
          { label: "Payment requests", to: "/finance/payment-requests" },
          { label: r.request_number },
        ]}
        meta={
          <>
            <StatusBadge status={r.status} />
            <span className="text-sm text-fg-muted">{r.vendor_name}</span>
            <span className="text-sm tabular text-fg-muted">{formatMoney(r.amount)}</span>
            {r.is_advance && <span className="text-sm text-fg-muted">Advance</span>}
          </>
        }
        actions={
          <>
            {r.can_edit && (
              <Button asChild>
                <Link to={`/finance/payment-requests/${r.id}/edit`}>Edit</Link>
              </Button>
            )}
            {r.can_submit && (
              <Button variant="primary" onClick={() => setDialog("submit")}>
                <Send /> Submit for approval
              </Button>
            )}
            {r.can_cancel && (
              <Button variant="danger" onClick={() => setDialog("cancel")}>
                Cancel
              </Button>
            )}
            {r.can_delete && (
              <Button variant="danger" onClick={() => setDialog("delete")}>
                Delete
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {r.decision_reason && (r.status === "DRAFT" || r.status === "CHANGES_REQUESTED") && (
          <FormAlert>{r.decision_reason}</FormAlert>
        )}

        <Section title="Reason">
          <p className="whitespace-pre-wrap text-sm">{r.reason}</p>
        </Section>

        <ApprovalSection
          docType="payment_request"
          docId={r.id}
          label={r.request_number}
          empty="Not submitted yet. Once it is, the people who must sign it and each decision appear here."
        />
      </PageBody>

      <ConfirmDialog
        open={dialog === "submit"}
        onOpenChange={(o) => setDialog(o ? "submit" : null)}
        title={`Submit ${r.request_number}?`}
        description="It goes to the people who must sign it. Nothing is paid until every step is approved."
        confirmLabel="Submit for approval"
        onConfirm={async () => {
          await api.post(`/finance/payment-requests/${r.id}/submit`, undefined, {
            ifMatch: String(r.version),
          });
          await refresh();
          toast.success(`${r.request_number} submitted for approval.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "cancel"}
        onOpenChange={(o) => setDialog(o ? "cancel" : null)}
        title={`Cancel ${r.request_number}?`}
        description="This cannot be undone."
        confirmLabel="Cancel request"
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/finance/payment-requests/${r.id}/cancel`, { reason });
          await refresh();
          toast.success(`${r.request_number} cancelled.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        onOpenChange={(o) => setDialog(o ? "delete" : null)}
        title={`Delete ${r.request_number}?`}
        description="It never happened, so there is nothing to reverse — this removes it entirely."
        confirmLabel="Delete"
        destructive
        onConfirm={async () => {
          await api.delete(`/finance/payment-requests/${r.id}`);
          toast.success(`${r.request_number} deleted.`);
          navigate("/finance/payment-requests");
        }}
      />
    </>
  );
}
