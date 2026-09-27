import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Pencil, Plus, Trash2 } from "lucide-react";
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
import { api } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import { DECIMAL_RE } from "@/lib/forms";
import {
  useAccountOptions,
  useCostCenterOptions,
  usePagedList,
  usePhaseOptions,
  useProjectOptions,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatMoney } from "@/lib/utils";
import type { BudgetCreate, BudgetListItem, BudgetRead } from "@/types/models";

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<BudgetListItem>();

export function BudgetListPage() {
  const list = useListParams({ sort: "-fiscal_year" });
  const query = usePagedList<BudgetListItem>("budgets", "/finance/budgets", list.query);
  const projects = useProjectOptions();

  const columns = useMemo(
    () => [
      col.accessor("name", {
        header: "Budget",
        meta: { alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/finance/budgets/${c.row.original.id}`}
            className="font-medium text-primary hover:underline"
          >
            {c.getValue()}
          </Link>
        ),
      }),
      col.display({
        id: "project",
        header: "Project",
        cell: (c) => c.row.original.project_code ?? "—",
      }),
      col.accessor("fiscal_year", {
        header: "FY",
        meta: { sortKey: "fiscal_year" },
        cell: (c) => `FY${c.getValue()}`,
      }),
      col.accessor("status", {
        header: "Status",
        cell: (c) => <StatusBadge status={c.getValue()} />,
      }),
      col.accessor("total_amount", {
        header: "Total",
        meta: { numeric: true },
        cell: (c) => formatMoney(c.getValue()),
      }),
      col.accessor("approved_at", {
        header: "Approved",
        cell: (c) => (c.getValue() ? formatDate(c.getValue()) : "—"),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Budgets"
        subtitle="One project's spending plan for one fiscal year, and what has been committed and spent
          against it."
        actions={
          <PermissionGate permission="finance.budget.create">
            <Button variant="primary" asChild>
              <Link to="/finance/budgets/new">
                <Plus /> New budget
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="budgets"
          caption="Budgets"
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
          getRowHref={(r) => `/finance/budgets/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              search={false}
              savedViewsId="budgets"
              filters={[{ key: "project_id", label: "Project", options: projects.options }]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Form ------------------------------------------------------------------------

const lineSchema = z.object({
  account_id: z.string().min(1, "Choose an account"),
  budgeted_amount: z
    .string()
    .regex(DECIMAL_RE, "A number, up to 4 decimals")
    .refine((v) => Number(v) > 0, "More than zero"),
  phase_id: z.string(),
  cost_center_id: z.string(),
});

const schema = z.object({
  project_id: z.string().min(1, "Choose a project"),
  fiscal_year: z.string().regex(/^\d{4}$/, "A four-digit year"),
  name: z.string().trim().min(2, "Name it").max(160),
  lines: z.array(lineSchema).min(1, "Add at least one line"),
});
type Values = z.infer<typeof schema>;

const emptyLine = { account_id: "", budgeted_amount: "", phase_id: "", cost_center_id: "" };

function currentFiscalYear(): number {
  const today = new Date();
  const month = today.getMonth() + 1;
  return month >= 7 ? today.getFullYear() : today.getFullYear() - 1;
}

export function BudgetFormPage() {
  const { budgetId } = useParams();
  const existing = useQuery({
    queryKey: ["budgets", "detail", budgetId],
    queryFn: () => api.get<BudgetRead>(`/finance/budgets/${budgetId}`),
    enabled: Boolean(budgetId),
    staleTime: 0,
  });
  if (budgetId && existing.isLoading) return <PageSkeleton />;
  if (budgetId && existing.error) {
    return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />;
  }
  return <BudgetForm budget={existing.data} />;
}

function BudgetForm({ budget }: { budget?: BudgetRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projects = useProjectOptions();
  const accounts = useAccountOptions();
  const costCenters = useCostCenterOptions();
  const [formError, setFormError] = useState<string | null>(null);
  const postable = accounts.rows.filter((a) => a.is_postable && a.is_active);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: budget
      ? {
          project_id: budget.project_id,
          fiscal_year: String(budget.fiscal_year),
          name: budget.name,
          lines: budget.lines.map((l) => ({
            account_id: l.account_id,
            budgeted_amount: l.budgeted_amount,
            phase_id: l.phase_id ?? "",
            cost_center_id: l.cost_center_id ?? "",
          })),
        }
      : {
          project_id: "",
          fiscal_year: String(currentFiscalYear()),
          name: "",
          lines: [{ ...emptyLine }],
        },
  });
  const lines = useFieldArray({ control: form.control, name: "lines" });
  const errors = form.formState.errors;
  const projectId = form.watch("project_id");
  const phases = usePhaseOptions(projectId || undefined);

  const save = useMutation({
    mutationFn: (values: Values) => {
      const body: BudgetCreate = {
        project_id: values.project_id,
        fiscal_year: Number(values.fiscal_year),
        name: values.name,
        lines: values.lines.map((l) => ({
          account_id: l.account_id,
          budgeted_amount: l.budgeted_amount,
          phase_id: l.phase_id || null,
          cost_center_id: l.cost_center_id || null,
        })),
      };
      return budget
        ? api.put<BudgetRead>(`/finance/budgets/${budget.id}`, body, {
            ifMatch: String(budget.version),
          })
        : api.post<BudgetRead>("/finance/budgets", body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(`${saved.name} saved as a draft.`);
      navigate(`/finance/budgets/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["project_id", "fiscal_year", "name"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title={budget ? `Edit ${budget.name}` : "New budget"}
        crumbs={[
          { label: "Budgets", to: "/finance/budgets" },
          ...(budget ? [{ label: budget.name, to: `/finance/budgets/${budget.id}` }] : []),
          { label: budget ? "Edit" : "New" },
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
          <FormSection title="What">
            <FormGrid>
              <FormField label="Project" required error={errors.project_id?.message}>
                <Select {...form.register("project_id")} disabled={Boolean(budget)}>
                  <option value="">Choose…</option>
                  {projects.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Fiscal year" required error={errors.fiscal_year?.message}>
                <Input
                  inputMode="numeric"
                  className="tabular"
                  disabled={Boolean(budget)}
                  {...form.register("fiscal_year")}
                />
              </FormField>
              <FormField
                label="Name"
                required
                error={errors.name?.message}
                className="sm:col-span-2"
              >
                <Input {...form.register("name")} />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Lines">
            {errors.lines?.message && (
              <p className="mb-2 text-xs text-danger">{errors.lines.message}</p>
            )}
            <div className="flex flex-col gap-3">
              {lines.fields.map((field, i) => {
                const row = errors.lines?.[i];
                return (
                  <div
                    key={field.id}
                    className="grid grid-cols-1 gap-3 rounded-md border border-border p-3 md:grid-cols-[2fr_1fr_1fr_1fr_auto]"
                  >
                    <FormField label={`Line ${i + 1} account`} error={row?.account_id?.message}>
                      <Select {...form.register(`lines.${i}.account_id`)}>
                        <option value="">Choose…</option>
                        {postable.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.code} — {a.name}
                          </option>
                        ))}
                      </Select>
                    </FormField>
                    <FormField label="Phase">
                      <Select {...form.register(`lines.${i}.phase_id`)} disabled={!projectId}>
                        <option value="">Whole project</option>
                        {phases.options.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </Select>
                    </FormField>
                    <FormField label="Cost centre">
                      <Select {...form.register(`lines.${i}.cost_center_id`)}>
                        <option value="">—</option>
                        {costCenters.options.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </Select>
                    </FormField>
                    <FormField label="Budgeted" error={row?.budgeted_amount?.message}>
                      <Input
                        inputMode="decimal"
                        className="tabular"
                        {...form.register(`lines.${i}.budgeted_amount`)}
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
                );
              })}
              <div>
                <Button size="sm" onClick={() => lines.append({ ...emptyLine })}>
                  <Plus /> Add line
                </Button>
              </div>
            </div>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {budget ? "Save changes" : "Save draft"}
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

type Dialogs = null | "approve" | "revise" | "close";

export function BudgetDetailPage() {
  const { budgetId = "" } = useParams();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const [revisions, setRevisions] = useState<Record<string, string>>({});
  const query = useQuery({
    queryKey: ["budgets", "detail", budgetId],
    queryFn: () => api.get<BudgetRead>(`/finance/budgets/${budgetId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const b = query.data;

  return (
    <>
      <PageHeader
        title={b.name}
        crumbs={[{ label: "Budgets", to: "/finance/budgets" }, { label: b.name }]}
        meta={
          <>
            <StatusBadge status={b.status} />
            <span className="text-sm text-fg-muted">
              {b.project_code} · FY{b.fiscal_year}
            </span>
            <span className="text-sm tabular text-fg-muted">{formatMoney(b.total_amount)}</span>
          </>
        }
        actions={
          <>
            {b.can_edit && (
              <Button asChild>
                <Link to={`/finance/budgets/${b.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {b.can_approve && (
              <Button variant="primary" onClick={() => setDialog("approve")}>
                Approve
              </Button>
            )}
            {b.can_revise && (
              <Button
                onClick={() => {
                  setRevisions(
                    Object.fromEntries(
                      b.lines.map((l) => [l.id, l.revised_amount ?? l.budgeted_amount]),
                    ),
                  );
                  setDialog("revise");
                }}
              >
                Revise
              </Button>
            )}
            {b.can_close && (
              <Button variant="danger" onClick={() => setDialog("close")}>
                Close
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        <Section title="Lines">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Budget lines</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Account
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Phase / cost centre
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Budgeted
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Committed
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Actual
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Remaining
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Variance
                  </th>
                </tr>
              </thead>
              <tbody>
                {b.lines.map((l) => (
                  <tr key={l.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{l.account_code}</span>{" "}
                      {l.account_name}
                    </td>
                    <td className="px-4 py-2 text-xs text-fg-muted">
                      {[l.phase_code, l.cost_center_code].filter(Boolean).join(" · ") ||
                        "Whole project"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatMoney(l.revised_amount ?? l.budgeted_amount)}
                      {l.revised_amount != null && (
                        <p className="text-xs text-fg-subtle line-through">
                          {formatMoney(l.budgeted_amount)}
                        </p>
                      )}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatMoney(l.committed_amount)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatMoney(l.actual_amount)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatMoney(l.remaining_amount)}
                    </td>
                    <td
                      data-numeric
                      className={`px-4 py-2 ${
                        l.variance_pct != null && Number(l.variance_pct) > 0
                          ? "text-danger"
                          : "text-fg-muted"
                      }`}
                    >
                      {l.variance_pct != null ? `${Number(l.variance_pct).toFixed(1)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border font-semibold">
                  <td className="px-4 py-2" colSpan={2}>
                    Total
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(b.total_amount)}
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(
                      b.lines.reduce((sum, l) => sum + Number(l.committed_amount), 0).toString(),
                    )}
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(
                      b.lines.reduce((sum, l) => sum + Number(l.actual_amount), 0).toString(),
                    )}
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
              { label: "Approved", value: b.approved_at ? formatDate(b.approved_at) : null },
              { label: "Closed", value: b.closed_at ? formatDate(b.closed_at) : null },
            ]}
          />
        </Section>
      </PageBody>

      <ConfirmDialog
        open={dialog === "approve"}
        onOpenChange={(o) => setDialog(o ? "approve" : null)}
        title={`Approve ${b.name}?`}
        description="Purchase orders against this project and year can commit against it from then on."
        confirmLabel="Approve"
        onConfirm={async () => {
          await api.post(`/finance/budgets/${b.id}/approve`, undefined, {
            ifMatch: String(b.version),
          });
          await refresh();
          toast.success(`${b.name} approved.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "close"}
        onOpenChange={(o) => setDialog(o ? "close" : null)}
        title={`Close ${b.name}?`}
        description="This cannot be undone."
        confirmLabel="Close budget"
        destructive
        onConfirm={async () => {
          await api.post(`/finance/budgets/${b.id}/close`, undefined, {
            ifMatch: String(b.version),
          });
          await refresh();
          toast.success(`${b.name} closed.`);
        }}
      />

      {dialog === "revise" && (
        <ReviseDialog
          budget={b}
          revisions={revisions}
          setRevisions={setRevisions}
          onClose={() => setDialog(null)}
          onDone={() => void refresh()}
        />
      )}
    </>
  );
}

function ReviseDialog({
  budget,
  revisions,
  setRevisions,
  onClose,
  onDone,
}: {
  budget: BudgetRead;
  revisions: Record<string, string>;
  setRevisions: (r: Record<string, string>) => void;
  onClose: () => void;
  onDone: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `/finance/budgets/${budget.id}/revise`,
        { revisions },
        { ifMatch: String(budget.version) },
      ),
    onSuccess: () => {
      onDone();
      toast.success(`${budget.name} revised.`);
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={`Revise ${budget.name}`}
      description="The original budgeted figure is kept; only the revised figure changes."
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
            Save revision
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {budget.lines.map((l) => (
          <FormField key={l.id} label={`${l.account_code} — ${l.account_name}`}>
            <Input
              inputMode="decimal"
              className="tabular"
              value={revisions[l.id] ?? ""}
              onChange={(e) => setRevisions({ ...revisions, [l.id]: e.target.value })}
            />
          </FormField>
        ))}
        {error && (
          <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
