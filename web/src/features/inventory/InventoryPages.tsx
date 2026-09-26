import { createColumnHelper } from "@tanstack/react-table";
import { AlertTriangle } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Tag } from "@/components/ui/status-badge";
import { usePagedList, useMaterialOptions, useSiteOptions } from "@/lib/queries";
import { formatDateTime, formatMoney, formatQuantity, humanize } from "@/lib/utils";
import type { StockBalance, StockMovement } from "@/types/models";

// --- Balances --------------------------------------------------------------------

const bal = createColumnHelper<StockBalance>();

export function StockBalancesPage() {
  const list = useListParams({ sort: "-last_movement_at" });
  const query = usePagedList<StockBalance>("inventory", "/inventory/balances", list.query);
  const sites = useSiteOptions();
  const materials = useMaterialOptions();

  const columns = useMemo(
    () => [
      bal.accessor("material_name", {
        header: "Material",
        meta: { alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/inventory/ledger?material_id=${c.row.original.material_id}&warehouse_id=${c.row.original.warehouse_id}`}
            className="hover:underline"
          >
            <span className="font-mono text-xs text-fg-muted">{c.row.original.material_sku}</span>{" "}
            <span className="font-medium text-primary">{c.getValue()}</span>
          </Link>
        ),
      }),
      bal.accessor("warehouse_code", {
        header: "Warehouse",
        cell: (c) =>
          `${c.getValue() ?? ""}${c.row.original.site_code ? ` · ${c.row.original.site_code}` : ""}`,
      }),
      bal.accessor("quantity_on_hand", {
        header: "On hand",
        meta: { numeric: true, sortKey: "quantity_on_hand" },
        cell: (c) => (
          <span className="inline-flex items-center gap-1.5">
            {c.row.original.is_low && (
              <span title={`At or below the reorder level of ${c.row.original.reorder_level}`}>
                <AlertTriangle className="size-3.5 text-warning" aria-label="Low stock" />
              </span>
            )}
            {formatQuantity(c.getValue(), c.row.original.unit_code ?? undefined)}
          </span>
        ),
      }),
      bal.accessor("average_cost", {
        header: "Avg cost",
        meta: { numeric: true },
        cell: (c) =>
          c.getValue() == null ? (
            <span className="text-fg-subtle">Hidden</span>
          ) : (
            formatMoney(c.getValue())
          ),
      }),
      bal.accessor("total_value", {
        header: "Value",
        meta: { numeric: true, sortKey: "total_value" },
        cell: (c) =>
          c.getValue() == null ? (
            <span className="text-fg-subtle">Hidden</span>
          ) : (
            formatMoney(c.getValue(), { decimals: 0 })
          ),
      }),
      bal.accessor("last_movement_at", {
        header: "Last movement",
        meta: { sortKey: "last_movement_at" },
        cell: (c) => (c.getValue() ? formatDateTime(c.getValue()) : "—"),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Stock"
        subtitle="What is on hand at each warehouse, valued at weighted average cost. A cached total of the stock ledger, checked against it every night."
        actions={<Tag>Low stock is marked ⚠</Tag>}
      />
      <PageBody>
        <DataTable
          tableId="stock-balances"
          caption="Stock balances"
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
              savedViewsId="stock-balances"
              filters={[
                { key: "site_id", label: "Site", options: sites.options },
                { key: "material_id", label: "Material", options: materials.options },
                {
                  key: "in_stock",
                  label: "Stock",
                  options: [{ value: "true", label: "In stock only" }],
                },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Ledger ----------------------------------------------------------------------

const led = createColumnHelper<StockMovement>();

const TXN_OPTIONS = [
  "GRN_IN",
  "ISSUE_OUT",
  "TRANSFER_OUT",
  "TRANSFER_IN",
  "ADJUST_IN",
  "ADJUST_OUT",
  "REVERSAL_IN",
  "REVERSAL_OUT",
].map((value) => ({ value, label: humanize(value) }));

export function StockLedgerPage() {
  const list = useListParams({ sort: "-posted_at", limit: 50 });
  const query = usePagedList<StockMovement>("inventory-ledger", "/inventory/ledger", list.query);
  const materials = useMaterialOptions();

  const columns = useMemo(
    () => [
      led.accessor("posted_at", {
        header: "Posted",
        meta: { sortKey: "posted_at", alwaysVisible: true },
        cell: (c) => formatDateTime(c.getValue()),
      }),
      led.accessor("txn_type", {
        header: "Movement",
        cell: (c) => (
          <span className={c.row.original.reversal_of_id ? "text-warning" : undefined}>
            {humanize(c.getValue())}
          </span>
        ),
      }),
      led.accessor("material_name", {
        header: "Material",
        cell: (c) => (
          <span>
            <span className="font-mono text-xs text-fg-muted">{c.row.original.material_sku}</span>{" "}
            {c.getValue()}
          </span>
        ),
      }),
      led.accessor("warehouse_code", { header: "Warehouse" }),
      led.accessor("quantity_in", {
        header: "In",
        meta: { numeric: true },
        cell: (c) => (Number(c.getValue()) ? formatQuantity(c.getValue()) : ""),
      }),
      led.accessor("quantity_out", {
        header: "Out",
        meta: { numeric: true },
        cell: (c) => (Number(c.getValue()) ? formatQuantity(c.getValue()) : ""),
      }),
      led.accessor("balance_quantity_after", {
        header: "Balance",
        meta: { numeric: true },
        cell: (c) => formatQuantity(c.getValue(), c.row.original.unit_code ?? undefined),
      }),
      led.accessor("unit_cost", {
        header: "Unit cost",
        meta: { numeric: true },
        cell: (c) => (c.getValue() == null ? "" : formatMoney(c.getValue())),
      }),
      led.accessor("remarks", {
        header: "Source",
        cell: (c) => c.getValue() ?? c.row.original.source_type,
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Stock ledger"
        subtitle="Every movement, append-only. A mistake is put right with a reversal, never an edit."
        crumbs={[{ label: "Stock", to: "/inventory" }, { label: "Ledger" }]}
      />
      <PageBody>
        <DataTable
          tableId="stock-ledger"
          caption="Stock movements"
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
              savedViewsId="stock-ledger"
              filters={[
                { key: "material_id", label: "Material", options: materials.options },
                { key: "txn_type", label: "Movement", options: TXN_OPTIONS },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}
