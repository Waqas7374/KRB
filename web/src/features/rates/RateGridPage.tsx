import { createColumnHelper } from "@tanstack/react-table";
import { List } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Tag } from "@/components/ui/status-badge";
import { usePagedList, useMaterialOptions, useVendorOptions } from "@/lib/queries";
import { formatDate, formatMoney } from "@/lib/utils";
import type { VendorRateGridRow } from "@/types/models";

/**
 * A line through the rates that have stood, oldest to newest. It shows direction and steadiness,
 * not values: the figures are in the cells beside it and in the text read out to a screen reader.
 */
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  const width = 88;
  const height = 24;
  const pad = 3;
  if (values.length === 0) return <span className="text-fg-subtle">—</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) =>
    values.length === 1 ? width / 2 : pad + (i * (width - 2 * pad)) / (values.length - 1);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - 2 * pad);
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const rising = values.length > 1 && values[values.length - 1]! > values[0]!;
  const falling = values.length > 1 && values[values.length - 1]! < values[0]!;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
      className={rising ? "text-danger" : falling ? "text-success" : "text-fg-muted"}
    >
      {values.length > 1 && (
        <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.5} />
      )}
      <circle
        cx={x(values.length - 1)}
        cy={y(values[values.length - 1]!)}
        r={2.5}
        fill="currentColor"
      />
    </svg>
  );
}

const col = createColumnHelper<VendorRateGridRow>();

export function RateGridPage() {
  const list = useListParams({ sort: "-effective_from" });
  const query = usePagedList<VendorRateGridRow>(
    "vendor-rate-grid",
    "/vendor-rates/grid",
    list.query,
  );
  const vendors = useVendorOptions();
  const materials = useMaterialOptions();

  const columns = useMemo(
    () => [
      col.accessor("material_name", {
        header: "Material",
        meta: { alwaysVisible: true },
        cell: (c) => (
          <span>
            <span className="font-mono text-xs text-fg-muted">{c.row.original.material_sku}</span>{" "}
            <span className="font-medium">{c.getValue()}</span>
          </span>
        ),
      }),
      col.accessor("vendor_name", {
        header: "Vendor",
        cell: (c) => (
          <span>
            <span className="font-mono text-xs text-fg-muted">{c.row.original.vendor_code}</span>{" "}
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
        header: "Last change",
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
      col.display({
        id: "trend",
        header: "History",
        cell: (c) => {
          const values = c.row.original.points.map((p) => Number(p.rate));
          const words = c.row.original.points
            .map(
              (p) => `${formatMoney(p.rate, { decimals: 2 })} from ${formatDate(p.effective_from)}`,
            )
            .join(", then ");
          return (
            <Link
              to={`/vendor-rates?vendor_id=${c.row.original.vendor_id}&material_id=${c.row.original.material_id}`}
              className="inline-flex items-center gap-2 hover:underline"
              title="See every period and why it changed"
            >
              <Sparkline values={values} label={`Rate history: ${words}`} />
              <span className="text-xs text-fg-muted">
                {values.length} period{values.length === 1 ? "" : "s"}
              </span>
            </Link>
          );
        },
      }),
      col.accessor("scope", { header: "Applies to" }),
      col.accessor("effective_from", {
        header: "Since",
        meta: { sortKey: "effective_from" },
        cell: (c) => (
          <span>
            {formatDate(c.getValue())}
            {c.row.original.has_pending && (
              <Tag className="ml-1.5 text-warning">Change pending</Tag>
            )}
          </span>
        ),
      }),
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Rate overview"
        subtitle="What each vendor charges now, for each material, and the road each price took. A pending change is shown but not yet in force."
        actions={
          <Button asChild>
            <Link to="/vendor-rates">
              <List /> Every period
            </Link>
          </Button>
        }
      />
      <PageBody>
        <DataTable
          tableId="vendor-rate-grid"
          caption="Vendor rates in force, with history"
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
          getRowId={(r) => r.rate_id}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Filter by the pickers →"
              savedViewsId="vendor-rate-grid"
              filters={[
                { key: "material_id", label: "Material", options: materials.options },
                { key: "vendor_id", label: "Vendor", options: vendors.options },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}
