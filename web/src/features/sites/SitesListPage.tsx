import { createColumnHelper } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { DataTable } from "@/components/data-table/data-table";
import { fetchAllPages } from "@/components/data-table/export";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Tag } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { siteTypeOptions } from "@/lib/enums";
import { usePagedList, useProjectOptions } from "@/lib/queries";
import { formatDate, formatQuantity, humanize } from "@/lib/utils";
import type { SiteListItem } from "@/types/models";

const col = createColumnHelper<SiteListItem>();

const columns = [
  col.accessor("code", {
    header: "Code",
    meta: { sortKey: "code", alwaysVisible: true },
    cell: (c) => (
      <Link to={`/sites/${c.row.original.id}`} className="font-mono text-primary hover:underline">
        {c.getValue()}
      </Link>
    ),
  }),
  col.accessor("name", {
    header: "Name",
    meta: { sortKey: "name" },
    cell: (c) => <span className="font-medium">{c.getValue()}</span>,
  }),
  col.accessor("project_code", {
    header: "Project",
    cell: (c) => c.getValue() ?? <span className="text-fg-muted">Company-level</span>,
  }),
  col.accessor("site_type", {
    header: "Type",
    meta: { sortKey: "site_type" },
    cell: (c) => humanize(c.getValue()),
  }),
  col.accessor("city", {
    header: "City",
    meta: { sortKey: "city" },
    cell: (c) => c.getValue() ?? "—",
  }),
  col.accessor("geofence_radius_m", {
    header: "Geofence",
    meta: { csv: (r) => (r.has_polygon_geofence ? "polygon" : `${r.geofence_radius_m} m`) },
    cell: (c) =>
      c.row.original.has_polygon_geofence ? (
        <Tag>Polygon</Tag>
      ) : (
        <span className="tabular">{formatQuantity(c.getValue(), "m radius")}</span>
      ),
  }),
  col.accessor("updated_at", {
    header: "Updated",
    meta: { sortKey: "updated_at" },
    cell: (c) => formatDate(c.getValue()),
  }),
];

export function SitesListPage() {
  const list = useListParams({ sort: "code" });
  const query = usePagedList<SiteListItem>("sites", "/sites", list.query);
  const projects = useProjectOptions();

  return (
    <>
      <PageHeader
        title="Sites"
        subtitle="Development sites, stores and offices. The geofence decides which deliveries get flagged."
        actions={
          <PermissionGate permission="sites.create">
            <Button variant="primary" asChild>
              <Link to="/sites/new">
                <Plus /> New site
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="sites"
          caption="Sites"
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
          getRowHref={(r) => `/sites/${r.id}`}
          exportRows={() => fetchAllPages<SiteListItem>("/sites", list.query)}
          toolbar={
            <FilterBar
              list={list}
              savedViewsId="sites"
              filters={[
                { key: "project_id", label: "Project", options: projects.options },
                { key: "site_type", label: "Type", options: siteTypeOptions },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}
