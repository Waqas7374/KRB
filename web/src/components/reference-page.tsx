import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ColumnDef } from "@tanstack/react-table";
import { Pencil, Plus } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { fetchAllPages } from "@/components/data-table/export";
import { FilterBar, type FilterDef } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { FormAlert } from "@/features/auth/auth-layout";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { codeField, DECIMAL_RE, dirtyValues, emptyToNull, str } from "@/lib/forms";
import { usePagedList, type Option } from "@/lib/queries";
import { toast } from "@/lib/toast";

/**
 * One page shape for small reference tables (departments, cost centres,
 * categories, truck types, warehouses, units): a DataTable plus a drawer
 * form, driven by a field list. Anything with real behaviour (vendors,
 * projects, materials) gets its own screens instead of growing this.
 */

export interface FieldSpec {
  name: string;
  label: string;
  kind?: "text" | "code" | "integer" | "decimal" | "textarea" | "select" | "checkbox";
  options?: Option[];
  /** Label of the empty option in a select; omit to force a choice. */
  emptyLabel?: string;
  required?: boolean;
  hint?: string;
  /** Shown on create only; the API does not allow changing it. */
  createOnly?: boolean;
  wide?: boolean;
}

interface ReferencePageProps<T extends { id: string }> {
  resource: string;
  path: string;
  title: string;
  subtitle?: ReactNode;
  singular: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  columns: ColumnDef<T, any>[];
  fields: FieldSpec[];
  createPermission: string;
  /** Omit when the API has no update endpoint. */
  updatePermission?: string;
  defaultSort?: string;
  filters?: FilterDef[];
  searchable?: boolean;
  /** Rows the edit form starts from. */
  toFormValues?: (row: T) => Record<string, string | boolean>;
}

type FormValues = Record<string, string | boolean>;

function buildSchema(fields: FieldSpec[], editing: boolean) {
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const f of fields) {
    if (editing && f.createOnly) continue;
    let s: z.ZodTypeAny;
    switch (f.kind) {
      case "checkbox":
        s = z.boolean();
        break;
      case "code":
        s = codeField;
        break;
      case "integer":
        s = z
          .string()
          .trim()
          .refine((v) => v === "" || /^\d+$/.test(v), "A whole number");
        break;
      case "decimal":
        s = z
          .string()
          .trim()
          .refine((v) => v === "" || DECIMAL_RE.test(v), "A plain number, e.g. 12.5");
        break;
      default:
        s = z.string();
    }
    if (f.required && f.kind !== "checkbox" && f.kind !== "code") {
      s = (s as z.ZodString).refine((v: string) => v.trim() !== "", "Required");
    }
    shape[f.name] = s;
  }
  return z.object(shape);
}

function toPayload(fields: FieldSpec[], values: FormValues): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    const spec = fields.find((f) => f.name === key);
    if (typeof value === "boolean") out[key] = value;
    else if (spec?.kind === "integer") out[key] = value.trim() === "" ? null : Number(value);
    else out[key] = emptyToNull(value);
  }
  return out;
}

export function ReferencePage<T extends { id: string; version?: number }>({
  resource,
  path,
  title,
  subtitle,
  singular,
  columns,
  fields,
  createPermission,
  updatePermission,
  defaultSort = "code",
  filters,
  searchable = true,
  toFormValues,
}: ReferencePageProps<T>) {
  const list = useListParams({ sort: defaultSort });
  const query = usePagedList<T>(resource, path, list.query);
  const canCreate = useCan(createPermission);
  const canUpdate = useCan(updatePermission);
  const [editing, setEditing] = useState<T | "new" | null>(null);

  const allColumns = useMemo(() => {
    if (!updatePermission || !canUpdate) return columns;
    const edit: ColumnDef<T, unknown> = {
      id: "edit",
      header: "",
      enableHiding: false,
      meta: { alwaysVisible: true, csv: () => null },
      cell: (c) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setEditing(c.row.original)}
          aria-label={`Edit ${singular}`}
        >
          <Pencil />
        </Button>
      ),
    };
    return [...columns, edit];
  }, [columns, updatePermission, canUpdate, singular]);

  return (
    <>
      <PageHeader
        title={title}
        subtitle={subtitle}
        actions={
          canCreate && (
            <Button variant="primary" onClick={() => setEditing("new")}>
              <Plus /> New {singular}
            </Button>
          )
        }
      />
      <PageBody>
        <DataTable
          tableId={resource}
          caption={title}
          columns={allColumns}
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
          exportRows={() => fetchAllPages<T>(path, list.query)}
          toolbar={
            searchable || filters?.length ? (
              <FilterBar list={list} filters={filters} search={searchable} />
            ) : undefined
          }
        />
      </PageBody>
      {editing && (
        <ReferenceForm
          resource={resource}
          path={path}
          singular={singular}
          fields={fields}
          row={editing === "new" ? undefined : editing}
          toFormValues={toFormValues}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

function ReferenceForm<T extends { id: string; version?: number }>({
  resource,
  path,
  singular,
  fields,
  row,
  toFormValues,
  onClose,
}: {
  resource: string;
  path: string;
  singular: string;
  fields: FieldSpec[];
  row?: T;
  toFormValues?: (row: T) => FormValues;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const visible = fields.filter((f) => !(row && f.createOnly));
  const schema = useMemo(() => buildSchema(fields, Boolean(row)), [fields, row]);

  const defaults: FormValues = useMemo(() => {
    const base: FormValues = {};
    const source = row
      ? toFormValues
        ? toFormValues(row)
        : (row as unknown as Record<string, unknown>)
      : {};
    for (const f of visible) {
      const v = source[f.name];
      base[f.name] =
        f.kind === "checkbox"
          ? Boolean(v)
          : typeof v === "string" || typeof v === "number"
            ? str(v)
            : f.kind === "select" && !f.emptyLabel
              ? (f.options?.[0]?.value ?? "")
              : "";
    }
    return base;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row]);

  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: defaults });

  const save = useMutation({
    mutationFn: (values: FormValues) => {
      if (!row) return api.post<T>(path, toPayload(fields, values));
      const changed = dirtyValues(form.formState.dirtyFields, values) as FormValues;
      return api.patch<T>(`${path}/${row.id}`, toPayload(fields, changed), {
        ifMatch: row.version !== undefined ? String(row.version) : undefined,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [resource] });
      toast.success(
        row
          ? `${singular[0]?.toUpperCase()}${singular.slice(1)} updated.`
          : `${singular[0]?.toUpperCase()}${singular.slice(1)} created.`,
      );
      onClose();
    },
    onError: (err) => {
      const rest = applyServerErrors(
        err,
        form.setError,
        visible.map((f) => f.name),
      );
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={row ? `Edit ${singular}` : `New ${singular}`}
      variant="drawer"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => void form.handleSubmit((v) => save.mutate(v))()}
          >
            {row ? "Save" : `Create ${singular}`}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormGrid>
          {visible.map((f, i) => {
            const error = form.formState.errors[f.name]?.message;
            const message = typeof error === "string" ? error : undefined;
            if (f.kind === "checkbox") {
              return (
                <label key={f.name} className="flex items-center gap-2 text-sm sm:col-span-2">
                  <Checkbox {...form.register(f.name)} /> {f.label}
                </label>
              );
            }
            return (
              <FormField
                key={f.name}
                label={f.label}
                required={f.required || f.kind === "code"}
                hint={f.hint}
                error={message}
                className={f.wide || f.kind === "textarea" ? "sm:col-span-2" : undefined}
              >
                {f.kind === "select" ? (
                  <Select {...form.register(f.name)}>
                    {f.emptyLabel !== undefined && <option value="">{f.emptyLabel}</option>}
                    {f.options?.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
                ) : f.kind === "textarea" ? (
                  <Textarea rows={2} {...form.register(f.name)} />
                ) : (
                  <Input
                    {...form.register(f.name)}
                    autoFocus={i === 0}
                    inputMode={
                      f.kind === "decimal"
                        ? "decimal"
                        : f.kind === "integer"
                          ? "numeric"
                          : undefined
                    }
                    className={
                      f.kind === "code"
                        ? "font-mono uppercase"
                        : f.kind === "decimal" || f.kind === "integer"
                          ? "tabular"
                          : undefined
                    }
                  />
                )}
              </FormField>
            );
          })}
        </FormGrid>
      </div>
    </Dialog>
  );
}
