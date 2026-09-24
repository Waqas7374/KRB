import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { ArrowLeftRight, Archive, Pencil, Plus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router-dom";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { fetchAllPages } from "@/components/data-table/export";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { materialTrackingOptions } from "@/lib/enums";
import { applyServerErrors } from "@/lib/errors";
import { codeField, DECIMAL_RE, dirtyValues, emptyToNull, str } from "@/lib/forms";
import { labelFor, useCategoryOptions, usePagedList, useUnitOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDateTime, formatMoney, formatQuantity, humanize } from "@/lib/utils";
import type { MaterialDetail, MaterialListItem, MaterialRead } from "@/types/models";

// --- List ---------------------------------------------------------------------

const col = createColumnHelper<MaterialListItem>();

const columns = [
  col.accessor("sku", {
    header: "SKU",
    meta: { sortKey: "sku", alwaysVisible: true },
    cell: (c) => (
      <Link
        to={`/materials/${c.row.original.id}`}
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
  col.accessor("category_name", { header: "Category", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("base_unit_code", {
    header: "Base unit",
    cell: (c) => <span className="font-mono">{c.getValue() ?? "—"}</span>,
  }),
  col.accessor("standard_rate", {
    header: "Standard rate",
    meta: { numeric: true, sortKey: "standard_rate" },
    cell: (c) => formatMoney(c.getValue()),
  }),
  col.display({
    id: "flags",
    header: "Use",
    meta: {
      csv: (r) =>
        [r.is_stockable && "stock", r.is_purchasable && "purchase"].filter(Boolean).join(" "),
    },
    cell: (c) => (
      <span className="flex gap-1">
        {c.row.original.is_stockable && <Tag>Stocked</Tag>}
        {c.row.original.is_purchasable && <Tag>Purchased</Tag>}
      </span>
    ),
  }),
];

export function MaterialsListPage() {
  const list = useListParams({ sort: "name" });
  const query = usePagedList<MaterialListItem>("materials", "/materials", list.query);
  const categories = useCategoryOptions();
  return (
    <>
      <PageHeader
        title="Materials"
        subtitle="The catalogue used on requests, orders, deliveries and stock."
        actions={
          <PermissionGate permission="materials.create">
            <Button variant="primary" asChild>
              <Link to="/materials/new">
                <Plus /> New material
              </Link>
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="materials"
          caption="Materials"
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
          getRowHref={(r) => `/materials/${r.id}`}
          exportRows={() => fetchAllPages<MaterialListItem>("/materials", list.query)}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="SKU, name, description…  ( / )"
              savedViewsId="materials"
              filters={[{ key: "category_id", label: "Category", options: categories.options }]}
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Form -----------------------------------------------------------------------

const decimal = z
  .string()
  .trim()
  .refine((v) => v === "" || DECIMAL_RE.test(v), "A plain number, e.g. 12.5");

const schema = z
  .object({
    sku: codeField,
    name: z.string().trim().min(2, "At least 2 characters").max(200),
    description: z.string(),
    category_id: z.string().min(1, "Choose a category"),
    base_unit_id: z.string().min(1, "Choose a unit"),
    tracking_type: z.string(),
    min_stock: decimal,
    max_stock: decimal,
    reorder_level: decimal,
    standard_rate: decimal,
    standard_rate_unit_id: z.string(),
    hs_code: z.string().max(20),
    is_stockable: z.boolean(),
    is_purchasable: z.boolean(),
  })
  .refine((v) => !v.min_stock || !v.max_stock || Number(v.max_stock) >= Number(v.min_stock), {
    path: ["max_stock"],
    message: "Less than the minimum",
  });
type Values = z.infer<typeof schema>;

export function MaterialFormPage() {
  const { materialId } = useParams();
  const material = useQuery({
    queryKey: ["materials", "detail", materialId],
    queryFn: () => api.get<MaterialDetail>(`/materials/${materialId}`),
    enabled: Boolean(materialId),
    staleTime: 0,
  });
  if (materialId && material.isLoading) return <PageSkeleton />;
  if (materialId && material.error)
    return <ErrorState error={material.error} onRetry={() => void material.refetch()} />;
  return <MaterialForm material={material.data} />;
}

function MaterialForm({ material }: { material?: MaterialDetail }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const categories = useCategoryOptions();
  const units = useUnitOptions();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      sku: material?.sku ?? "",
      name: material?.name ?? "",
      description: str(material?.description),
      category_id: material?.category_id ?? "",
      base_unit_id: material?.base_unit_id ?? "",
      tracking_type: material?.tracking_type ?? "QUANTITY",
      min_stock: str(material?.min_stock),
      max_stock: str(material?.max_stock),
      reorder_level: str(material?.reorder_level),
      standard_rate: str(material?.standard_rate),
      standard_rate_unit_id: str(material?.standard_rate_unit_id),
      hs_code: str(material?.hs_code),
      is_stockable: material?.is_stockable ?? true,
      is_purchasable: material?.is_purchasable ?? true,
    },
  });
  const errors = form.formState.errors;

  const save = useMutation({
    mutationFn: (v: Values) => {
      if (!material) return api.post<MaterialRead>("/materials", emptyToNull(v));
      const changed = dirtyValues(form.formState.dirtyFields, v);
      // Identity and unit of measure are fixed once stock may exist in them.
      delete changed.sku;
      delete changed.base_unit_id;
      delete changed.tracking_type;
      return api.patch<MaterialRead>(`/materials/${material.id}`, emptyToNull(changed), {
        ifMatch: String(material.version),
      });
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["materials"] });
      toast.success(material ? `Saved ${saved.sku}.` : `Created ${saved.sku}.`);
      navigate(`/materials/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, Object.keys(schema.innerType().shape));
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title={material ? `Edit ${material.name}` : "New material"}
        crumbs={[
          { label: "Materials", to: "/materials" },
          ...(material ? [{ label: material.sku, to: `/materials/${material.id}` }] : []),
          { label: material ? "Edit" : "New" },
        ]}
      />
      <PageBody className="max-w-4xl">
        <form
          onSubmit={(e) =>
            void form.handleSubmit((v) => {
              setFormError(null);
              save.mutate(v);
            })(e)
          }
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}
          <FormSection title="Material">
            <FormGrid>
              <FormField
                label="SKU"
                required
                error={errors.sku?.message}
                hint={material ? "Cannot be changed." : "e.g. AGG-CRUSH-34"}
              >
                <Input
                  {...form.register("sku")}
                  readOnly={Boolean(material)}
                  className="font-mono uppercase read-only:opacity-60"
                  autoFocus={!material}
                />
              </FormField>
              <FormField label="Name" required error={errors.name?.message}>
                <Input {...form.register("name")} />
              </FormField>
              <FormField label="Category" required error={errors.category_id?.message}>
                <Select {...form.register("category_id")}>
                  <option value="">Choose…</option>
                  {categories.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField
                label="Base unit"
                required
                error={errors.base_unit_id?.message}
                hint={
                  material
                    ? "Fixed after creation; stock is kept in this unit."
                    : "Stock is kept in this unit."
                }
              >
                {material ? (
                  <Input
                    readOnly
                    value={labelFor(units.options, material.base_unit_id) ?? ""}
                    className="opacity-60"
                  />
                ) : (
                  <Select {...form.register("base_unit_id")}>
                    <option value="">Choose…</option>
                    {units.options.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
                )}
              </FormField>
              <FormField label="Tracking">
                {material ? (
                  <Input readOnly value={humanize(material.tracking_type)} className="opacity-60" />
                ) : (
                  <Select {...form.register("tracking_type")}>
                    {materialTrackingOptions.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
                )}
              </FormField>
              <FormField label="HS code" error={errors.hs_code?.message}>
                <Input {...form.register("hs_code")} />
              </FormField>
              <FormField label="Description" className="sm:col-span-2">
                <Textarea {...form.register("description")} />
              </FormField>
            </FormGrid>
            <div className="mt-3 flex gap-6">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox {...form.register("is_stockable")} /> Kept in stock
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox {...form.register("is_purchasable")} /> Can be purchased
              </label>
            </div>
          </FormSection>
          <FormSection title="Stock levels (in base unit)">
            <FormGrid className="sm:grid-cols-3">
              <FormField label="Minimum" error={errors.min_stock?.message}>
                <Input inputMode="decimal" className="tabular" {...form.register("min_stock")} />
              </FormField>
              <FormField label="Maximum" error={errors.max_stock?.message}>
                <Input inputMode="decimal" className="tabular" {...form.register("max_stock")} />
              </FormField>
              <FormField label="Reorder at" error={errors.reorder_level?.message}>
                <Input
                  inputMode="decimal"
                  className="tabular"
                  {...form.register("reorder_level")}
                />
              </FormField>
            </FormGrid>
          </FormSection>
          <FormSection title="Costing">
            <FormGrid>
              <FormField
                label="Standard rate (PKR)"
                hint="A planning reference only — deliveries are priced from vendor rates."
                error={errors.standard_rate?.message}
              >
                <Input
                  inputMode="decimal"
                  className="tabular"
                  {...form.register("standard_rate")}
                />
              </FormField>
              <FormField label="Per unit">
                <Select {...form.register("standard_rate_unit_id")}>
                  <option value="">Base unit</option>
                  {units.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
            </FormGrid>
          </FormSection>
          <div className="flex gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {material ? "Save changes" : "Create material"}
            </Button>
            <Button onClick={() => navigate(-1)} disabled={save.isPending}>
              Cancel
            </Button>
          </div>
        </form>
      </PageBody>
    </>
  );
}

// --- Detail ---------------------------------------------------------------------

export function MaterialDetailPage() {
  const { materialId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const canUpdate = useCan("materials.update");
  const canDeactivate = useCan("materials.deactivate");
  const categories = useCategoryOptions();
  const units = useUnitOptions();
  const [dialog, setDialog] = useState<null | "unit" | "deactivate">(null);

  const material = useQuery({
    queryKey: ["materials", "detail", materialId],
    queryFn: () => api.get<MaterialDetail>(`/materials/${materialId}`),
  });
  if (material.isLoading) return <PageSkeleton />;
  if (material.error || !material.data)
    return <ErrorState error={material.error} onRetry={() => void material.refetch()} />;
  const m = material.data;
  const base = labelFor(units.options, m.base_unit_id);
  const unitCode = (id: string | null) => labelFor(units.options, id)?.split(" — ")[0] ?? undefined;

  return (
    <>
      <PageHeader
        title={m.name}
        crumbs={[{ label: "Materials", to: "/materials" }, { label: m.sku }]}
        meta={
          <>
            <span className="font-mono text-sm text-fg-muted">{m.sku}</span>
            <Tag>{humanize(m.tracking_type)}</Tag>
            {m.is_stockable && <Tag>Stocked</Tag>}
            {m.is_purchasable && <Tag>Purchased</Tag>}
          </>
        }
        actions={
          <>
            <Button asChild>
              <Link to={`/unit-conversions?material_id=${m.id}`}>
                <ArrowLeftRight /> Conversions
              </Link>
            </Button>
            {canUpdate && (
              <Button asChild>
                <Link to={`/materials/${m.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {canDeactivate && (
              <Button variant="danger" onClick={() => setDialog("deactivate")}>
                <Archive /> Deactivate
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        <Section title="Details">
          <FieldGrid
            columns={4}
            items={[
              { label: "Category", value: labelFor(categories.options, m.category_id) },
              { label: "Base unit", value: base },
              { label: "HS code", value: m.hs_code, mono: true },
              {
                label: "Standard rate",
                value: m.standard_rate
                  ? `${formatMoney(m.standard_rate)} / ${unitCode(m.standard_rate_unit_id ?? m.base_unit_id) ?? "unit"}`
                  : null,
              },
              {
                label: "Minimum stock",
                value: m.min_stock ? formatQuantity(m.min_stock, unitCode(m.base_unit_id)) : null,
              },
              {
                label: "Maximum stock",
                value: m.max_stock ? formatQuantity(m.max_stock, unitCode(m.base_unit_id)) : null,
              },
              {
                label: "Reorder level",
                value: m.reorder_level
                  ? formatQuantity(m.reorder_level, unitCode(m.base_unit_id))
                  : null,
              },
              { label: "Last updated", value: formatDateTime(m.updated_at) },
              { label: "Description", value: m.description, wide: true },
            ]}
          />
        </Section>
        <Section
          title="Alternate units"
          actions={
            canUpdate && (
              <Button size="sm" variant="ghost" onClick={() => setDialog("unit")}>
                <Plus /> Add
              </Button>
            )
          }
        >
          {m.alternate_units?.length ? (
            <table className="w-full text-sm">
              <caption className="sr-only">Alternate units</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Unit
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Default for
                  </th>
                </tr>
              </thead>
              <tbody>
                {m.alternate_units.map((u) => (
                  <tr key={u.id} className="border-t border-border">
                    <td className="px-4 py-2">{labelFor(units.options, u.unit_id)}</td>
                    <td className="px-4 py-2">
                      <span className="flex gap-1">
                        {u.is_purchase_default && <Tag>Purchasing</Tag>}
                        {u.is_issue_default && <Tag>Issuing</Tag>}
                        {u.is_capture_default && <Tag>Site capture</Tag>}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-4 py-4 text-sm text-fg-muted">
              Only the base unit ({base}) is used for this material.
            </p>
          )}
        </Section>
      </PageBody>
      {dialog === "unit" && (
        <AlternateUnitDialog materialId={m.id} onClose={() => setDialog(null)} />
      )}
      <ConfirmDialog
        open={dialog === "deactivate"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`Deactivate ${m.name}?`}
        description="It disappears from pickers and lists. Existing documents that use it are unaffected."
        confirmLabel={`Deactivate ${m.sku}`}
        destructive
        onConfirm={async () => {
          await api.post(`/materials/${m.id}/deactivate`);
          await queryClient.invalidateQueries({ queryKey: ["materials"] });
          toast.success(`${m.sku} deactivated.`);
          navigate("/materials");
        }}
      />
    </>
  );
}

const altSchema = z.object({
  unit_id: z.string().min(1, "Choose a unit"),
  is_purchase_default: z.boolean(),
  is_issue_default: z.boolean(),
  is_capture_default: z.boolean(),
});
type AltValues = z.infer<typeof altSchema>;

function AlternateUnitDialog({ materialId, onClose }: { materialId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const units = useUnitOptions();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<AltValues>({
    resolver: zodResolver(altSchema),
    defaultValues: {
      unit_id: "",
      is_purchase_default: false,
      is_issue_default: false,
      is_capture_default: false,
    },
  });
  const add = useMutation({
    mutationFn: (v: AltValues) => api.post(`/materials/${materialId}/units`, v),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["materials", "detail", materialId] });
      toast.success("Alternate unit added. Make sure a conversion to the base unit exists.");
      onClose();
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["unit_id"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Add alternate unit"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={add.isPending}
            onClick={() => void form.handleSubmit((v) => add.mutate(v))()}
          >
            Add unit
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormField label="Unit" required error={form.formState.errors.unit_id?.message}>
          <Select {...form.register("unit_id")}>
            <option value="">Choose…</option>
            {units.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </FormField>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox {...form.register("is_purchase_default")} /> Default when purchasing
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox {...form.register("is_issue_default")} /> Default when issuing from store
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox {...form.register("is_capture_default")} /> Default on site delivery capture
        </label>
      </div>
    </Dialog>
  );
}
