import { createColumnHelper } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { DataTable } from "@/components/data-table/data-table";
import { fetchAllPages } from "@/components/data-table/export";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { vendorStatusOptions, vendorTypeOptions } from "@/lib/enums";
import { usePagedList } from "@/lib/queries";
import { formatDate, humanize } from "@/lib/utils";
import type { VendorListItem } from "@/types/models";

const col = createColumnHelper<VendorListItem>();

const columns = [
  col.accessor("code", {
    header: "Code",
    meta: { sortKey: "code", alwaysVisible: true },
    cell: (c) => (
      <Link to={`/vendors/${c.row.original.id}`} className="font-mono text-primary hover:underline">
        {c.getValue()}
      </Link>
    ),
  }),
  col.accessor("display_name", {
    header: "Name",
    meta: { sortKey: "legal_name" },
    cell: (c) => <span className="font-medium">{c.getValue()}</span>,
  }),
  col.accessor("vendor_type", {
    header: "Type",
    meta: { sortKey: "vendor_type", csv: (r) => r.vendor_type },
    cell: (c) => humanize(c.getValue()),
  }),
  col.accessor("status", {
    header: "Status",
    meta: { sortKey: "status" },
    cell: (c) => <StatusBadge status={c.getValue()} />,
  }),
  col.accessor("city", { header: "City", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("phone", { header: "Phone", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("payment_terms_days", {
    header: "Terms (days)",
    meta: { numeric: true, sortKey: "payment_terms_days" },
  }),
  col.accessor("material_count", {
    header: "Materials",
    meta: { numeric: true },
    cell: (c) => c.getValue() ?? 0,
  }),
  col.accessor("updated_at", {
    header: "Updated",
    meta: { sortKey: "updated_at" },
    cell: (c) => formatDate(c.getValue()),
  }),
];

export function VendorsListPage() {
  const list = useListParams({ sort: "legal_name" });
  const query = usePagedList<VendorListItem>("vendors", "/vendors", list.query);

  return (
    <>
      <PageHeader
        title="Vendors"
        subtitle="Suppliers, contractors and service providers. New vendors start as drafts and must be approved before trading."
        actions={
          <PermissionGate permission="vendors.create">
            <Button variant="primary" asChild>
              <Link to="/vendors/new">
                <Plus /> New vendor
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="vendors"
          caption="Vendors"
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
          getRowHref={(r) => `/vendors/${r.id}`}
          exportRows={() => fetchAllPages<VendorListItem>("/vendors", list.query)}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Code, name, NTN, phone…  ( / )"
              savedViewsId="vendors"
              filters={[
                { key: "status", label: "Status", options: vendorStatusOptions },
                { key: "vendor_type", label: "Type", options: vendorTypeOptions },
              ]}
            />
          }
          empty={
            <EmptyState
              title="No vendors match"
              description="Try a different search, or clear the filters."
            />
          }
        />
      </PageBody>
    </>
  );
}
