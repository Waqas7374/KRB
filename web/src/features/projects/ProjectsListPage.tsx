import { createColumnHelper } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { DataTable } from "@/components/data-table/data-table";
import { fetchAllPages } from "@/components/data-table/export";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { projectStatusOptions, projectTypeOptions } from "@/lib/enums";
import { usePagedList } from "@/lib/queries";
import { formatDate, formatMoney, humanize } from "@/lib/utils";
import type { ProjectListItem } from "@/types/models";

const col = createColumnHelper<ProjectListItem>();

const columns = [
  col.accessor("code", {
    header: "Code",
    meta: { sortKey: "code", alwaysVisible: true },
    cell: (c) => (
      <Link
        to={`/projects/${c.row.original.id}`}
        className="font-mono text-primary hover:underline"
      >
        {c.getValue()}
      </Link>
    ),
  }),
  col.accessor("name", {
    header: "Name",
    meta: { sortKey: "name" },
    cell: (c) => <span className="font-medium">{c.getValue()}</span>,
  }),
  col.accessor("project_type", {
    header: "Type",
    meta: { sortKey: "project_type" },
    cell: (c) => humanize(c.getValue()),
  }),
  col.accessor("status", {
    header: "Status",
    meta: { sortKey: "status" },
    cell: (c) => <StatusBadge status={c.getValue()} />,
  }),
  col.accessor("location_name", { header: "Location", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("site_count", {
    header: "Sites",
    meta: { numeric: true },
    cell: (c) => c.getValue() ?? 0,
  }),
  col.accessor("total_budget", {
    header: "Budget",
    meta: { numeric: true },
    cell: (c) => formatMoney(c.getValue(), { currency: c.row.original.currency_code, decimals: 0 }),
  }),
  col.accessor("start_date", {
    header: "Start",
    meta: { sortKey: "start_date" },
    cell: (c) => formatDate(c.getValue()),
  }),
  col.accessor("end_date", {
    header: "End",
    meta: { sortKey: "end_date" },
    cell: (c) => formatDate(c.getValue()),
  }),
];

export function ProjectsListPage() {
  const list = useListParams({ sort: "code" });
  const query = usePagedList<ProjectListItem>("projects", "/projects", list.query);

  return (
    <>
      <PageHeader
        title="Projects"
        subtitle="Only projects inside your access scope are listed."
        actions={
          <PermissionGate permission="projects.create">
            <Button variant="primary" asChild>
              <Link to="/projects/new">
                <Plus /> New project
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="projects"
          caption="Projects"
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
          getRowHref={(r) => `/projects/${r.id}`}
          exportRows={() => fetchAllPages<ProjectListItem>("/projects", list.query)}
          toolbar={
            <FilterBar
              list={list}
              savedViewsId="projects"
              filters={[
                { key: "status", label: "Status", options: projectStatusOptions },
                { key: "project_type", label: "Type", options: projectTypeOptions },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}
