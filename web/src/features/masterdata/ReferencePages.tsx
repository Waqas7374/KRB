import { createColumnHelper } from "@tanstack/react-table";

import { type FieldSpec, ReferencePage } from "@/components/reference-page";
import { Tag } from "@/components/ui/status-badge";
import { unitDimensionOptions, warehouseTypeOptions } from "@/lib/enums";
import {
  labelFor,
  useCategoryOptions,
  useDepartmentOptions,
  useProjectOptions,
  useSiteOptions,
  useUserOptions,
} from "@/lib/queries";
import { formatQuantity, humanize } from "@/lib/utils";
import type {
  CostCenterRead,
  DepartmentRead,
  MaterialCategoryRead,
  TruckTypeRead,
  UnitRead,
  WarehouseRead,
} from "@/types/models";

// Column arrays are declared as locals before being passed down: inline in
// the JSX prop they would be contextually typed by the prop's
// ColumnDef<T, any>[] and every cell value would degrade to `any`.

const mono = (v: string) => <span className="font-mono">{v}</span>;

// --- Departments --------------------------------------------------------------

const dept = createColumnHelper<DepartmentRead>();

export function DepartmentsPage() {
  const departments = useDepartmentOptions();
  const fields: FieldSpec[] = [
    { name: "code", label: "Code", kind: "code", createOnly: true },
    { name: "name", label: "Name", required: true },
    {
      name: "parent_department_id",
      label: "Parent department",
      kind: "select",
      options: departments.options,
      emptyLabel: "None (top level)",
    },
    { name: "description", label: "Description", kind: "textarea" },
  ];
  const columns = [
    dept.accessor("code", {
      header: "Code",
      meta: { sortKey: "code", alwaysVisible: true },
      cell: (c) => mono(c.getValue()),
    }),
    dept.accessor("name", { header: "Name", meta: { sortKey: "name" } }),
    dept.accessor("parent_department_id", {
      header: "Parent",
      cell: (c) => labelFor(departments.options, c.getValue()) ?? "—",
    }),
    dept.accessor("description", { header: "Description", cell: (c) => c.getValue() ?? "" }),
  ];
  return (
    <ReferencePage<DepartmentRead>
      resource="departments"
      path="/departments"
      title="Departments"
      singular="department"
      createPermission="departments.manage"
      updatePermission="departments.manage"
      fields={fields}
      columns={columns}
    />
  );
}

// --- Cost centres -------------------------------------------------------------

const cc = createColumnHelper<CostCenterRead>();

export function CostCentersPage() {
  const projects = useProjectOptions();
  const departments = useDepartmentOptions();
  const users = useUserOptions();
  const fields: FieldSpec[] = [
    { name: "code", label: "Code", kind: "code", createOnly: true },
    { name: "name", label: "Name", required: true },
    {
      name: "project_id",
      label: "Project",
      kind: "select",
      options: projects.options,
      emptyLabel: "None",
    },
    {
      name: "department_id",
      label: "Department",
      kind: "select",
      options: departments.options,
      emptyLabel: "None",
    },
    ...(users.allowed
      ? [
          {
            name: "owner_user_id",
            label: "Owner",
            kind: "select",
            options: users.options,
            emptyLabel: "None",
          } as FieldSpec,
        ]
      : []),
    { name: "description", label: "Description", kind: "textarea" },
  ];
  const columns = [
    cc.accessor("code", {
      header: "Code",
      meta: { sortKey: "code", alwaysVisible: true },
      cell: (c) => mono(c.getValue()),
    }),
    cc.accessor("name", { header: "Name", meta: { sortKey: "name" } }),
    cc.accessor("project_id", {
      header: "Project",
      cell: (c) => labelFor(projects.options, c.getValue()) ?? "—",
    }),
    cc.accessor("department_id", {
      header: "Department",
      cell: (c) => labelFor(departments.options, c.getValue()) ?? "—",
    }),
  ];
  return (
    <ReferencePage<CostCenterRead>
      resource="cost-centers"
      path="/cost-centers"
      title="Cost centres"
      subtitle="Where spend is charged. Used on purchase orders and journal lines from Phase 2."
      singular="cost centre"
      createPermission="departments.manage"
      updatePermission="departments.manage"
      fields={fields}
      filters={[{ key: "project_id", label: "Project", options: projects.options }]}
      columns={columns}
    />
  );
}

// --- Material categories ------------------------------------------------------

const cat = createColumnHelper<MaterialCategoryRead>();

export function MaterialCategoriesPage() {
  const categories = useCategoryOptions();
  const fields: FieldSpec[] = [
    { name: "code", label: "Code", kind: "code" },
    { name: "name", label: "Name", required: true },
    {
      name: "parent_id",
      label: "Parent category",
      kind: "select",
      options: categories.options,
      emptyLabel: "None (top level)",
    },
    {
      name: "sequence",
      label: "Display order",
      kind: "integer",
      hint: "Lower numbers are listed first",
    },
    { name: "description", label: "Description", kind: "textarea" },
  ];
  const columns = [
    cat.accessor("sequence", { header: "#", meta: { sortKey: "sequence", numeric: true } }),
    cat.accessor("code", {
      header: "Code",
      meta: { sortKey: "code", alwaysVisible: true },
      cell: (c) => mono(c.getValue()),
    }),
    cat.accessor("name", { header: "Name", meta: { sortKey: "name" } }),
    cat.accessor("parent_id", {
      header: "Parent",
      cell: (c) => labelFor(categories.options, c.getValue()) ?? "—",
    }),
    cat.accessor("description", { header: "Description", cell: (c) => c.getValue() ?? "" }),
  ];
  return (
    <ReferencePage<MaterialCategoryRead>
      resource="material-categories"
      path="/material-categories"
      title="Material categories"
      singular="category"
      defaultSort="sequence"
      createPermission="materials.create"
      fields={fields}
      columns={columns}
    />
  );
}

// --- Units ----------------------------------------------------------------------

const unit = createColumnHelper<UnitRead>();

const unitFields: FieldSpec[] = [
  { name: "code", label: "Code", kind: "code", createOnly: true, hint: "e.g. CFT, TON, BAG" },
  { name: "name", label: "Name", required: true },
  { name: "symbol", label: "Symbol" },
  {
    name: "dimension",
    label: "Measures",
    kind: "select",
    options: unitDimensionOptions,
    createOnly: true,
  },
  {
    name: "precision",
    label: "Decimal places",
    kind: "integer",
    hint: "0–6; how quantities in this unit are rounded",
  },
];

const unitColumns = [
  unit.accessor("code", {
    header: "Code",
    meta: { sortKey: "code", alwaysVisible: true },
    cell: (c) => mono(c.getValue()),
  }),
  unit.accessor("name", { header: "Name", meta: { sortKey: "name" } }),
  unit.accessor("symbol", { header: "Symbol", cell: (c) => c.getValue() ?? "—" }),
  unit.accessor("dimension", {
    header: "Measures",
    meta: { sortKey: "dimension" },
    cell: (c) => <Tag>{humanize(c.getValue())}</Tag>,
  }),
  unit.accessor("precision", { header: "Decimals", meta: { numeric: true } }),
];

export function UnitsPage() {
  return (
    <ReferencePage<UnitRead>
      resource="units"
      path="/units"
      title="Units of measure"
      subtitle="Conversions between units are managed on the Conversions screen."
      singular="unit"
      createPermission="units.manage"
      updatePermission="units.manage"
      fields={unitFields}
      columns={unitColumns}
    />
  );
}

// --- Truck types ----------------------------------------------------------------

const truck = createColumnHelper<TruckTypeRead>();

const truckFields: FieldSpec[] = [
  { name: "code", label: "Code", kind: "code" },
  { name: "name", label: "Name", required: true },
  { name: "default_max_tonnage", label: "Max tonnage (t)", kind: "decimal", required: true },
  { name: "axle_count", label: "Axles", kind: "integer" },
  { name: "typical_volume_cft", label: "Typical volume (cft)", kind: "decimal" },
  { name: "description", label: "Description", kind: "textarea" },
];

const truckColumns = [
  truck.accessor("code", {
    header: "Code",
    meta: { sortKey: "code", alwaysVisible: true },
    cell: (c) => mono(c.getValue()),
  }),
  truck.accessor("name", { header: "Name", meta: { sortKey: "name" } }),
  truck.accessor("axle_count", {
    header: "Axles",
    meta: { numeric: true },
    cell: (c) => c.getValue() ?? "—",
  }),
  truck.accessor("default_max_tonnage", {
    header: "Max tonnage",
    meta: { numeric: true, sortKey: "default_max_tonnage" },
    cell: (c) => formatQuantity(c.getValue(), "t"),
  }),
  truck.accessor("typical_volume_cft", {
    header: "Volume",
    meta: { numeric: true },
    cell: (c) => formatQuantity(c.getValue(), "cft"),
  }),
];

export function TruckTypesPage() {
  return (
    <ReferencePage<TruckTypeRead>
      resource="truck-types"
      path="/truck-types"
      title="Truck types"
      subtitle="A truck type's maximum tonnage seeds the tonnage-limit check on deliveries."
      singular="truck type"
      createPermission="settings.manage_rules"
      fields={truckFields}
      columns={truckColumns}
    />
  );
}

// --- Warehouses -----------------------------------------------------------------

const wh = createColumnHelper<WarehouseRead>();

export function WarehousesPage() {
  const sites = useSiteOptions();
  const users = useUserOptions();
  const fields: FieldSpec[] = [
    { name: "code", label: "Code", kind: "code" },
    { name: "name", label: "Name", required: true },
    {
      name: "site_id",
      label: "Site",
      kind: "select",
      options: sites.options,
      emptyLabel: "Choose…",
      required: true,
    },
    { name: "warehouse_type", label: "Type", kind: "select", options: warehouseTypeOptions },
    ...(users.allowed
      ? [
          {
            name: "keeper_user_id",
            label: "Store keeper",
            kind: "select",
            options: users.options,
            emptyLabel: "None",
          } as FieldSpec,
        ]
      : []),
    {
      name: "is_default_receiving",
      label: "Default receiving store for this site",
      kind: "checkbox",
    },
    { name: "capacity_note", label: "Capacity note", kind: "textarea" },
  ];
  const columns = [
    wh.accessor("code", {
      header: "Code",
      meta: { sortKey: "code", alwaysVisible: true },
      cell: (c) => mono(c.getValue()),
    }),
    wh.accessor("name", { header: "Name", meta: { sortKey: "name" } }),
    wh.accessor("site_id", { header: "Site", cell: (c) => labelFor(sites.options, c.getValue()) }),
    wh.accessor("warehouse_type", {
      header: "Type",
      meta: { sortKey: "warehouse_type" },
      cell: (c) => humanize(c.getValue()),
    }),
    wh.accessor("is_default_receiving", {
      header: "Default receiving",
      cell: (c) => (c.getValue() ? "Yes" : ""),
    }),
  ];
  return (
    <ReferencePage<WarehouseRead>
      resource="warehouses"
      path="/warehouses"
      title="Warehouses"
      subtitle="Stock locations. Each belongs to one site."
      singular="warehouse"
      createPermission="warehouses.manage"
      searchable={false}
      filters={[{ key: "site_id", label: "Site", options: sites.options }]}
      fields={fields}
      columns={columns}
    />
  );
}
