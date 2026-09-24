import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type RowData,
  type VisibilityState,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Columns3,
  Download,
  Rows3,
} from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/menu";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { describeError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { useUiStore, type Density } from "@/lib/ui-store";
import { cn } from "@/lib/utils";

import { downloadCsv, type CsvColumn } from "./csv";
import { PAGE_SIZES } from "./use-list-params";

declare module "@tanstack/react-table" {
  // Type parameters must match the library's declaration to merge.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    /** Right-aligned with tabular figures. */
    numeric?: boolean;
    /** Server field this column sorts by; absent means not sortable. */
    sortKey?: string;
    /** Value written to CSV exports; defaults to the accessor value. */
    csv?: (row: TData) => string | number | boolean | null | undefined;
    /** Cannot be hidden from the column menu (the identifying column). */
    alwaysVisible?: boolean;
  }
}

const densityClass: Record<Density, string> = {
  comfortable: "py-2.5",
  compact: "py-1.5",
  dense: "py-1",
};

export interface DataTableProps<T> {
  /** Persistence key for column visibility, e.g. "vendors". */
  tableId: string;
  caption: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  columns: ColumnDef<T, any>[];
  rows: T[] | undefined;
  total: number | null | undefined;
  isLoading: boolean;
  isFetching?: boolean;
  error?: unknown;
  onRetry?: () => void;
  sort?: string | undefined;
  onSortChange?: (sort: string | undefined) => void;
  offset?: number;
  limit?: number;
  onOffsetChange?: (offset: number) => void;
  onLimitChange?: (limit: number) => void;
  getRowId: (row: T) => string;
  /** Enter or click on a row navigates here. */
  getRowHref?: (row: T) => string;
  empty?: ReactNode;
  toolbar?: ReactNode;
  /** Fetches every row of the current view for CSV export. */
  exportRows?: () => Promise<{ rows: T[]; truncated: boolean }>;
  exportName?: string;
}

function readHidden(tableId: string): VisibilityState {
  try {
    const raw = localStorage.getItem(`krb.table.${tableId}.hidden`);
    return raw ? (JSON.parse(raw) as VisibilityState) : {};
  } catch {
    return {};
  }
}

/**
 * The one list component (docs/08 §3). Server-side sort and pagination, URL
 * state owned by the caller via useListParams, persisted column visibility
 * and density, keyboard row navigation, CSV export of the current view, and a
 * footer that always says how many rows matched.
 */
export function DataTable<T>({
  tableId,
  caption,
  columns,
  rows,
  total,
  isLoading,
  isFetching,
  error,
  onRetry,
  sort,
  onSortChange,
  offset = 0,
  limit = 50,
  onOffsetChange,
  onLimitChange,
  getRowId,
  getRowHref,
  empty,
  toolbar,
  exportRows,
  exportName,
}: DataTableProps<T>) {
  const navigate = useNavigate();
  const density = useUiStore((s) => s.density);
  const setDensity = useUiStore((s) => s.setDensity);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() =>
    readHidden(tableId),
  );
  const [exporting, setExporting] = useState(false);
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(`krb.table.${tableId}.hidden`, JSON.stringify(columnVisibility));
    } catch {
      // Preference only.
    }
  }, [tableId, columnVisibility]);

  const table = useReactTable({
    data: rows ?? [],
    columns,
    getRowId,
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
  });

  const visibleColumns = table.getVisibleLeafColumns();

  const toggleSort = (sortKey: string) => {
    if (!onSortChange) return;
    // asc -> desc -> default
    if (sort === sortKey) onSortChange(`-${sortKey}`);
    else if (sort === `-${sortKey}`) onSortChange(undefined);
    else onSortChange(sortKey);
  };

  const onRowKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, href: string | undefined) => {
    const row = event.currentTarget;
    if (event.key === "Enter" && href) {
      event.preventDefault();
      navigate(href);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      (row.nextElementSibling as HTMLElement | null)?.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      (row.previousElementSibling as HTMLElement | null)?.focus();
    }
  };

  const runExport = async () => {
    if (!exportRows) return;
    setExporting(true);
    try {
      const { rows: all, truncated } = await exportRows();
      const csvColumns: CsvColumn<T>[] = visibleColumns.map((column) => {
        const def = column.columnDef;
        const header = typeof def.header === "string" ? def.header : column.id;
        const accessorKey = "accessorKey" in def ? (def.accessorKey as string) : undefined;
        return {
          header,
          value: (row: T) =>
            def.meta?.csv
              ? def.meta.csv(row)
              : accessorKey
                ? ((row as Record<string, unknown>)[accessorKey] as string | number | null)
                : null,
        };
      });
      downloadCsv(`${exportName ?? tableId}.csv`, all, csvColumns);
      toast.success(
        truncated
          ? `Exported the first ${all.length} rows. Narrow the filters to export the rest.`
          : `Exported ${all.length} rows.`,
      );
    } catch (err) {
      toast.error(`Export failed: ${describeError(err)}`);
    } finally {
      setExporting(false);
    }
  };

  const first = total === 0 || !rows?.length ? 0 : offset + 1;
  const last = offset + (rows?.length ?? 0);
  const hasPrev = offset > 0;
  const hasNext = total != null ? last < total : (rows?.length ?? 0) === limit;

  return (
    <div className="flex min-h-0 flex-col rounded-md border border-border bg-bg">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{toolbar}</div>
        <div className="flex items-center gap-1">
          {exportRows && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void runExport()}
              loading={exporting}
              disabled={!total}
            >
              {!exporting && <Download />} Export
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="ghost" aria-label="Table settings">
                <Columns3 /> View
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuLabel>Columns</DropdownMenuLabel>
              {table
                .getAllLeafColumns()
                .filter((c) => !c.columnDef.meta?.alwaysVisible)
                .map((column) => (
                  <DropdownMenuCheckboxItem
                    key={column.id}
                    checked={column.getIsVisible()}
                    onCheckedChange={(v) => column.toggleVisibility(v)}
                  >
                    {typeof column.columnDef.header === "string"
                      ? column.columnDef.header
                      : column.id}
                  </DropdownMenuCheckboxItem>
                ))}
              <DropdownMenuSeparator />
              <DropdownMenuLabel>
                <span className="inline-flex items-center gap-1">
                  <Rows3 className="size-3" /> Density
                </span>
              </DropdownMenuLabel>
              {(["comfortable", "compact", "dense"] as const).map((d) => (
                <DropdownMenuCheckboxItem
                  key={d}
                  checked={density === d}
                  onCheckedChange={() => setDensity(d)}
                >
                  {d.charAt(0).toUpperCase() + d.slice(1)}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="relative min-h-0 overflow-auto">
        {isFetching && !isLoading && (
          <div
            className="absolute inset-x-0 top-0 z-20 h-0.5 animate-pulse bg-primary"
            aria-hidden
          />
        )}
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 z-10 bg-surface">
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const meta = header.column.columnDef.meta;
                  const sortKey = meta?.sortKey;
                  const direction =
                    sortKey && sort === sortKey
                      ? "ascending"
                      : sortKey && sort === `-${sortKey}`
                        ? "descending"
                        : undefined;
                  const label = flexRender(header.column.columnDef.header, header.getContext());
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={direction ?? (sortKey ? "none" : undefined)}
                      data-numeric={meta?.numeric || undefined}
                      className="whitespace-nowrap border-b border-border px-3 py-2 text-left text-xs font-semibold text-fg-muted"
                    >
                      {sortKey && onSortChange ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(sortKey)}
                          className={cn(
                            "inline-flex items-center gap-1 hover:text-fg",
                            meta?.numeric && "flex-row-reverse",
                          )}
                        >
                          {label}
                          {direction === "ascending" ? (
                            <ArrowUp className="size-3" aria-hidden />
                          ) : direction === "descending" ? (
                            <ArrowDown className="size-3" aria-hidden />
                          ) : (
                            <ArrowUpDown className="size-3 opacity-40" aria-hidden />
                          )}
                        </button>
                      ) : (
                        label
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody ref={bodyRef}>
            {isLoading &&
              Array.from({ length: 8 }, (_, i) => (
                <tr key={`skeleton-${i}`} className="border-b border-border">
                  {visibleColumns.map((column) => (
                    <td key={column.id} className={cn("px-3", densityClass[density])}>
                      <Skeleton className="h-4 w-3/4" />
                    </td>
                  ))}
                </tr>
              ))}
            {!isLoading &&
              !error &&
              table.getRowModel().rows.map((row) => {
                const href = getRowHref?.(row.original);
                return (
                  <tr
                    key={row.id}
                    tabIndex={0}
                    onKeyDown={(e) => onRowKeyDown(e, href)}
                    onClick={(e) => {
                      // Let links and buttons inside the row do their own thing.
                      if (!href || (e.target as HTMLElement).closest("a,button")) return;
                      navigate(href);
                    }}
                    className={cn(
                      "border-b border-border outline-none focus-visible:bg-row-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--focus-ring)]",
                      href && "cursor-pointer hover:bg-row-hover",
                    )}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        data-numeric={cell.column.columnDef.meta?.numeric || undefined}
                        className={cn("px-3 align-middle", densityClass[density])}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                );
              })}
          </tbody>
        </table>
        {!isLoading && Boolean(error) && <ErrorState error={error} onRetry={onRetry} />}
        {!isLoading && !error && rows?.length === 0 && (
          <>{empty ?? <EmptyState title="No records match" />}</>
        )}
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border bg-surface px-3 py-1.5 text-xs text-fg-muted">
        <p aria-live="polite">
          {isLoading
            ? "Loading…"
            : error
              ? "—"
              : total != null
                ? `${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()} records`
                : `${first}–${last}`}
        </p>
        {onOffsetChange && (
          <div className="flex items-center gap-2">
            {onLimitChange && (
              <label className="flex items-center gap-1.5">
                Rows
                <Select
                  className="h-6 w-auto py-0 text-xs"
                  value={limit}
                  onChange={(e) => onLimitChange(Number(e.target.value))}
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </Select>
              </label>
            )}
            <Button
              size="icon"
              variant="ghost"
              className="size-6"
              disabled={!hasPrev}
              onClick={() => onOffsetChange(Math.max(0, offset - limit))}
              aria-label="Previous page"
            >
              <ChevronLeft />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="size-6"
              disabled={!hasNext}
              onClick={() => onOffsetChange(offset + limit)}
              aria-label="Next page"
            >
              <ChevronRight />
            </Button>
          </div>
        )}
      </footer>
    </div>
  );
}
