import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { useCan } from "@/features/auth/use-can";
import { api, type Page } from "@/lib/api";
import type {
  DepartmentRead,
  MaterialCategoryRead,
  MaterialListItem,
  ProjectListItem,
  RoleRead,
  SiteListItem,
  TruckTypeRead,
  UnitRead,
  UserAdminRead,
  VendorListItem,
} from "@/types/models";

type Query = Record<string, string | number | undefined>;

/**
 * A paged list bound to the URL-derived query. `placeholderData` keeps the
 * previous page on screen while the next one loads, so paging does not flash
 * a skeleton (the thin progress bar in DataTable shows the fetch instead).
 */
export function usePagedList<T>(resource: string, path: string, query: Query, enabled = true) {
  return useQuery({
    queryKey: [resource, "list", query],
    queryFn: () => api.get<Page<T>>(path, { query }),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export interface Option {
  value: string;
  label: string;
}

const MASTER_STALE = 5 * 60_000;
/** Pickers load one page of this size; larger sets need the async EntityPicker. */
const LOOKUP_LIMIT = 200;

function useLookup<T>(
  resource: string,
  path: string,
  permission: string,
  toOption: (row: T) => Option,
  query: Query = {},
) {
  const allowed = useCan(permission);
  const result = useQuery({
    queryKey: [resource, "lookup", query],
    queryFn: () => api.get<Page<T>>(path, { query: { limit: LOOKUP_LIMIT, ...query } }),
    staleTime: MASTER_STALE,
    enabled: allowed,
  });
  return {
    options: result.data?.items.map(toOption) ?? [],
    rows: result.data?.items ?? [],
    /** True when more rows exist than the picker shows. */
    truncated: Boolean(result.data?.page.has_more),
    isLoading: result.isLoading,
    allowed,
  };
}

export const useProjectOptions = () =>
  useLookup<ProjectListItem>(
    "projects",
    "/projects",
    "projects.view",
    (p) => ({
      value: p.id,
      label: `${p.code} — ${p.name}`,
    }),
    { sort: "code" },
  );

export const useSiteOptions = (projectId?: string) =>
  useLookup<SiteListItem>(
    "sites",
    "/sites",
    "sites.view",
    (s) => ({ value: s.id, label: `${s.code} — ${s.name}` }),
    { sort: "code", project_id: projectId },
  );

export const useUnitOptions = () =>
  useLookup<UnitRead>(
    "units",
    "/units",
    "units.view",
    (u) => ({
      value: u.id,
      label: `${u.code} — ${u.name}`,
    }),
    { sort: "code" },
  );

export const useCategoryOptions = () =>
  useLookup<MaterialCategoryRead>(
    "material-categories",
    "/material-categories",
    "materials.view",
    (c) => ({ value: c.id, label: `${c.code} — ${c.name}` }),
  );

export const useDepartmentOptions = () =>
  useLookup<DepartmentRead>("departments", "/departments", "departments.view", (d) => ({
    value: d.id,
    label: `${d.code} — ${d.name}`,
  }));

export const useRoleOptions = () =>
  useLookup<RoleRead>("roles", "/roles", "roles.view", (r) => ({
    value: r.id,
    label: r.name,
  }));

export const useUserOptions = () =>
  useLookup<UserAdminRead>(
    "users",
    "/users",
    "users.view",
    (u) => ({
      value: u.id,
      label: u.full_name,
    }),
    { status: "ACTIVE" },
  );

export const useMaterialOptions = () =>
  useLookup<MaterialListItem>(
    "materials",
    "/materials",
    "materials.view",
    (m) => ({
      value: m.id,
      label: `${m.sku} — ${m.name}`,
    }),
    { sort: "name" },
  );

export const useVendorOptions = () =>
  useLookup<VendorListItem>("vendors", "/vendors", "vendors.view", (v) => ({
    value: v.id,
    label: `${v.code} — ${v.display_name}`,
  }));

export const useTruckTypeOptions = () =>
  useLookup<TruckTypeRead>("truck-types", "/truck-types", "materials.view", (t) => ({
    value: t.id,
    label: `${t.code} — ${t.name}`,
  }));

/** Resolve an id to its option label, falling back to a short id. */
export function labelFor(options: Option[], id: string | null | undefined): string | null {
  if (!id) return null;
  return options.find((o) => o.value === id)?.label ?? `${id.slice(0, 8)}…`;
}
