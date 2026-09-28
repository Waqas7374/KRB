import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Ban, Check, Plus } from "lucide-react";
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
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import { emptyToNull } from "@/lib/forms";
import { usePagedList, useSiteOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney, humanize } from "@/lib/utils";
import type { StockIssueCreate, StockIssueListItem, StockIssueRead } from "@/types/models";

import { emptyLine, lineSchema, useWarehousePicker } from "./stock-helpers";
import { LinesSection, MovementLinesTable } from "./stock-shared";

const STATUS_OPTIONS = ["DRAFT", "ISSUED", "CANCELLED"].map((value) => ({
  value,
  label: humanize(value),
}));
const TARGET_TYPES = ["EMPLOYEE", "CONTRACTOR", "WORK_ORDER"];

// --- List ------------------------------------------------------------------------

const col = createColumnHelper<StockIssueListItem>();

export function IssueListPage() {
  const list = useListParams({ sort: "-created_at" });
  const query = usePagedList<StockIssueListItem>("stock-issues", "/inventory/issues", list.query);
  const sites = useSiteOptions();

  const columns = useMemo(
    () => [
      col.accessor("issue_number", {
        header: "Issue",
        meta: { sortKey: "issue_number", alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/inventory/issues/${c.row.original.id}`}
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
      col.accessor("issued_to_name", {
        header: "Issued to",
        cell: (c) => (
          <span>
            {c.getValue()}{" "}
            <span className="text-xs text-fg-muted">
              ({humanize(c.row.original.issued_to_type)})
            </span>
          </span>
        ),
      }),
      col.accessor("purpose", { header: "Purpose" }),
      col.accessor("warehouse_code", { header: "Store", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("site_code", { header: "Site", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("issue_date", {
        header: "Date",
        meta: { sortKey: "issue_date" },
        cell: (c) => formatDate(c.getValue()),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Stock issues"
        subtitle="Material handed out of a store to a person, a contractor or a job. Posting one takes the stock out at its current average cost."
        actions={
          <PermissionGate permission="inventory.issue">
            <Button variant="primary" asChild>
              <Link to="/inventory/issues/new">
                <Plus /> New issue
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="stock-issues"
          caption="Stock issues"
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
          getRowHref={(r) => `/inventory/issues/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Number, person or purpose…  ( / )"
              savedViewsId="stock-issues"
              filters={[
                { key: "status", label: "Status", options: STATUS_OPTIONS },
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

const schema = z.object({
  warehouse_id: z.string().min(1, "Choose the store it comes from"),
  issued_to_type: z.enum(["EMPLOYEE", "CONTRACTOR", "WORK_ORDER"]),
  issued_to_name: z.string().trim().min(2, "Say who or what it goes to").max(160),
  purpose: z.string().trim().min(3, "Say what it is for").max(300),
  issue_date: z.string(),
  remarks: z.string().max(1000),
  post_now: z.boolean(),
  lines: z.array(lineSchema).min(1, "Add at least one line"),
});
type Values = z.infer<typeof schema>;

export function IssueFormPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const stores = useWarehousePicker("issue");
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      warehouse_id: "",
      issued_to_type: "CONTRACTOR",
      issued_to_name: "",
      purpose: "",
      issue_date: "",
      remarks: "",
      post_now: true,
      lines: [{ ...emptyLine }],
    },
  });
  const errors = form.formState.errors;

  const save = useMutation({
    mutationFn: async (values: Values) => {
      const body = {
        ...emptyToNull({
          warehouse_id: values.warehouse_id,
          issued_to_type: values.issued_to_type,
          issued_to_name: values.issued_to_name,
          purpose: values.purpose,
          issue_date: values.issue_date,
          remarks: values.remarks,
        }),
        lines: values.lines.map((l) => emptyToNull(l)),
      } as unknown as StockIssueCreate;
      const draft = await api.post<StockIssueRead>("/inventory/issues", body);
      if (!values.post_now) return draft;
      try {
        return await api.post<StockIssueRead>(`/inventory/issues/${draft.id}/post`, undefined, {
          ifMatch: String(draft.version),
        });
      } catch (err) {
        // The draft exists; say so, rather than leaving it behind unmentioned.
        toast.error(
          `${draft.issue_number} was saved as a draft but not posted: ${describeError(err)}`,
        );
        return draft;
      }
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(
        saved.status === "ISSUED"
          ? `${saved.issue_number} issued — the stock has left the store.`
          : `${saved.issue_number} saved as a draft. Nothing has left the store yet.`,
      );
      navigate(`/inventory/issues/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["warehouse_id", "issue_date"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title="New stock issue"
        subtitle="Hand material out of a store. It is charged at the store's current average cost."
        crumbs={[{ label: "Stock issues", to: "/inventory/issues" }, { label: "New" }]}
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
          <FormSection title="From and to">
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
              <FormField label="Date" error={errors.issue_date?.message} hint="Left blank, today.">
                <Input type="date" {...form.register("issue_date")} />
              </FormField>
              <FormField label="Issued to" required error={errors.issued_to_type?.message}>
                <Select {...form.register("issued_to_type")}>
                  {TARGET_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {humanize(t)}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Name" required error={errors.issued_to_name?.message}>
                <Input
                  {...form.register("issued_to_name")}
                  placeholder="e.g. Al-Noor Contractors"
                />
              </FormField>
              <FormField
                label="Purpose"
                required
                error={errors.purpose?.message}
                className="sm:col-span-2"
              >
                <Input {...form.register("purpose")} placeholder="e.g. Road base, block C" />
              </FormField>
            </FormGrid>
          </FormSection>

          <LinesSection form={form} error={errors.lines?.message} />

          <FormSection title="Notes">
            <FormField label="Remarks">
              <Textarea rows={2} {...form.register("remarks")} />
            </FormField>
            <label className="mt-3 flex items-center gap-2 text-sm">
              <input type="checkbox" {...form.register("post_now")} />
              Post now — the stock leaves the store immediately
            </label>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {form.watch("post_now") ? "Issue material" : "Save draft"}
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

type Dialogs = null | "post" | "cancel";

export function IssueDetailPage() {
  const { issueId = "" } = useParams();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const query = useQuery({
    queryKey: ["stock-issues", "detail", issueId],
    queryFn: () => api.get<StockIssueRead>(`/inventory/issues/${issueId}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const i = query.data;

  return (
    <>
      <PageHeader
        title={i.issue_number}
        crumbs={[{ label: "Stock issues", to: "/inventory/issues" }, { label: i.issue_number }]}
        meta={
          <>
            <StatusBadge status={i.status} />
            <span className="text-sm text-fg-muted">
              {i.warehouse_code} → {i.issued_to_name}
            </span>
            {!i.prices_hidden && i.total_value && (
              <span className="text-sm text-fg-muted">
                Value{" "}
                <span className="tabular font-medium text-fg">{formatMoney(i.total_value)}</span>
              </span>
            )}
          </>
        }
        actions={
          <>
            {i.can_post && (
              <Button variant="primary" onClick={() => setDialog("post")}>
                <Check /> Post
              </Button>
            )}
            {i.can_cancel && (
              <Button variant="danger" onClick={() => setDialog("cancel")}>
                <Ban /> Cancel
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {i.cancel_reason && <FormAlert>Cancelled: {i.cancel_reason}</FormAlert>}
        <Section title="Lines">
          <MovementLinesTable
            lines={i.items}
            hidden={i.prices_hidden}
            caption="Material issued, line by line"
          />
        </Section>
        <Section title="Details">
          <FieldGrid
            items={[
              { label: "Store", value: `${i.warehouse_code} — ${i.warehouse_name}` },
              { label: "Site", value: i.site_code },
              { label: "Issued to", value: `${i.issued_to_name} (${humanize(i.issued_to_type)})` },
              { label: "Purpose", value: i.purpose },
              { label: "Date", value: formatDate(i.issue_date) },
              { label: "Posted", value: i.issued_at ? formatDateTime(i.issued_at) : null },
              {
                label: "Journal entry",
                value: i.journal_entry_id ? (
                  <Link
                    to={`/finance/journal-entries/${i.journal_entry_id}`}
                    className="font-mono text-primary hover:underline"
                  >
                    View posting
                  </Link>
                ) : null,
              },
              { label: "Remarks", value: i.remarks, wide: true },
            ]}
          />
        </Section>
      </PageBody>

      <ConfirmDialog
        open={dialog === "post"}
        onOpenChange={(o) => setDialog(o ? "post" : null)}
        title={`Post ${i.issue_number}?`}
        description="The stock leaves the store now, at its current average cost. If it is not all there, nothing is posted."
        confirmLabel={`Post ${i.issue_number}`}
        onConfirm={async () => {
          await api.post(`/inventory/issues/${i.id}/post`, undefined, {
            ifMatch: String(i.version),
          });
          await refresh();
          toast.success(`${i.issue_number} issued.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "cancel"}
        onOpenChange={(o) => setDialog(o ? "cancel" : null)}
        title={`Cancel ${i.issue_number}?`}
        description={
          i.status === "ISSUED"
            ? "The stock goes back into the store at the cost it left at, by contra entries. Nothing is deleted."
            : "The draft is closed. Nothing has left the store."
        }
        confirmLabel={`Cancel ${i.issue_number}`}
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (reason) => {
          await api.post(`/inventory/issues/${i.id}/cancel`, { reason });
          await refresh();
          toast.success(`${i.issue_number} cancelled.`);
        }}
      />
    </>
  );
}
