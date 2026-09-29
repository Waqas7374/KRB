import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Ban, GitCompareArrows, Mail, Pencil, Plus, Send, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Checkbox, Input, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import { emptyToNull, DECIMAL_RE } from "@/lib/forms";
import { usePagedList, useProjectOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney, formatQuantity } from "@/lib/utils";
import type {
  PurchaseRequestListItem,
  PurchaseRequestRead,
  RfqListItem,
  RfqRead,
  VendorListItem,
} from "@/types/models";

import { plain, RFQ_STATUS_OPTIONS } from "./sourcing";

// --- List -----------------------------------------------------------------------

const col = createColumnHelper<RfqListItem>();

const columns = [
  col.accessor("rfq_number", {
    header: "Number",
    meta: { sortKey: "rfq_number", alwaysVisible: true },
    cell: (c) => (
      <Link to={`/rfqs/${c.row.original.id}`} className="font-mono text-primary hover:underline">
        {c.getValue()}
      </Link>
    ),
  }),
  col.accessor("title", {
    header: "Title",
    cell: (c) => <span className="line-clamp-1">{c.getValue()}</span>,
  }),
  col.accessor("status", {
    header: "Status",
    meta: { sortKey: "status" },
    cell: (c) => <StatusBadge status={c.getValue()} />,
  }),
  col.accessor("project_code", { header: "Project", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("pr_number", {
    header: "Request",
    cell: (c) =>
      c.getValue() ? (
        <Link
          to={`/purchase-requests/${c.row.original.purchase_request_id}`}
          className="font-mono text-primary hover:underline"
        >
          {c.getValue()}
        </Link>
      ) : (
        "—"
      ),
  }),
  col.accessor("item_count", { header: "Lines", meta: { numeric: true } }),
  col.accessor("vendor_count", { header: "Invited", meta: { numeric: true } }),
  col.accessor("quotation_count", { header: "Quotes", meta: { numeric: true } }),
  col.accessor("due_date", {
    header: "Due",
    meta: { sortKey: "due_date" },
    cell: (c) => formatDate(c.getValue()),
  }),
];

export function RfqsListPage() {
  const list = useListParams({ sort: "-created_at" });
  const query = usePagedList<RfqListItem>("rfqs", "/rfqs", list.query);
  const projects = useProjectOptions();

  return (
    <>
      <PageHeader
        title="RFQs"
        subtitle="Requests for quotation. Ask several vendors, compare the answers, then choose — with a reason."
        actions={
          <PermissionGate permission="procurement.rfq.create">
            <Button variant="primary" asChild>
              <Link to="/rfqs/new">
                <Plus /> New RFQ
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="rfqs"
          caption="Requests for quotation"
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
          getRowHref={(r) => `/rfqs/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Number or title…  ( / )"
              savedViewsId="rfqs"
              filters={[
                { key: "status", label: "Status", options: RFQ_STATUS_OPTIONS },
                { key: "project_id", label: "Project", options: projects.options },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Form -----------------------------------------------------------------------

const lineSchema = z.object({
  include: z.boolean(),
  pr_item_id: z.string(),
  material_id: z.string(),
  unit_id: z.string(),
  description: z.string(),
  label: z.string(),
  unit_code: z.string(),
  remaining: z.string(),
  quantity: z.string().trim(),
});

const schema = z
  .object({
    title: z.string().trim().min(3, "At least 3 characters").max(200),
    due_date: z.string(),
    terms: z.string().max(4000),
    vendor_ids: z.array(z.string()),
    items: z.array(lineSchema),
  })
  .superRefine((v, ctx) => {
    const chosen = v.items.filter((i) => i.include);
    if (chosen.length === 0) {
      ctx.addIssue({ code: "custom", path: ["items"], message: "Choose at least one line" });
    }
    v.items.forEach((item, index) => {
      if (!item.include) return;
      if (!DECIMAL_RE.test(item.quantity) || Number(item.quantity) <= 0) {
        ctx.addIssue({
          code: "custom",
          path: ["items", index, "quantity"],
          message: "A positive number",
        });
      } else if (item.remaining && Number(item.quantity) > Number(item.remaining)) {
        ctx.addIssue({
          code: "custom",
          path: ["items", index, "quantity"],
          message: `At most ${plain(item.remaining)} remain`,
        });
      }
    });
  });
type Values = z.infer<typeof schema>;

/** New RFQs start from an approved purchase request; drafts are edited in place. */
export function RfqFormPage() {
  const { rfqId } = useParams();
  const [search] = useSearchParams();
  const prId = search.get("pr");

  const rfq = useQuery({
    queryKey: ["rfqs", "detail", rfqId],
    queryFn: () => api.get<RfqRead>(`/rfqs/${rfqId}`),
    enabled: Boolean(rfqId),
    staleTime: 0,
  });
  const sourceId = rfqId ? rfq.data?.purchase_request_id : prId;
  const pr = useQuery({
    queryKey: ["purchase-requests", "detail", sourceId],
    queryFn: () => api.get<PurchaseRequestRead>(`/purchase-requests/${sourceId}`),
    enabled: Boolean(sourceId),
    staleTime: 0,
  });

  if (rfqId && rfq.isLoading) return <PageSkeleton />;
  if (rfqId && rfq.error)
    return <ErrorState error={rfq.error} onRetry={() => void rfq.refetch()} />;
  if (!rfqId && !prId) return <ChooseRequest />;
  if (sourceId && pr.isLoading) return <PageSkeleton />;
  if (sourceId && pr.error)
    return <ErrorState error={pr.error} onRetry={() => void pr.refetch()} />;
  return <RfqForm rfq={rfq.data} request={pr.data} />;
}

function ChooseRequest() {
  const navigate = useNavigate();
  const requests = usePagedList<PurchaseRequestListItem>(
    "purchase-requests",
    "/purchase-requests",
    { limit: 100, sort: "-approved_at", status: "APPROVED" },
  );
  const partly = usePagedList<PurchaseRequestListItem>("purchase-requests", "/purchase-requests", {
    limit: 100,
    status: "PARTIALLY_SOURCED",
  });
  const rows = [...(requests.data?.items ?? []), ...(partly.data?.items ?? [])];
  return (
    <>
      <PageHeader
        title="New RFQ"
        crumbs={[{ label: "RFQs", to: "/rfqs" }, { label: "New" }]}
        subtitle="An RFQ asks vendors to quote the lines of an approved purchase request."
      />
      <PageBody className="max-w-3xl">
        {requests.isLoading ? (
          <PageSkeleton />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No approved purchase requests to source"
            description="Once a purchase request is approved, it appears here. Fully sourced requests do not."
          />
        ) : (
          <Section title="Approved purchase requests">
            <ul className="divide-y divide-border">
              {rows.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-surface"
                    onClick={() => navigate(`/rfqs/new?pr=${r.id}`)}
                  >
                    <span>
                      <span className="font-mono text-primary">{r.pr_number}</span>{" "}
                      <span className="text-sm text-fg-muted">
                        {r.project_code}
                        {r.site_code && ` / ${r.site_code}`} · {r.item_count} line
                        {r.item_count === 1 ? "" : "s"}
                      </span>
                    </span>
                    <span className="flex items-center gap-3 text-sm">
                      <StatusBadge status={r.status} />
                      <span className="tabular">
                        {formatMoney(r.estimated_amount, {
                          currency: r.currency_code,
                          decimals: 0,
                        })}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Section>
        )}
      </PageBody>
    </>
  );
}

function RfqForm({ rfq, request }: { rfq?: RfqRead; request?: PurchaseRequestRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const vendors = usePagedList<VendorListItem>("vendors", "/vendors", {
    limit: 200,
    status: "ACTIVE",
    sort: "code",
  });
  const [formError, setFormError] = useState<string | null>(null);

  const requestLines = request?.items ?? [];
  const items: Values["items"] = rfq
    ? rfq.items.map((i) => {
        const source = requestLines.find((l) => l.id === i.pr_item_id);
        const remaining = source
          ? Number(source.quantity) - Number(source.sourced_quantity)
          : undefined;
        return {
          include: true,
          pr_item_id: i.pr_item_id ?? "",
          material_id: i.material_id,
          unit_id: i.unit_id,
          description: i.description ?? "",
          label: `${i.material_sku ?? ""} — ${i.material_name ?? ""}`,
          unit_code: i.unit_code ?? "",
          remaining: remaining === undefined ? "" : String(remaining),
          quantity: plain(i.quantity),
        };
      })
    : requestLines
        .filter((l) => Number(l.quantity) - Number(l.sourced_quantity) > 0)
        .map((l) => {
          const remaining = Number(l.quantity) - Number(l.sourced_quantity);
          return {
            include: true,
            pr_item_id: l.id,
            material_id: l.material_id,
            unit_id: l.unit_id,
            description: l.description ?? "",
            label: `${l.material_sku ?? ""} — ${l.material_name ?? ""}`,
            unit_code: l.unit_code ?? "",
            remaining: String(remaining),
            quantity: String(remaining),
          };
        });

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      title: rfq?.title ?? (request ? `Quotations for ${request.pr_number}` : ""),
      due_date: rfq?.due_date ?? "",
      terms: rfq?.terms ?? "",
      vendor_ids: [],
      items,
    },
  });
  const lines = useFieldArray({ control: form.control, name: "items" });
  const errors = form.formState.errors;
  const chosen = form.watch("vendor_ids");

  const save = useMutation({
    mutationFn: async ({ values, issue }: { values: Values; issue: boolean }) => {
      const included = values.items.filter((i) => i.include);
      const itemsBody = included.map((i) =>
        emptyToNull({
          material_id: i.material_id,
          unit_id: i.unit_id,
          quantity: i.quantity,
          description: i.description,
          pr_item_id: i.pr_item_id,
        }),
      );
      const head = emptyToNull({
        title: values.title,
        due_date: values.due_date,
        terms: values.terms,
      });
      const saved = rfq
        ? await api.put<RfqRead>(
            `/rfqs/${rfq.id}`,
            { ...head, items: itemsBody },
            { ifMatch: String(rfq.version) },
          )
        : await api.post<RfqRead>("/rfqs", {
            ...head,
            purchase_request_id: request?.id,
            items: itemsBody,
            vendor_ids: values.vendor_ids,
          });
      if (!issue) return { saved, issueError: null as string | null };
      try {
        const issued = await api.post<RfqRead>(`/rfqs/${saved.id}/issue`, undefined, {
          ifMatch: String(saved.version),
        });
        return { saved: issued, issueError: null };
      } catch (err) {
        return { saved, issueError: describeError(err) };
      }
    },
    onSuccess: async ({ saved, issueError }) => {
      await queryClient.invalidateQueries({ queryKey: ["rfqs"] });
      if (issueError) toast.error(`Saved as a draft, but not issued: ${issueError}`);
      else
        toast.success(
          saved.status === "ISSUED" ? `${saved.rfq_number} issued.` : `Saved ${saved.rfq_number}.`,
        );
      navigate(`/rfqs/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["title", "due_date", "terms"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  const run = (issue: boolean) =>
    form.handleSubmit((values) => {
      setFormError(null);
      save.mutate({ values, issue });
    });

  const toggleVendor = (id: string) =>
    form.setValue(
      "vendor_ids",
      chosen.includes(id) ? chosen.filter((v) => v !== id) : [...chosen, id],
    );

  return (
    <>
      <PageHeader
        title={rfq ? `Edit ${rfq.rfq_number}` : "New RFQ"}
        crumbs={[
          { label: "RFQs", to: "/rfqs" },
          ...(rfq ? [{ label: rfq.rfq_number, to: `/rfqs/${rfq.id}` }] : []),
          { label: rfq ? "Edit" : "New" },
        ]}
        subtitle={
          request
            ? `From ${request.pr_number} — ${request.project_code}${request.site_code ? ` / ${request.site_code}` : ""}`
            : undefined
        }
      />
      <PageBody className="max-w-5xl">
        <form onSubmit={(e) => void run(false)(e)} className="flex flex-col gap-5" noValidate>
          {formError && <FormAlert>{formError}</FormAlert>}

          <FormSection title="The request to vendors">
            <FormGrid>
              <FormField label="Title" required error={errors.title?.message}>
                <Input {...form.register("title")} />
              </FormField>
              <FormField
                label="Responses due by"
                hint="Needed before the RFQ can be issued."
                error={errors.due_date?.message}
              >
                <Input type="date" {...form.register("due_date")} />
              </FormField>
              <FormField label="Terms" className="sm:col-span-2" error={errors.terms?.message}>
                <Textarea
                  {...form.register("terms")}
                  placeholder="Delivery, payment and quality terms vendors must quote against"
                />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Lines to quote">
            {errors.items?.message && (
              <p className="mb-2 text-xs text-danger">{errors.items.message}</p>
            )}
            {errors.items?.root?.message && (
              <p className="mb-2 text-xs text-danger">{errors.items.root.message}</p>
            )}
            <div className="flex flex-col gap-2">
              {lines.fields.map((field, i) => {
                const rowError = errors.items?.[i]?.quantity?.message;
                return (
                  <div
                    key={field.id}
                    className="grid grid-cols-1 items-start gap-3 rounded-md border border-border p-3 md:grid-cols-[auto_2fr_1fr]"
                  >
                    <label className="flex items-center gap-2 pt-1.5">
                      <Checkbox
                        {...form.register(`items.${i}.include`)}
                        aria-label={`Include line ${i + 1}`}
                      />
                    </label>
                    <div className="pt-1.5 text-sm">
                      <span className="font-medium">{field.label}</span>
                      {field.remaining && (
                        <p className="text-xs text-fg-muted">
                          {plain(field.remaining)} {field.unit_code} still to source
                        </p>
                      )}
                    </div>
                    <FormField label={`Quantity (${field.unit_code})`} error={rowError}>
                      <Input
                        inputMode="decimal"
                        className="tabular"
                        aria-label={`Line ${i + 1} quantity`}
                        {...form.register(`items.${i}.quantity`)}
                      />
                    </FormField>
                  </div>
                );
              })}
            </div>
          </FormSection>

          {!rfq && (
            <FormSection title="Vendors to invite">
              {vendors.isLoading ? (
                <p className="text-sm text-fg-muted">Loading vendors…</p>
              ) : (vendors.data?.items.length ?? 0) === 0 ? (
                <p className="text-sm text-fg-muted">No active vendors. Approve a vendor first.</p>
              ) : (
                <ul className="grid gap-1.5 sm:grid-cols-2">
                  {vendors.data?.items.map((v) => (
                    <li key={v.id}>
                      <label className="flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-surface">
                        <Checkbox
                          checked={chosen.includes(v.id)}
                          onChange={() => toggleVendor(v.id)}
                        />
                        <span className="font-mono text-xs text-fg-muted">{v.code}</span>
                        <span className="truncate">{v.display_name}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-xs text-fg-subtle">
                Only active vendors can be invited. More can be added after the RFQ is drafted.
              </p>
            </FormSection>
          )}

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" loading={save.isPending && !save.variables?.issue}>
              Save draft
            </Button>
            {!rfq && (
              <Button
                variant="primary"
                loading={save.isPending && save.variables?.issue}
                onClick={() => void run(true)()}
              >
                <Send /> Save and issue
              </Button>
            )}
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

type Dialogs = null | "issue" | "close" | "cancel" | "invite";

export function RfqDetailPage() {
  const { rfqId = "" } = useParams();
  const queryClient = useQueryClient();
  const canCompare = useCan("procurement.quotation.view");
  const [dialog, setDialog] = useState<Dialogs>(null);

  const query = useQuery({
    queryKey: ["rfqs", "detail", rfqId],
    queryFn: () => api.get<RfqRead>(`/rfqs/${rfqId}`),
  });

  const refresh = () => queryClient.invalidateQueries();

  const issue = useMutation({
    mutationFn: () =>
      api.post<RfqRead>(`/rfqs/${rfqId}/issue`, undefined, {
        ifMatch: String(query.data?.version),
      }),
    onSuccess: async (saved) => {
      await refresh();
      toast.success(`${saved.rfq_number} issued to ${saved.vendors.length} vendor(s).`);
    },
    onError: (err) => toast.error(describeError(err)),
  });
  const withdraw = useMutation({
    mutationFn: (rfqVendorId: string) =>
      api.delete<RfqRead>(`/rfqs/${rfqId}/vendors/${rfqVendorId}`),
    onSuccess: refresh,
    onError: (err) => toast.error(describeError(err)),
  });
  const decline = useMutation({
    mutationFn: (rfqVendorId: string) =>
      api.post<RfqRead>(`/rfqs/${rfqId}/vendors/${rfqVendorId}/decline`),
    onSuccess: refresh,
    onError: (err) => toast.error(describeError(err)),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const r = query.data;

  return (
    <>
      <PageHeader
        title={r.rfq_number}
        subtitle={r.title}
        crumbs={[{ label: "RFQs", to: "/rfqs" }, { label: r.rfq_number }]}
        meta={
          <>
            <StatusBadge status={r.status} />
            <span className="text-sm text-fg-muted">
              {r.project_code}
              {r.site_code && ` / ${r.site_code}`}
            </span>
            {r.pr_number && (
              <span className="text-sm text-fg-muted">
                From{" "}
                <Link
                  to={`/purchase-requests/${r.purchase_request_id}`}
                  className="font-mono text-primary hover:underline"
                >
                  {r.pr_number}
                </Link>
              </span>
            )}
          </>
        }
        actions={
          <>
            {r.can_edit && (
              <Button asChild>
                <Link to={`/rfqs/${r.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {r.can_edit && (
              <Button onClick={() => setDialog("invite")}>
                <Mail /> Invite vendors
              </Button>
            )}
            {r.can_issue && (
              <Button variant="primary" onClick={() => issue.mutate()} loading={issue.isPending}>
                <Send /> Issue to vendors
              </Button>
            )}
            {canCompare && r.status !== "DRAFT" && (
              <Button variant={r.status === "ISSUED" ? "primary" : undefined} asChild>
                <Link to={`/rfqs/${r.id}/comparison`}>
                  <GitCompareArrows /> Compare quotations
                </Link>
              </Button>
            )}
            {r.can_close && (
              <Button onClick={() => setDialog("close")}>
                <XCircle /> Close RFQ
              </Button>
            )}
            {r.can_cancel && (
              <Button variant="danger" onClick={() => setDialog("cancel")}>
                <Ban /> Cancel
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {r.close_reason && (
          <FormAlert>
            {r.status === "CANCELLED" ? "Cancelled" : "Closed"}: {r.close_reason}
          </FormAlert>
        )}

        <Section title="Details">
          <FieldGrid
            items={[
              { label: "Issued", value: r.issue_date ? formatDate(r.issue_date) : null },
              { label: "Responses due", value: r.due_date ? formatDate(r.due_date) : null },
              { label: "Created", value: formatDateTime(r.created_at) },
              { label: "Terms", value: r.terms, wide: true },
            ]}
          />
        </Section>

        <Section title="Lines to quote">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Lines vendors are asked to quote</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    #
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Material
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Quantity
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Budgeted rate
                  </th>
                </tr>
              </thead>
              <tbody>
                {r.items.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="px-4 py-2 text-fg-muted">{i.line_no}</td>
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{i.material_sku}</span>{" "}
                      <span className="font-medium">{i.material_name}</span>
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.quantity, i.unit_code ?? undefined)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {i.estimated_rate ? formatMoney(i.estimated_rate) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section title={`Vendors (${r.vendors.length})`}>
          {r.vendors.length === 0 ? (
            <p className="px-4 py-4 text-sm text-fg-muted">
              No vendors invited yet. Invite at least one before issuing.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Invited vendors and their responses</caption>
                <thead className="bg-surface text-left text-xs text-fg-muted">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Vendor
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Response
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Quotation
                    </th>
                    <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                      Total
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {r.vendors.map((v) => (
                    <tr key={v.id} className="border-t border-border">
                      <td className="px-4 py-2">
                        <span className="font-mono text-xs text-fg-muted">{v.vendor_code}</span>{" "}
                        <span className="font-medium">{v.vendor_name}</span>
                      </td>
                      <td className="px-4 py-2">
                        <StatusBadge status={v.status} />
                        {v.responded_at && (
                          <span className="ml-2 text-xs text-fg-subtle">
                            {formatDate(v.responded_at)}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        {v.quotation_id ? (
                          <Link
                            to={`/quotations/${v.quotation_id}`}
                            className="font-mono text-primary hover:underline"
                          >
                            {v.quotation_number}
                          </Link>
                        ) : (
                          "—"
                        )}{" "}
                        {v.quotation_status && v.quotation_status !== "RECEIVED" && (
                          <StatusBadge status={v.quotation_status} />
                        )}
                      </td>
                      <td data-numeric className="px-4 py-2">
                        {v.quotation_total
                          ? formatMoney(v.quotation_total, { currency: r.currency_code })
                          : "—"}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <span className="inline-flex flex-wrap justify-end gap-1.5">
                          {r.can_record_quotation && v.status === "INVITED" && (
                            <>
                              <Button size="sm" asChild>
                                <Link to={`/rfqs/${r.id}/quotations/new?vendor=${v.vendor_id}`}>
                                  Record quotation
                                </Link>
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => decline.mutate(v.id)}
                                disabled={decline.isPending}
                              >
                                Declined
                              </Button>
                            </>
                          )}
                          {r.can_edit && (
                            <Button
                              size="icon"
                              variant="ghost"
                              aria-label={`Withdraw invitation to ${v.vendor_name}`}
                              onClick={() => withdraw.mutate(v.id)}
                              disabled={withdraw.isPending}
                            >
                              <Trash2 />
                            </Button>
                          )}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </PageBody>

      <InviteDialog
        open={dialog === "invite"}
        onOpenChange={(o) => setDialog(o ? "invite" : null)}
        rfq={r}
        onDone={refresh}
      />
      <ConfirmDialog
        open={dialog === "close"}
        onOpenChange={(o) => setDialog(o ? "close" : null)}
        title={`Close ${r.rfq_number}?`}
        description="Closing ends the RFQ without ordering from it. No further quotations can be recorded."
        confirmLabel="Close RFQ"
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/rfqs/${r.id}/close`, { reason });
          await refresh();
          toast.success(`${r.rfq_number} closed.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "cancel"}
        onOpenChange={(o) => setDialog(o ? "cancel" : null)}
        title={`Cancel ${r.rfq_number}?`}
        description="A cancelled RFQ cannot be reopened."
        confirmLabel={`Cancel ${r.rfq_number}`}
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/rfqs/${r.id}/cancel`, { reason });
          await refresh();
          toast.success(`${r.rfq_number} cancelled.`);
        }}
      />
    </>
  );
}

function InviteDialog({
  open,
  onOpenChange,
  rfq,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rfq: RfqRead;
  onDone: () => unknown;
}) {
  const vendors = usePagedList<VendorListItem>(
    "vendors",
    "/vendors",
    { limit: 200, status: "ACTIVE", sort: "code" },
    open,
  );
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const invited = new Set(rfq.vendors.map((v) => v.vendor_id));
  const available = vendors.data?.items.filter((v) => !invited.has(v.id)) ?? [];

  const invite = useMutation({
    mutationFn: () => api.post<RfqRead>(`/rfqs/${rfq.id}/vendors`, { vendor_ids: picked }),
    onSuccess: async () => {
      await onDone();
      setPicked([]);
      onOpenChange(false);
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Invite vendors"
      description="Only active vendors can be asked to quote."
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="primary"
            disabled={picked.length === 0}
            loading={invite.isPending}
            onClick={() => {
              setError(null);
              invite.mutate();
            }}
          >
            Invite {picked.length || ""} vendor{picked.length === 1 ? "" : "s"}
          </Button>
        </>
      }
    >
      {vendors.isLoading ? (
        <p className="text-sm text-fg-muted">Loading…</p>
      ) : available.length === 0 ? (
        <p className="text-sm text-fg-muted">Every active vendor has already been invited.</p>
      ) : (
        <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          {available.map((v) => (
            <li key={v.id}>
              <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface">
                <Checkbox
                  checked={picked.includes(v.id)}
                  onChange={() =>
                    setPicked((p) =>
                      p.includes(v.id) ? p.filter((x) => x !== v.id) : [...p, v.id],
                    )
                  }
                />
                <span className="font-mono text-xs text-fg-muted">{v.code}</span>
                <span>{v.display_name}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="mt-3 rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
    </Dialog>
  );
}
