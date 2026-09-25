import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Ban, Pencil, Plus, Send, Trash2 } from "lucide-react";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router-dom";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { fetchAllPages } from "@/components/data-table/export";
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
import { useAuthStore } from "@/features/auth/auth-store";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import {
  usePagedList,
  useMaterialOptions,
  useProjectOptions,
  useSiteOptions,
  useUnitOptions,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney, formatQuantity, humanize } from "@/lib/utils";
import type {
  ApprovalRequestRead,
  PurchaseRequestListItem,
  PurchaseRequestRead,
} from "@/types/models";

const STATUS_OPTIONS = [
  "DRAFT",
  "PENDING_APPROVAL",
  "APPROVED",
  "REJECTED",
  "CHANGES_REQUESTED",
  "CANCELLED",
].map((value) => ({ value, label: humanize(value) }));

// --- List -----------------------------------------------------------------------

const col = createColumnHelper<PurchaseRequestListItem>();

const columns = [
  col.accessor("pr_number", {
    header: "Number",
    meta: { sortKey: "pr_number", alwaysVisible: true },
    cell: (c) => (
      <Link
        to={`/purchase-requests/${c.row.original.id}`}
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
  col.accessor("project_code", { header: "Project", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("site_code", { header: "Site", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("priority", {
    header: "Priority",
    meta: { sortKey: "priority" },
    cell: (c) => (c.getValue() === "URGENT" ? <Tag>Urgent</Tag> : "Normal"),
  }),
  col.accessor("item_count", { header: "Lines", meta: { numeric: true } }),
  col.accessor("estimated_amount", {
    header: "Estimate",
    meta: { numeric: true, sortKey: "estimated_amount" },
    cell: (c) => formatMoney(c.getValue(), { currency: c.row.original.currency_code, decimals: 0 }),
  }),
  col.accessor("requested_by_name", { header: "Raised by", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("required_date", {
    header: "Needed by",
    meta: { sortKey: "required_date" },
    cell: (c) => formatDate(c.getValue()),
  }),
];

export function PurchaseRequestsListPage() {
  const list = useListParams({ sort: "-created_at" });
  const query = usePagedList<PurchaseRequestListItem>(
    "purchase-requests",
    "/purchase-requests",
    list.query,
  );
  const projects = useProjectOptions();

  return (
    <>
      <PageHeader
        title="Purchase requests"
        subtitle="Requests for materials. Each is routed for approval by its estimated total."
        actions={
          <PermissionGate permission="procurement.pr.create">
            <Button variant="primary" asChild>
              <Link to="/purchase-requests/new">
                <Plus /> New request
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="purchase-requests"
          caption="Purchase requests"
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
          getRowHref={(r) => `/purchase-requests/${r.id}`}
          exportRows={() =>
            fetchAllPages<PurchaseRequestListItem>("/purchase-requests", list.query)
          }
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Number or justification…  ( / )"
              savedViewsId="purchase-requests"
              filters={[
                { key: "status", label: "Status", options: STATUS_OPTIONS },
                { key: "project_id", label: "Project", options: projects.options },
                {
                  key: "mine",
                  label: "Raised by",
                  options: [{ value: "true", label: "Me" }],
                },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Form -----------------------------------------------------------------------

const decimal = (message: string) =>
  z
    .string()
    .trim()
    .refine((v) => v === "" || DECIMAL_RE.test(v), message);

const lineSchema = z.object({
  material_id: z.string().min(1, "Choose a material"),
  unit_id: z.string().min(1, "Choose a unit"),
  quantity: z
    .string()
    .trim()
    .refine((v) => DECIMAL_RE.test(v) && Number(v) > 0, "A positive number"),
  estimated_rate: decimal("A plain amount"),
  description: z.string(),
});

const schema = z
  .object({
    project_id: z.string().min(1, "Choose a project"),
    site_id: z.string(),
    required_date: z.string(),
    priority: z.string(),
    justification: z.string().trim().min(5, "At least 5 characters").max(2000),
    items: z.array(lineSchema).min(1, "Add at least one line"),
  })
  .refine(
    (v) => new Set(v.items.map((i) => `${i.material_id}|${i.unit_id}`)).size === v.items.length,
    { path: ["items"], message: "The same material and unit appear on two lines; combine them" },
  );
type Values = z.infer<typeof schema>;

const emptyLine = {
  material_id: "",
  unit_id: "",
  quantity: "",
  estimated_rate: "",
  description: "",
};

/** Display-only estimate for a line; the server recomputes and is the authority. */
function previewAmount(quantity: string, rate: string): string | null {
  if (!DECIMAL_RE.test(quantity) || !DECIMAL_RE.test(rate)) return null;
  return String(Number(quantity) * Number(rate));
}

export function PurchaseRequestFormPage() {
  const { requestId } = useParams();
  const existing = useQuery({
    queryKey: ["purchase-requests", "detail", requestId],
    queryFn: () => api.get<PurchaseRequestRead>(`/purchase-requests/${requestId}`),
    enabled: Boolean(requestId),
    staleTime: 0,
  });
  if (requestId && existing.isLoading) return <PageSkeleton />;
  if (requestId && existing.error) {
    return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />;
  }
  return <PurchaseRequestForm request={existing.data} />;
}

function PurchaseRequestForm({ request }: { request?: PurchaseRequestRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const me = useAuthStore((s) => s.me);
  const projects = useProjectOptions();
  const materials = useMaterialOptions();
  const units = useUnitOptions();
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: request
      ? {
          project_id: request.project_id,
          site_id: request.site_id ?? "",
          required_date: request.required_date ?? "",
          priority: request.priority,
          justification: request.justification,
          items: request.items.map((i) => ({
            material_id: i.material_id,
            unit_id: i.unit_id,
            quantity: i.quantity.replace(/\.?0+$/, "") || "0",
            estimated_rate: i.estimated_rate ? i.estimated_rate.replace(/\.?0+$/, "") : "",
            description: i.description ?? "",
          })),
        }
      : {
          project_id: me?.user.default_project_id ?? "",
          site_id: me?.user.default_site_id ?? "",
          required_date: "",
          priority: "NORMAL",
          justification: "",
          items: [{ ...emptyLine }],
        },
  });
  const lines = useFieldArray({ control: form.control, name: "items" });
  const projectId = form.watch("project_id");
  const sites = useSiteOptions(projectId || undefined);
  const errors = form.formState.errors;
  const watched = form.watch("items");

  const save = useMutation({
    mutationFn: async ({ values, submit }: { values: Values; submit: boolean }) => {
      const body = {
        ...emptyToNull({
          project_id: values.project_id,
          site_id: values.site_id,
          required_date: values.required_date,
          priority: values.priority,
          justification: values.justification,
        }),
        items: values.items.map((i) => emptyToNull(i)),
      };
      const saved = request
        ? await api.put<PurchaseRequestRead>(`/purchase-requests/${request.id}`, body, {
            ifMatch: String(request.version),
          })
        : await api.post<PurchaseRequestRead>("/purchase-requests", body);
      if (!submit) return { saved, submitError: null as string | null };
      try {
        const submitted = await api.post<PurchaseRequestRead>(
          `/purchase-requests/${saved.id}/submit`,
          undefined,
          { ifMatch: String(saved.version) },
        );
        return { saved: submitted, submitError: null };
      } catch (err) {
        // The request is saved; only routing was refused. Say so precisely.
        return { saved, submitError: describeError(err) };
      }
    },
    onSuccess: async ({ saved, submitError }) => {
      await queryClient.invalidateQueries({ queryKey: ["purchase-requests"] });
      await queryClient.invalidateQueries({ queryKey: ["approvals-inbox"] });
      if (submitError) toast.error(`Saved as a draft, but not submitted: ${submitError}`);
      else
        toast.success(
          saved.status === "PENDING_APPROVAL"
            ? `${saved.pr_number} submitted.`
            : `Saved ${saved.pr_number}.`,
        );
      navigate(`/purchase-requests/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "project_id",
        "site_id",
        "required_date",
        "priority",
        "justification",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  const run = (submit: boolean) =>
    form.handleSubmit((values) => {
      setFormError(null);
      save.mutate({ values, submit });
    });

  const total = watched.reduce(
    (sum, i) => sum + Number(previewAmount(i.quantity, i.estimated_rate) ?? 0),
    0,
  );

  return (
    <>
      <PageHeader
        title={request ? `Edit ${request.pr_number}` : "New purchase request"}
        crumbs={[
          { label: "Purchase requests", to: "/purchase-requests" },
          ...(request
            ? [{ label: request.pr_number, to: `/purchase-requests/${request.id}` }]
            : []),
          { label: request ? "Edit" : "New" },
        ]}
      />
      <PageBody className="max-w-5xl">
        <form onSubmit={(e) => void run(false)(e)} className="flex flex-col gap-5" noValidate>
          {formError && <FormAlert>{formError}</FormAlert>}
          {request && request.status !== "DRAFT" && request.decision_reason && (
            <FormAlert>
              {humanize(request.status)}: {request.decision_reason}
            </FormAlert>
          )}

          <FormSection title="Where and when">
            <FormGrid>
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
              <FormField label="Site" error={errors.site_id?.message}>
                <Select {...form.register("site_id")} disabled={!projectId}>
                  <option value="">Whole project</option>
                  {sites.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Needed by" error={errors.required_date?.message}>
                <Input type="date" {...form.register("required_date")} />
              </FormField>
              <FormField label="Priority">
                <Select {...form.register("priority")}>
                  <option value="NORMAL">Normal</option>
                  <option value="URGENT">Urgent</option>
                </Select>
              </FormField>
              <FormField
                label="Justification"
                required
                className="sm:col-span-2"
                error={errors.justification?.message}
              >
                <Textarea
                  {...form.register("justification")}
                  placeholder="What the material is for — approvers read this"
                />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Materials">
            {errors.items?.message && (
              <p className="mb-2 text-xs text-danger">{errors.items.message}</p>
            )}
            {errors.items?.root?.message && (
              <p className="mb-2 text-xs text-danger">{errors.items.root.message}</p>
            )}
            <div className="flex flex-col gap-3">
              {lines.fields.map((field, i) => {
                const rowErrors = errors.items?.[i];
                const amount = previewAmount(
                  watched[i]?.quantity ?? "",
                  watched[i]?.estimated_rate ?? "",
                );
                return (
                  <div key={field.id} className="rounded-md border border-border p-3">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_1fr_1fr_auto]">
                      <FormField
                        label={`Line ${i + 1} material`}
                        error={rowErrors?.material_id?.message}
                      >
                        <Select
                          {...form.register(`items.${i}.material_id`, {
                            onChange: (e: { target: { value: string } }) => {
                              const picked = materials.rows.find((m) => m.id === e.target.value);
                              if (picked) form.setValue(`items.${i}.unit_id`, picked.base_unit_id);
                            },
                          })}
                        >
                          <option value="">Choose…</option>
                          {materials.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Quantity" error={rowErrors?.quantity?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`items.${i}.quantity`)}
                        />
                      </FormField>
                      <FormField label="Unit" error={rowErrors?.unit_id?.message}>
                        <Select {...form.register(`items.${i}.unit_id`)}>
                          <option value="">Choose…</option>
                          {units.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Estimated rate" error={rowErrors?.estimated_rate?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`items.${i}.estimated_rate`)}
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
                      {amount && (
                        <span className="ml-auto text-sm text-fg-muted">
                          Line estimate{" "}
                          <span className="tabular font-medium text-fg">{formatMoney(amount)}</span>
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
              <div className="flex items-center justify-between">
                <Button size="sm" onClick={() => lines.append({ ...emptyLine })}>
                  <Plus /> Add line
                </Button>
                <p className="text-sm">
                  Estimated total{" "}
                  <span className="tabular text-base font-semibold">
                    {formatMoney(String(total))}
                  </span>
                  <span className="ml-1 text-xs text-fg-subtle">decides who must approve</span>
                </p>
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

export function PurchaseRequestDetailPage() {
  const { requestId = "" } = useParams();
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);

  const pr = useQuery({
    queryKey: ["purchase-requests", "detail", requestId],
    queryFn: () => api.get<PurchaseRequestRead>(`/purchase-requests/${requestId}`),
  });
  const trail = useQuery({
    queryKey: ["approvals", "trail", "purchase_request", requestId],
    queryFn: () =>
      api.get<ApprovalRequestRead[]>("/approvals/requests", {
        query: { doc_type: "purchase_request", doc_id: requestId },
      }),
  });

  const submit = useMutation({
    mutationFn: () =>
      api.post<PurchaseRequestRead>(`/purchase-requests/${requestId}/submit`, undefined, {
        ifMatch: String(pr.data?.version),
      }),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(`${saved.pr_number} submitted for approval.`);
    },
    onError: (err) => toast.error(describeError(err)),
  });

  if (pr.isLoading) return <PageSkeleton />;
  if (pr.error || !pr.data)
    return <ErrorState error={pr.error} onRetry={() => void pr.refetch()} />;
  const p = pr.data;
  const [current, ...earlier] = trail.data ?? [];
  const total = p.items.reduce((sum, i) => sum + Number(i.estimated_amount), 0);

  return (
    <>
      <PageHeader
        title={p.pr_number}
        crumbs={[{ label: "Purchase requests", to: "/purchase-requests" }, { label: p.pr_number }]}
        meta={
          <>
            <StatusBadge status={p.status} />
            {p.priority === "URGENT" && <Tag>Urgent</Tag>}
            <span className="text-sm text-fg-muted">
              {p.project_code}
              {p.site_code && ` / ${p.site_code}`}
            </span>
            <span className="text-sm text-fg-muted">
              Raised by {p.requested_by_name ?? "—"} · {formatDateTime(p.created_at)}
            </span>
          </>
        }
        actions={
          <>
            {p.can_edit && (
              <Button asChild>
                <Link to={`/purchase-requests/${p.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {p.can_submit && (
              <Button variant="primary" onClick={() => submit.mutate()} loading={submit.isPending}>
                <Send /> Submit for approval
              </Button>
            )}
            {p.can_cancel && (
              <Button variant="danger" onClick={() => setCancelling(true)}>
                <Ban /> Cancel request
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {p.decision_reason && ["REJECTED", "CHANGES_REQUESTED"].includes(p.status) && (
          <FormAlert>
            {humanize(p.status)}: {p.decision_reason}
          </FormAlert>
        )}
        {p.cancel_reason && <FormAlert>Cancelled: {p.cancel_reason}</FormAlert>}

        <Section title="Details">
          <FieldGrid
            items={[
              {
                label: "Project",
                value: p.project_name ? `${p.project_code} — ${p.project_name}` : p.project_code,
              },
              { label: "Site", value: p.site_name ? `${p.site_code} — ${p.site_name}` : null },
              { label: "Phase", value: p.phase_code },
              { label: "Needed by", value: p.required_date ? formatDate(p.required_date) : null },
              { label: "Submitted", value: p.submitted_at ? formatDateTime(p.submitted_at) : null },
              { label: "Approved", value: p.approved_at ? formatDateTime(p.approved_at) : null },
              { label: "Justification", value: p.justification, wide: true },
            ]}
          />
        </Section>

        <Section title="Materials">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Requested materials</caption>
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
                    Est. rate
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Est. amount
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Sourced
                  </th>
                </tr>
              </thead>
              <tbody>
                {p.items.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="px-4 py-2 text-fg-muted">{i.line_no}</td>
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{i.material_sku}</span>{" "}
                      <span className="font-medium">{i.material_name}</span>
                      {i.description && <p className="text-xs text-fg-muted">{i.description}</p>}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.quantity, i.unit_code ?? undefined)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {i.estimated_rate ? formatMoney(i.estimated_rate) : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatMoney(i.estimated_amount)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.sourced_quantity)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border-strong bg-surface">
                  <td colSpan={4} className="px-4 py-2 text-right font-semibold">
                    Estimated total
                  </td>
                  <td data-numeric className="px-4 py-2 font-semibold">
                    {formatMoney(p.estimated_amount || String(total), {
                      currency: p.currency_code,
                    })}
                  </td>
                  <td />
                </tr>
              </tfoot>
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
                label={p.pr_number}
                onDone={() =>
                  void queryClient.invalidateQueries({ queryKey: ["purchase-requests"] })
                }
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
        open={cancelling}
        onOpenChange={setCancelling}
        title={`Cancel ${p.pr_number}?`}
        description="A cancelled request cannot be reopened. Raise a new one if it is needed again."
        confirmLabel={`Cancel ${p.pr_number}`}
        destructive
        reason={{ label: "Reason", required: true }}
        onConfirm={async (reason) => {
          await api.post(`/purchase-requests/${p.id}/cancel`, { reason });
          await queryClient.invalidateQueries();
          toast.success(`${p.pr_number} cancelled.`);
        }}
      />
    </>
  );
}
