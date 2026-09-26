import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { History, Plus, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input, Select, Textarea } from "@/components/ui/input";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { api, type Page } from "@/lib/api";
import { describeError } from "@/lib/errors";
import {
  useMaterialOptions,
  usePagedList,
  useProjectOptions,
  useSiteOptions,
  useUnitOptions,
  useVendorOptions,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney } from "@/lib/utils";
import type { VendorRateHistory, VendorRateRead } from "@/types/models";

const STATUS_OPTIONS = [
  { value: "ACTIVE", label: "Approved" },
  { value: "PENDING_APPROVAL", label: "Pending approval" },
  { value: "REJECTED", label: "Rejected" },
  { value: "WITHDRAWN", label: "Withdrawn" },
];

const today = () => new Date().toISOString().slice(0, 10);
const trim = (v: string) => (v.includes(".") ? v.replace(/\.?0+$/, "") || "0" : v);

const col = createColumnHelper<VendorRateRead>();

export function VendorRatesPage() {
  const list = useListParams({ sort: "-effective_from" });
  const query = usePagedList<VendorRateRead>("vendor-rates", "/vendor-rates", {
    ...list.query,
    current: list.query.current === "true" ? "true" : undefined,
  });
  const vendors = useVendorOptions();
  const materials = useMaterialOptions();
  const canHistory = useCan("rates.view_history");
  const queryClient = useQueryClient();
  const [proposing, setProposing] = useState(false);
  const [historyFor, setHistoryFor] = useState<VendorRateRead | null>(null);

  const withdraw = useMutation({
    mutationFn: (id: string) => api.post<VendorRateRead>(`/vendor-rates/${id}/withdraw`),
    onSuccess: async () => {
      await queryClient.invalidateQueries();
      toast.success("Proposal withdrawn.");
    },
    onError: (err) => toast.error(describeError(err)),
  });

  const columns = useMemo(
    () => [
      col.accessor("vendor_name", {
        header: "Vendor",
        meta: { alwaysVisible: true },
        cell: (c) => (
          <span>
            <span className="font-mono text-xs text-fg-muted">{c.row.original.vendor_code}</span>{" "}
            <span className="font-medium">{c.getValue()}</span>
          </span>
        ),
      }),
      col.accessor("material_name", {
        header: "Material",
        cell: (c) => (
          <span>
            <span className="font-mono text-xs text-fg-muted">{c.row.original.material_sku}</span>{" "}
            {c.getValue()}
          </span>
        ),
      }),
      col.accessor("rate", {
        header: "Rate",
        meta: { numeric: true, sortKey: "rate" },
        cell: (c) => (
          <span>
            {formatMoney(c.getValue(), { currency: c.row.original.currency_code, decimals: 2 })}
            <span className="text-xs text-fg-muted"> / {c.row.original.unit_code}</span>
          </span>
        ),
      }),
      col.accessor("change_pct", {
        header: "Change",
        meta: { numeric: true },
        cell: (c) => {
          const v = c.getValue();
          if (v == null) return <span className="text-fg-subtle">first rate</span>;
          const n = Number(v);
          return (
            <span className={n > 0 ? "text-danger" : n < 0 ? "text-success" : undefined}>
              {n > 0 ? "+" : ""}
              {n.toFixed(2)}%
            </span>
          );
        },
      }),
      col.accessor("scope", { header: "Applies to" }),
      col.display({
        id: "period",
        header: "Effective",
        meta: { sortKey: "effective_from" },
        cell: (c) => (
          <span>
            {formatDate(c.row.original.effective_from)} →{" "}
            {c.row.original.effective_to ? formatDate(c.row.original.effective_to) : "open"}
            {c.row.original.is_current && <Tag className="ml-1.5 text-success">Current</Tag>}
          </span>
        ),
      }),
      col.accessor("status", {
        header: "Status",
        meta: { sortKey: "status" },
        cell: (c) => <StatusBadge status={c.getValue()} />,
      }),
      col.display({
        id: "actions",
        header: () => <span className="sr-only">Actions</span>,
        meta: { alwaysVisible: true },
        cell: (c) => (
          <span className="flex justify-end gap-1">
            {c.row.original.can_withdraw && (
              <Button
                size="sm"
                onClick={() => withdraw.mutate(c.row.original.id)}
                disabled={withdraw.isPending}
              >
                <Undo2 /> Withdraw
              </Button>
            )}
            {canHistory && (
              <Button
                size="icon"
                variant="ghost"
                aria-label={`History of ${c.row.original.vendor_name} / ${c.row.original.material_name}`}
                onClick={() => setHistoryFor(c.row.original)}
              >
                <History />
              </Button>
            )}
          </span>
        ),
      }),
    ],
    [canHistory, withdraw],
  );

  return (
    <>
      <PageHeader
        title="Vendor rates"
        subtitle="What each vendor charges, from when. A rate is never edited: a change is a new period, approved before it takes effect."
        actions={
          <PermissionGate permission="rates.create">
            <Button variant="primary" onClick={() => setProposing(true)}>
              <Plus /> New rate
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="vendor-rates"
          caption="Vendor rates"
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
          toolbar={
            <FilterBar
              list={list}
              savedViewsId="vendor-rates"
              filters={[
                { key: "vendor_id", label: "Vendor", options: vendors.options },
                { key: "material_id", label: "Material", options: materials.options },
                { key: "status", label: "Status", options: STATUS_OPTIONS },
                {
                  key: "current",
                  label: "In force",
                  options: [{ value: "true", label: "Current rates only" }],
                },
              ]}
            />
          }
        />
      </PageBody>

      {proposing && <ProposeDialog onClose={() => setProposing(false)} />}
      {historyFor && <HistoryDialog rate={historyFor} onClose={() => setHistoryFor(null)} />}
    </>
  );
}

// --- Propose ---------------------------------------------------------------------

function ProposeDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const vendors = useVendorOptions();
  const materials = useMaterialOptions();
  const units = useUnitOptions();
  const projects = useProjectOptions();
  const [form, setForm] = useState({
    vendor_id: "",
    material_id: "",
    unit_id: "",
    rate: "",
    project_id: "",
    site_id: "",
    effective_from: today(),
    reason: "",
  });
  const sites = useSiteOptions(form.project_id || undefined);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));

  const propose = useMutation({
    mutationFn: () => {
      if (!/^\d+(\.\d{1,6})?$/.test(form.rate.trim())) {
        throw new Error("Enter the rate as a plain number.");
      }
      return api.post<VendorRateRead>("/vendor-rates", {
        vendor_id: form.vendor_id,
        material_id: form.material_id,
        unit_id: form.unit_id,
        rate: form.rate.trim(),
        project_id: form.project_id || null,
        site_id: form.site_id || null,
        effective_from: form.effective_from,
        reason: form.reason || null,
      });
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(
        saved.status === "ACTIVE"
          ? "Rate recorded and in force."
          : "Rate submitted; it takes effect once approved.",
      );
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      variant="drawer"
      title="New rate"
      description="A new period for a vendor and material. Depending on the size of the change it may need approval before it takes effect."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={propose.isPending}
            disabled={!form.vendor_id || !form.material_id || !form.unit_id || !form.rate}
            onClick={() => {
              setError(null);
              propose.mutate();
            }}
          >
            Submit rate
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormField label="Vendor" required>
          <Select value={form.vendor_id} onChange={(e) => set({ vendor_id: e.target.value })}>
            <option value="">Choose…</option>
            {vendors.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Material" required>
          <Select
            value={form.material_id}
            onChange={(e) => {
              const picked = materials.rows.find((m) => m.id === e.target.value);
              set({ material_id: e.target.value, unit_id: picked?.base_unit_id ?? form.unit_id });
            }}
          >
            <option value="">Choose…</option>
            {materials.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </FormField>
        <div className="grid grid-cols-2 gap-3">
          <FormField label="Rate" required hint="Per unit, in PKR.">
            <Input
              inputMode="decimal"
              className="tabular"
              value={form.rate}
              onChange={(e) => set({ rate: e.target.value })}
            />
          </FormField>
          <FormField label="Priced per" required>
            <Select value={form.unit_id} onChange={(e) => set({ unit_id: e.target.value })}>
              <option value="">Choose…</option>
              {units.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
        </div>
        <fieldset className="flex flex-col gap-2 rounded-md border border-border p-3">
          <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
            Applies to
          </legend>
          <p className="text-xs text-fg-muted">
            Leave both blank for a company-wide rate. A site rate beats a project rate, which beats
            the company rate.
          </p>
          <FormField label="Project">
            <Select
              value={form.project_id}
              onChange={(e) => set({ project_id: e.target.value, site_id: "" })}
            >
              <option value="">All projects</option>
              {projects.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Site">
            <Select
              value={form.site_id}
              disabled={!form.project_id}
              onChange={(e) => set({ site_id: e.target.value })}
            >
              <option value="">Whole project</option>
              {sites.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
        </fieldset>
        <FormField
          label="Effective from"
          required
          hint="Must be after the start of any earlier period: rates only move forward."
        >
          <Input
            type="date"
            value={form.effective_from}
            onChange={(e) => set({ effective_from: e.target.value })}
          />
        </FormField>
        <FormField label="Reason">
          <Textarea
            rows={2}
            value={form.reason}
            onChange={(e) => set({ reason: e.target.value })}
            placeholder="e.g. monsoon freight"
          />
        </FormField>
        {error && (
          <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}

// --- History ---------------------------------------------------------------------

function HistoryDialog({ rate, onClose }: { rate: VendorRateRead; onClose: () => void }) {
  const history = useQuery({
    queryKey: ["vendor-rates", "history", rate.vendor_id, rate.material_id],
    queryFn: () =>
      api.get<Page<VendorRateHistory>>("/vendor-rates/history", {
        query: { vendor_id: rate.vendor_id, material_id: rate.material_id, limit: 100 },
      }),
  });
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      variant="drawer"
      title={`${rate.vendor_name} — ${rate.material_name}`}
      description="Every change to this vendor's price for this material, newest first. It cannot be edited."
      footer={<Button onClick={onClose}>Close</Button>}
    >
      {history.isLoading ? (
        <p className="text-sm text-fg-muted">Loading…</p>
      ) : history.error ? (
        <p role="alert" className="text-sm text-danger">
          {describeError(history.error)}
        </p>
      ) : (history.data?.items.length ?? 0) === 0 ? (
        <p className="text-sm text-fg-muted">No changes recorded yet.</p>
      ) : (
        <ol className="flex flex-col divide-y divide-border">
          {history.data?.items.map((h) => (
            <li key={h.id} className="py-2.5 text-sm">
              <p>
                <span className="tabular text-fg-muted">
                  {h.old_rate ? formatMoney(trim(h.old_rate)) : "—"}
                </span>{" "}
                → <span className="tabular font-semibold">{formatMoney(trim(h.new_rate))}</span>
                {h.change_pct != null && (
                  <span className="ml-2 text-xs text-fg-muted">
                    ({Number(h.change_pct) > 0 ? "+" : ""}
                    {Number(h.change_pct).toFixed(2)}%)
                  </span>
                )}
              </p>
              <p className="text-xs text-fg-muted">
                From {formatDate(h.effective_from)} · by {h.changed_by_name ?? "—"} ·{" "}
                {formatDateTime(h.changed_at)}
              </p>
              {h.reason && <p className="mt-0.5 text-xs">“{h.reason}”</p>}
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  );
}
