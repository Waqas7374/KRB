import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Pencil, Plus, RotateCcw, Send, Trash2, Undo2 } from "lucide-react";
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
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import {
  useAccountOptions,
  useCostCenterOptions,
  useMaterialOptions,
  usePagedList,
  useProjectOptions,
  useSiteOptions,
  useVendorOptions,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney, humanize } from "@/lib/utils";
import type { JournalEntryCreate, JournalEntryListItem, JournalEntryRead } from "@/types/models";

const STATUS_OPTIONS = ["DRAFT", "PENDING_APPROVAL", "POSTED", "REVERSED"].map((value) => ({
  value,
  label: humanize(value),
}));
const SOURCE_OPTIONS = [
  "MANUAL",
  "GRN",
  "INVENTORY",
  "INVOICE",
  "PAYMENT",
  "PAYROLL",
  "DEPRECIATION",
  "OPENING_BALANCE",
].map((value) => ({ value, label: humanize(value) }));

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<JournalEntryListItem>();

export function JournalEntryListPage() {
  const list = useListParams({ sort: "-entry_date" });
  const query = usePagedList<JournalEntryListItem>(
    "journal-entries",
    "/finance/journal-entries",
    list.query,
  );

  const columns = useMemo(
    () => [
      col.accessor("je_number", {
        header: "Entry",
        meta: { sortKey: "je_number", alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/finance/journal-entries/${c.row.original.id}`}
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
      col.accessor("entry_date", {
        header: "Date",
        meta: { sortKey: "entry_date" },
        cell: (c) => formatDate(c.getValue()),
      }),
      col.accessor("description", { header: "Description" }),
      col.accessor("source_type", { header: "Source", cell: (c) => humanize(c.getValue()) }),
      col.accessor("total_debit", {
        header: "Amount",
        meta: { numeric: true },
        cell: (c) => formatMoney(c.getValue()),
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
        title="Journal entries"
        subtitle="Every manual posting to the general ledger. Nothing moves until every step signs it."
        actions={
          <PermissionGate permission="finance.gl.create">
            <Button variant="primary" asChild>
              <Link to="/finance/journal-entries/new">
                <Plus /> New entry
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="journal-entries"
          caption="Journal entries"
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
          getRowHref={(r) => `/finance/journal-entries/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Number, description or reference…  ( / )"
              savedViewsId="journal-entries"
              filters={[
                { key: "status", label: "Status", options: STATUS_OPTIONS },
                { key: "source_type", label: "Source", options: SOURCE_OPTIONS },
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
    account_id: z.string().min(1, "Choose an account"),
    debit: z.string().regex(DECIMAL_RE, "A number, up to 4 decimals").or(z.literal("")),
    credit: z.string().regex(DECIMAL_RE, "A number, up to 4 decimals").or(z.literal("")),
    description: z.string().max(300),
    project_id: z.string(),
    site_id: z.string(),
    cost_center_id: z.string(),
    vendor_id: z.string(),
    material_id: z.string(),
  })
  .refine((l) => (Number(l.debit) || 0) > 0 !== (Number(l.credit) || 0) > 0, {
    path: ["debit"],
    message: "A line is a debit or a credit, never both and never neither",
  });

const schema = z.object({
  entry_date: z.string().min(1, "Choose a date"),
  description: z.string().trim().min(3, "Say what this entry is for").max(500),
  reference: z.string().max(100),
  lines: z.array(lineSchema).min(2, "A journal entry needs at least two lines"),
});
type Values = z.infer<typeof schema>;

const emptyLine = {
  account_id: "",
  debit: "",
  credit: "",
  description: "",
  project_id: "",
  site_id: "",
  cost_center_id: "",
  vendor_id: "",
  material_id: "",
};

export function JournalEntryFormPage() {
  const { jeId } = useParams();
  const existing = useQuery({
    queryKey: ["journal-entries", "detail", jeId],
    queryFn: () => api.get<JournalEntryRead>(`/finance/journal-entries/${jeId}`),
    enabled: Boolean(jeId),
    staleTime: 0,
  });
  if (jeId && existing.isLoading) return <PageSkeleton />;
  if (jeId && existing.error) {
    return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />;
  }
  return <JournalEntryForm entry={existing.data} />;
}

function JournalEntryForm({ entry }: { entry?: JournalEntryRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const accounts = useAccountOptions();
  const projects = useProjectOptions();
  const sites = useSiteOptions();
  const costCenters = useCostCenterOptions();
  const vendors = useVendorOptions();
  const materials = useMaterialOptions();
  const [formError, setFormError] = useState<string | null>(null);

  const postable = accounts.rows.filter((a) => a.is_postable && a.is_active);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: entry
      ? {
          entry_date: entry.entry_date,
          description: entry.description,
          reference: entry.reference ?? "",
          lines: entry.lines.map((l) => ({
            account_id: l.account_id,
            debit: Number(l.debit) > 0 ? l.debit : "",
            credit: Number(l.credit) > 0 ? l.credit : "",
            description: l.description ?? "",
            project_id: l.project_id ?? "",
            site_id: l.site_id ?? "",
            cost_center_id: l.cost_center_id ?? "",
            vendor_id: l.vendor_id ?? "",
            material_id: l.material_id ?? "",
          })),
        }
      : {
          entry_date: new Date().toISOString().slice(0, 10),
          description: "",
          reference: "",
          lines: [{ ...emptyLine }, { ...emptyLine }],
        },
  });
  const lines = useFieldArray({ control: form.control, name: "lines" });
  const errors = form.formState.errors;
  const watched = form.watch("lines");
  const totalDebit = watched.reduce((sum, l) => sum + (Number(l.debit) || 0), 0);
  const totalCredit = watched.reduce((sum, l) => sum + (Number(l.credit) || 0), 0);

  const save = useMutation({
    mutationFn: (values: Values) => {
      const body = {
        ...emptyToNull({
          entry_date: values.entry_date,
          description: values.description,
          reference: values.reference,
        }),
        lines: values.lines.map((l) => ({
          account_id: l.account_id,
          debit: l.debit || "0",
          credit: l.credit || "0",
          ...emptyToNull({
            description: l.description,
            project_id: l.project_id,
            site_id: l.site_id,
            cost_center_id: l.cost_center_id,
            vendor_id: l.vendor_id,
            material_id: l.material_id,
          }),
        })),
      } as unknown as JournalEntryCreate;
      return entry
        ? api.put<JournalEntryRead>(`/finance/journal-entries/${entry.id}`, body, {
            ifMatch: String(entry.version),
          })
        : api.post<JournalEntryRead>("/finance/journal-entries", body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(`${saved.je_number} saved as a draft. Submit it for approval.`);
      navigate(`/finance/journal-entries/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["entry_date", "description"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title={entry ? `Edit ${entry.je_number}` : "New journal entry"}
        subtitle="It moves nothing in the ledger until it is approved."
        crumbs={[
          { label: "Journal entries", to: "/finance/journal-entries" },
          ...(entry
            ? [{ label: entry.je_number, to: `/finance/journal-entries/${entry.id}` }]
            : []),
          { label: entry ? "Edit" : "New" },
        ]}
      />
      <PageBody className="max-w-5xl">
        <form
          onSubmit={(e) => {
            setFormError(null);
            void form.handleSubmit((v) => save.mutate(v))(e);
          }}
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}
          {entry?.decision_reason && entry.status === "DRAFT" && (
            <FormAlert>{entry.decision_reason}</FormAlert>
          )}
          <FormSection title="What">
            <FormGrid>
              <FormField label="Date" required error={errors.entry_date?.message}>
                <Input type="date" {...form.register("entry_date")} />
              </FormField>
              <FormField label="Reference" hint="A voucher or document number, if there is one.">
                <Input {...form.register("reference")} />
              </FormField>
              <FormField
                label="Description"
                required
                error={errors.description?.message}
                className="sm:col-span-2"
              >
                <Textarea rows={2} {...form.register("description")} />
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
                  <div key={field.id} className="rounded-md border border-border p-3">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_1fr_auto]">
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
                      <FormField label="Debit" error={row?.debit?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`lines.${i}.debit`)}
                        />
                      </FormField>
                      <FormField label="Credit" error={row?.credit?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`lines.${i}.credit`)}
                        />
                      </FormField>
                      <div className="flex items-end pb-0.5">
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Remove line ${i + 1}`}
                          onClick={() => lines.remove(i)}
                          disabled={lines.fields.length <= 2}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                      <FormField label="Line note">
                        <Input {...form.register(`lines.${i}.description`)} />
                      </FormField>
                      <FormField label="Project">
                        <Select {...form.register(`lines.${i}.project_id`)}>
                          <option value="">—</option>
                          {projects.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Site">
                        <Select {...form.register(`lines.${i}.site_id`)}>
                          <option value="">—</option>
                          {sites.options.map((o) => (
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
                      <FormField label="Vendor">
                        <Select {...form.register(`lines.${i}.vendor_id`)}>
                          <option value="">—</option>
                          {vendors.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Material">
                        <Select {...form.register(`lines.${i}.material_id`)}>
                          <option value="">—</option>
                          {materials.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
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
            <p
              className={`mt-3 text-sm ${
                totalDebit === totalCredit ? "text-fg-muted" : "text-danger"
              }`}
            >
              Debits {formatMoney(String(totalDebit))} · Credits {formatMoney(String(totalCredit))}
              {totalDebit !== totalCredit &&
                ` · out of balance by ${formatMoney(String(Math.abs(totalDebit - totalCredit)))}`}
            </p>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {entry ? "Save changes" : "Save draft"}
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

type Dialogs = null | "submit" | "withdraw" | "delete" | "reverse";

export function JournalEntryDetailPage() {
  const { jeId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const canReverse = useCan("finance.gl.reverse");
  const [dialog, setDialog] = useState<Dialogs>(null);
  const query = useQuery({
    queryKey: ["journal-entries", "detail", jeId],
    queryFn: () => api.get<JournalEntryRead>(`/finance/journal-entries/${jeId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const e = query.data;

  return (
    <>
      <PageHeader
        title={e.je_number}
        crumbs={[
          { label: "Journal entries", to: "/finance/journal-entries" },
          { label: e.je_number },
        ]}
        meta={
          <>
            <StatusBadge status={e.status} />
            <span className="text-sm text-fg-muted">{humanize(e.source_type)}</span>
            <span className="text-sm text-fg-muted">
              Period {e.period_no}, FY{e.fiscal_year}
            </span>
            <span className="text-sm tabular text-fg-muted">{formatMoney(e.total_debit)}</span>
          </>
        }
        actions={
          <>
            {e.can_edit && (
              <Button asChild>
                <Link to={`/finance/journal-entries/${e.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {e.can_submit && (
              <Button variant="primary" onClick={() => setDialog("submit")}>
                <Send /> Submit for approval
              </Button>
            )}
            {e.can_withdraw && (
              <Button onClick={() => setDialog("withdraw")}>
                <Undo2 /> Withdraw
              </Button>
            )}
            {e.can_delete && (
              <Button variant="danger" onClick={() => setDialog("delete")}>
                <Trash2 /> Delete
              </Button>
            )}
            {e.can_reverse && canReverse && (
              <Button variant="danger" onClick={() => setDialog("reverse")}>
                <RotateCcw /> Reverse
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {e.status === "DRAFT" && e.decision_reason && <FormAlert>{e.decision_reason}</FormAlert>}
        {e.reversal_of_id && (
          <FormAlert>
            This reverses{" "}
            <Link to={`/finance/journal-entries/${e.reversal_of_id}`} className="underline">
              another entry
            </Link>
            .
          </FormAlert>
        )}

        <Section title="Lines">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Journal entry lines</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Account
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Dimensions
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Debit
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Credit
                  </th>
                </tr>
              </thead>
              <tbody>
                {e.lines.map((l) => (
                  <tr key={l.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{l.account_code}</span>{" "}
                      <span className="font-medium">{l.account_name}</span>
                      {l.description && <p className="text-xs text-fg-muted">{l.description}</p>}
                    </td>
                    <td className="px-4 py-2 text-xs text-fg-muted">
                      {[
                        l.project_code,
                        l.site_code,
                        l.cost_center_code,
                        l.vendor_name,
                        l.material_name,
                      ]
                        .filter(Boolean)
                        .join(" · ") || "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(l.debit) > 0 ? formatMoney(l.debit) : "—"}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(l.credit) > 0 ? formatMoney(l.credit) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border font-medium">
                  <td className="px-4 py-2" colSpan={2}>
                    Total
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(e.total_debit)}
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(e.total_credit)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Section>

        <Section title="Details">
          <FieldGrid
            items={[
              { label: "Reference", value: e.reference },
              { label: "Submitted", value: e.submitted_at ? formatDateTime(e.submitted_at) : null },
              { label: "Posted", value: e.posted_at ? formatDateTime(e.posted_at) : null },
              { label: "Description", value: e.description, wide: true },
            ]}
          />
        </Section>

        <ApprovalSection
          docType="journal_entry"
          docId={e.id}
          label={e.je_number}
          empty="Not submitted yet. Once it is, the people who must sign it and each decision appear here."
        />
      </PageBody>

      <ConfirmDialog
        open={dialog === "submit"}
        onOpenChange={(o) => setDialog(o ? "submit" : null)}
        title={`Submit ${e.je_number}?`}
        description="It goes to the people who must sign it. Nothing posts until every step is approved."
        confirmLabel="Submit for approval"
        onConfirm={async () => {
          await api.post(`/finance/journal-entries/${e.id}/submit`, undefined, {
            ifMatch: String(e.version),
          });
          await refresh();
          toast.success(`${e.je_number} submitted for approval.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "withdraw"}
        onOpenChange={(o) => setDialog(o ? "withdraw" : null)}
        title={`Withdraw ${e.je_number}?`}
        description="It goes back to a draft so you can change it. Only possible before anyone has approved a step."
        confirmLabel="Withdraw"
        onConfirm={async () => {
          await api.post(`/finance/journal-entries/${e.id}/withdraw`);
          await refresh();
          toast.success(`${e.je_number} withdrawn.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        onOpenChange={(o) => setDialog(o ? "delete" : null)}
        title={`Delete ${e.je_number}?`}
        description="It never happened, so there is nothing to reverse — this removes it entirely."
        confirmLabel="Delete"
        destructive
        onConfirm={async () => {
          await api.delete(`/finance/journal-entries/${e.id}`);
          toast.success(`${e.je_number} deleted.`);
          navigate("/finance/journal-entries");
        }}
      />
      <ConfirmDialog
        open={dialog === "reverse"}
        onOpenChange={(o) => setDialog(o ? "reverse" : null)}
        title={`Reverse ${e.je_number}?`}
        description="Posts a new entry with every line swapped. The original stays on the books, marked reversed."
        confirmLabel="Reverse"
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          const rev = await api.post<JournalEntryRead>(`/finance/journal-entries/${e.id}/reverse`, {
            reason,
          });
          await refresh();
          toast.success(`${rev.je_number} posted as the reversal.`);
          navigate(`/finance/journal-entries/${rev.id}`);
        }}
      />
    </>
  );
}
