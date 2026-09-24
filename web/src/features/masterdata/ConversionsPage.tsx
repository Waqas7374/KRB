import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calculator, History, Plus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";

import { PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { conversionScopeOptions } from "@/lib/enums";
import { applyServerErrors, describeError } from "@/lib/errors";
import { emptyToNull } from "@/lib/forms";
import { labelFor, useMaterialOptions, useUnitOptions, useVendorOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, humanize } from "@/lib/utils";
import type { ConversionRead, ConversionResolveResponse } from "@/types/models";

/** Factors carry up to 12 decimals; trailing zeros are noise. */
function factor(value: string): string {
  return value.includes(".") ? value.replace(/\.?0+$/, "") : value;
}

const FACTOR_RE = /^\d+(\.\d{1,12})?$/;

export function ConversionsPage() {
  const [params, setParams] = useSearchParams();
  const canManage = useCan("units.manage_conversions");
  const units = useUnitOptions();
  const materials = useMaterialOptions();
  const vendors = useVendorOptions();
  const [creating, setCreating] = useState(false);
  const [history, setHistory] = useState<ConversionRead | null>(null);

  const materialId = params.get("material_id") ?? "";
  const vendorId = params.get("vendor_id") ?? "";
  const currentOnly = params.get("all") !== "1";

  const conversions = useQuery({
    queryKey: ["unit-conversions", "list", { materialId, vendorId, currentOnly }],
    queryFn: () =>
      api.get<ConversionRead[]>("/unit-conversions", {
        query: { material_id: materialId, vendor_id: vendorId, current_only: currentOnly },
      }),
  });

  const setParam = (key: string, value: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  const scopeLabel = (c: ConversionRead) =>
    [
      c.material_id && labelFor(materials.options, c.material_id),
      c.vendor_id && labelFor(vendors.options, c.vendor_id),
    ]
      .filter(Boolean)
      .join(" · ") || "All materials, all vendors";

  return (
    <>
      <PageHeader
        title="Unit conversions"
        subtitle="Effective-dated and append-only: entering a new factor supersedes the old one from its start date; history is never edited."
        actions={
          canManage && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus /> New factor
            </Button>
          )
        }
      />
      <PageBody>
        <ResolveSimulator />
        <Section title="Conversion factors">
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
            <Select
              className="h-7 w-auto max-w-64"
              aria-label="Material"
              value={materialId}
              onChange={(e) => setParam("material_id", e.target.value)}
            >
              <option value="">Material: all</option>
              {materials.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
            <Select
              className="h-7 w-auto max-w-64"
              aria-label="Vendor"
              value={vendorId}
              onChange={(e) => setParam("vendor_id", e.target.value)}
            >
              <option value="">Vendor: all</option>
              {vendors.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={!currentOnly}
                onChange={(e) => setParam("all", e.target.checked ? "1" : "")}
              />
              Include superseded
            </label>
          </div>
          {conversions.isLoading ? (
            <div className="p-4">
              <Skeleton className="h-24 w-full" />
            </div>
          ) : conversions.isError ? (
            <ErrorState error={conversions.error} onRetry={() => void conversions.refetch()} />
          ) : conversions.data?.length === 0 ? (
            <EmptyState title="No conversions match" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Unit conversion factors</caption>
                <thead className="bg-surface text-left text-xs text-fg-muted">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Conversion
                    </th>
                    <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                      Factor
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Applies to
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Effective
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Status
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Basis
                    </th>
                    <th scope="col" className="px-4 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {conversions.data?.map((c) => (
                    <tr key={c.id} className="border-t border-border">
                      <td className="px-4 py-2 font-mono">
                        1 {c.from_unit_code} = {factor(c.factor)} {c.to_unit_code}
                      </td>
                      <td data-numeric className="px-4 py-2 font-mono">
                        {factor(c.factor)}
                      </td>
                      <td className="px-4 py-2">
                        <Tag>{humanize(c.scope_type)}</Tag>{" "}
                        <span className="ml-1 text-fg-muted">{scopeLabel(c)}</span>
                      </td>
                      <td className="px-4 py-2">
                        {formatDate(c.effective_from)} –{" "}
                        {c.effective_to ? formatDate(c.effective_to) : "open"}
                      </td>
                      <td className="px-4 py-2">
                        <StatusBadge
                          status={c.is_current ? "ACTIVE" : "INACTIVE"}
                          tone={c.is_current ? "success" : "neutral"}
                        />
                      </td>
                      <td
                        className="max-w-64 truncate px-4 py-2 text-fg-muted"
                        title={c.basis_note ?? undefined}
                      >
                        {c.basis_note ?? "—"}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <Button size="sm" variant="ghost" onClick={() => setHistory(c)}>
                          <History /> History
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </PageBody>
      {creating && (
        <CreateConversionDialog
          units={units.options}
          materials={materials.options}
          vendors={vendors.options}
          onClose={() => setCreating(false)}
        />
      )}
      {history && <HistoryDialog conversion={history} onClose={() => setHistory(null)} />}
    </>
  );
}

function HistoryDialog({
  conversion,
  onClose,
}: {
  conversion: ConversionRead;
  onClose: () => void;
}) {
  const rows = useQuery({
    queryKey: ["unit-conversions", "history", conversion.id],
    queryFn: () =>
      api.get<ConversionRead[]>("/unit-conversions/history", {
        query: {
          from_unit_id: conversion.from_unit_id,
          to_unit_id: conversion.to_unit_id,
          scope_type: conversion.scope_type,
          material_id: conversion.material_id ?? undefined,
          vendor_id: conversion.vendor_id ?? undefined,
        },
      }),
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      variant="drawer"
      title={`History: ${conversion.from_unit_code} → ${conversion.to_unit_code}`}
      description={`${humanize(conversion.scope_type)} scope. Every factor that has ever applied, newest first.`}
    >
      {rows.isError ? (
        <ErrorState error={rows.error} />
      ) : (
        <ol className="flex flex-col gap-2">
          {rows.data?.map((r) => (
            <li key={r.id} className="rounded-md border border-border p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-mono font-medium">{factor(r.factor)}</span>
                {r.is_current && <Tag>Current</Tag>}
              </div>
              <p className="text-fg-muted">
                {formatDate(r.effective_from)} –{" "}
                {r.effective_to ? formatDate(r.effective_to) : "open"}
              </p>
              {r.basis_note && <p className="mt-1">{r.basis_note}</p>}
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  );
}

const createSchema = z
  .object({
    from_unit_id: z.string().min(1, "Choose a unit"),
    to_unit_id: z.string().min(1, "Choose a unit"),
    factor: z
      .string()
      .trim()
      .refine((v) => FACTOR_RE.test(v) && Number(v) > 0, "A positive number, up to 12 decimals"),
    scope_type: z.string(),
    material_id: z.string(),
    vendor_id: z.string(),
    effective_from: z.string(),
    basis_note: z.string(),
  })
  .refine((v) => v.from_unit_id !== v.to_unit_id, {
    path: ["to_unit_id"],
    message: "Choose a different unit",
  })
  .refine((v) => !["MATERIAL", "MATERIAL_VENDOR"].includes(v.scope_type) || v.material_id, {
    path: ["material_id"],
    message: "Required for this scope",
  })
  .refine((v) => !["VENDOR", "MATERIAL_VENDOR"].includes(v.scope_type) || v.vendor_id, {
    path: ["vendor_id"],
    message: "Required for this scope",
  });
type CreateValues = z.infer<typeof createSchema>;

function CreateConversionDialog({
  units,
  materials,
  vendors,
  onClose,
}: {
  units: { value: string; label: string }[];
  materials: { value: string; label: string }[];
  vendors: { value: string; label: string }[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      from_unit_id: "",
      to_unit_id: "",
      factor: "",
      scope_type: "GLOBAL",
      material_id: "",
      vendor_id: "",
      effective_from: "",
      basis_note: "",
    },
  });
  const scope = form.watch("scope_type");
  const needsMaterial = scope === "MATERIAL" || scope === "MATERIAL_VENDOR";
  const needsVendor = scope === "VENDOR" || scope === "MATERIAL_VENDOR";
  const from = labelFor(units, form.watch("from_unit_id"))?.split(" — ")[0];
  const to = labelFor(units, form.watch("to_unit_id"))?.split(" — ")[0];

  const create = useMutation({
    mutationFn: (v: CreateValues) =>
      api.post("/unit-conversions", {
        ...emptyToNull(v),
        material_id: needsMaterial ? v.material_id : null,
        vendor_id: needsVendor ? v.vendor_id : null,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["unit-conversions"] });
      toast.success(
        "Conversion saved. Any previous factor for this pair and scope now ends the day before.",
      );
      onClose();
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "from_unit_id",
        "to_unit_id",
        "factor",
        "scope_type",
        "material_id",
        "vendor_id",
        "effective_from",
        "basis_note",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      variant="drawer"
      title="New conversion factor"
      description="For truck-load-to-tonnage factors, prefer the Calibration screen so the factor is backed by weighbridge readings."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={create.isPending}
            onClick={() => void form.handleSubmit((v) => create.mutate(v))()}
          >
            Save factor
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormGrid>
          <FormField label="From unit" required error={errors.from_unit_id?.message}>
            <Select {...form.register("from_unit_id")}>
              <option value="">Choose…</option>
              {units.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="To unit" required error={errors.to_unit_id?.message}>
            <Select {...form.register("to_unit_id")}>
              <option value="">Choose…</option>
              {units.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField
            label="Factor"
            required
            error={errors.factor?.message}
            hint={
              from && to ? `1 ${from} = factor × ${to}` : "How many 'to' units make one 'from' unit"
            }
          >
            <Input inputMode="decimal" className="font-mono" {...form.register("factor")} />
          </FormField>
          <FormField label="Effective from" hint="Defaults to today">
            <Input type="date" {...form.register("effective_from")} />
          </FormField>
          <FormField label="Applies to" className="sm:col-span-2">
            <Select {...form.register("scope_type")}>
              {conversionScopeOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          {needsMaterial && (
            <FormField label="Material" required error={errors.material_id?.message}>
              <Select {...form.register("material_id")}>
                <option value="">Choose…</option>
                {materials.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FormField>
          )}
          {needsVendor && (
            <FormField label="Vendor" required error={errors.vendor_id?.message}>
              <Select {...form.register("vendor_id")}>
                <option value="">Choose…</option>
                {vendors.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FormField>
          )}
          <FormField
            label="Basis"
            hint="Where the number comes from — kept with the factor forever"
            className="sm:col-span-2"
          >
            <Textarea rows={2} {...form.register("basis_note")} />
          </FormField>
        </FormGrid>
      </div>
    </Dialog>
  );
}

/**
 * The simulator (§5.7): shows exactly which factor the server would use for
 * a pair, material, vendor and date — including chained and inverted lookups.
 */
function ResolveSimulator() {
  const units = useUnitOptions();
  const materials = useMaterialOptions();
  const vendors = useVendorOptions();
  const [values, setValues] = useState({
    from_unit_id: "",
    to_unit_id: "",
    material_id: "",
    vendor_id: "",
    at: "",
    quantity: "",
  });
  const [result, setResult] = useState<ConversionResolveResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resolve = useMutation({
    mutationFn: () =>
      api.post<ConversionResolveResponse>("/unit-conversions/resolve", emptyToNull(values)),
    onSuccess: (r) => {
      setResult(r);
      setError(null);
    },
    onError: (err) => {
      setResult(null);
      setError(describeError(err));
    },
  });
  const set = (key: keyof typeof values) => (e: { target: { value: string } }) =>
    setValues((v) => ({ ...v, [key]: e.target.value }));
  const code = (id: string) => labelFor(units.options, id)?.split(" — ")[0];

  return (
    <Section title="Resolve a conversion">
      <div className="flex flex-wrap items-end gap-2 p-3">
        <FormField label="Quantity" className="w-28">
          <Input
            inputMode="decimal"
            value={values.quantity}
            onChange={set("quantity")}
            className="tabular"
          />
        </FormField>
        <FormField label="From" className="w-44">
          <Select value={values.from_unit_id} onChange={set("from_unit_id")}>
            <option value="">Choose…</option>
            {units.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="To" className="w-44">
          <Select value={values.to_unit_id} onChange={set("to_unit_id")}>
            <option value="">Choose…</option>
            {units.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Material" className="w-56">
          <Select value={values.material_id} onChange={set("material_id")}>
            <option value="">Any</option>
            {materials.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Vendor" className="w-56">
          <Select value={values.vendor_id} onChange={set("vendor_id")}>
            <option value="">Any</option>
            {vendors.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="On date" className="w-40">
          <Input type="date" value={values.at} onChange={set("at")} />
        </FormField>
        <Button
          onClick={() => resolve.mutate()}
          loading={resolve.isPending}
          disabled={!values.from_unit_id || !values.to_unit_id}
        >
          <Calculator /> Resolve
        </Button>
      </div>
      {(result || error) && (
        <div className="border-t border-border px-4 py-3 text-sm" aria-live="polite">
          {error ? (
            <p className="text-danger">{error}</p>
          ) : result ? (
            <p>
              {result.converted_quantity && (
                <span className="mr-3 font-mono text-base font-semibold">
                  {values.quantity} {code(values.from_unit_id)} ={" "}
                  {factor(result.converted_quantity)} {code(values.to_unit_id)}
                </span>
              )}
              factor <span className="font-mono">{factor(result.factor)}</span> from the{" "}
              <strong>{humanize(result.scope)}</strong> rule
              {result.via_unit_code && <> , chained via {result.via_unit_code}</>}
              {result.inverted && <> (inverted from the reverse pair)</>}.
            </p>
          ) : null}
        </div>
      )}
    </Section>
  );
}
