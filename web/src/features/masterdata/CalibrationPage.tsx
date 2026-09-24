import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";

import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { FormAlert } from "@/features/auth/auth-layout";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import { labelFor, useMaterialOptions, useUnitOptions, useVendorOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn, formatDate, formatQuantity } from "@/lib/utils";
import type { CalibrationReadingRead, CalibrationStatsResponse } from "@/types/models";

const trim = (v: string | null | undefined) =>
  v && v.includes(".") ? v.replace(/\.?0+$/, "") : (v ?? "—");
/** Local calendar date (toISOString would give the UTC date, a day behind in PKT before 05:00). */
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/**
 * Weighbridge calibration (§N-6). The system never averages readings into a
 * live factor by itself: an administrator logs readings, looks at the spread,
 * picks which readings count, and confirms a factor — which then becomes a
 * new effective-dated conversion row.
 */
export function CalibrationPage() {
  const [params, setParams] = useSearchParams();
  const canManage = useCan("units.manage_conversions");
  const materials = useMaterialOptions();
  const units = useUnitOptions();
  const vendors = useVendorOptions();
  const queryClient = useQueryClient();

  const key = {
    material_id: params.get("material_id") ?? "",
    from_unit_id: params.get("from_unit_id") ?? "",
    to_unit_id: params.get("to_unit_id") ?? "",
    vendor_id: params.get("vendor_id") ?? "",
  };
  const ready = Boolean(key.material_id && key.from_unit_id && key.to_unit_id);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dialog, setDialog] = useState<null | "log" | "confirm">(null);
  const [discarding, setDiscarding] = useState<CalibrationReadingRead | null>(null);

  const stats = useQuery({
    queryKey: ["calibration", "stats", key],
    queryFn: () =>
      api.get<CalibrationStatsResponse>("/unit-conversions/calibrations/stats", {
        query: { ...key, vendor_id: key.vendor_id || undefined },
      }),
    enabled: ready,
  });

  // Default selection: every pending reading. The admin unticks outliers.
  useEffect(() => {
    setSelected(new Set(stats.data?.readings.map((r) => r.id) ?? []));
  }, [stats.data]);

  const setParam = (name: string, value: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(name, value);
        else next.delete(name);
        return next;
      },
      { replace: true },
    );

  const code = (id: string) => labelFor(units.options, id)?.split(" — ")[0] ?? "";
  const readings = stats.data?.readings ?? [];
  const chosen = readings.filter((r) => selected.has(r.id));
  const spread = stats.data?.spread_pct ? Number(stats.data.spread_pct) : null;

  return (
    <>
      <PageHeader
        title="Weighbridge calibration"
        subtitle="Derive a truck-load-to-weight factor from real weighbridge readings, reviewed by a person before it takes effect."
      />
      <PageBody>
        <Section title="What is being calibrated">
          <div className="flex flex-wrap items-end gap-2 p-3">
            <FormField label="Material" className="w-64">
              <Select
                value={key.material_id}
                onChange={(e) => setParam("material_id", e.target.value)}
              >
                <option value="">Choose…</option>
                {materials.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField label="Measured in" className="w-44">
              <Select
                value={key.from_unit_id}
                onChange={(e) => setParam("from_unit_id", e.target.value)}
              >
                <option value="">Choose…</option>
                {units.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField label="Weighed in" className="w-44">
              <Select
                value={key.to_unit_id}
                onChange={(e) => setParam("to_unit_id", e.target.value)}
              >
                <option value="">Choose…</option>
                {units.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField label="Vendor (optional)" className="w-56">
              <Select value={key.vendor_id} onChange={(e) => setParam("vendor_id", e.target.value)}>
                <option value="">Any vendor</option>
                {vendors.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FormField>
          </div>
        </Section>

        {!ready ? (
          <EmptyState
            title="Choose a material and the two units"
            description="For example: crush, measured in cubic feet (CFT) on the truck, weighed in tonnes."
          />
        ) : stats.isError ? (
          <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
        ) : (
          <>
            <Section title="Pending readings — summary">
              <FieldGrid
                columns={4}
                items={[
                  { label: "Readings", value: String(stats.data?.count ?? "…") },
                  { label: "Average factor", value: trim(stats.data?.average_factor), mono: true },
                  { label: "Median factor", value: trim(stats.data?.median_factor), mono: true },
                  {
                    label: "Range",
                    value: stats.data?.min_factor
                      ? `${trim(stats.data.min_factor)} – ${trim(stats.data.max_factor)}`
                      : "—",
                    mono: true,
                  },
                  {
                    label: "Spread",
                    value:
                      spread === null ? (
                        "—"
                      ) : (
                        <span
                          className={cn(
                            spread > 10
                              ? "text-danger"
                              : spread > 5
                                ? "text-warning"
                                : "text-success",
                          )}
                        >
                          {formatQuantity(spread)}%
                          {spread > 10 ? " — readings disagree; check for outliers" : ""}
                        </span>
                      ),
                  },
                ]}
              />
            </Section>

            <Section
              title="Readings"
              actions={
                canManage && (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => setDialog("log")}>
                      <Plus /> Log reading
                    </Button>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={chosen.length === 0}
                      onClick={() => setDialog("confirm")}
                    >
                      <CheckCircle2 /> Confirm factor from {chosen.length}
                    </Button>
                  </>
                )
              }
            >
              {readings.length === 0 ? (
                <p className="px-4 py-4 text-sm text-fg-muted">
                  No pending readings for this combination.
                </p>
              ) : (
                <table className="w-full text-sm">
                  <caption className="sr-only">Pending calibration readings</caption>
                  <thead className="bg-surface text-left text-xs text-fg-muted">
                    <tr>
                      <th scope="col" className="w-8 px-4 py-2">
                        <span className="sr-only">Use</span>
                      </th>
                      <th scope="col" className="px-4 py-2 font-semibold">
                        Date
                      </th>
                      <th scope="col" className="px-4 py-2 font-semibold">
                        Truck
                      </th>
                      <th scope="col" className="px-4 py-2 font-semibold">
                        Weighbridge ref
                      </th>
                      <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                        Measured
                      </th>
                      <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                        Weighed
                      </th>
                      <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                        Factor
                      </th>
                      <th scope="col" className="px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {readings.map((r) => (
                      <tr
                        key={r.id}
                        className={cn(
                          "border-t border-border",
                          !selected.has(r.id) && "text-fg-subtle",
                        )}
                      >
                        <td className="px-4 py-2">
                          <Checkbox
                            aria-label={`Use reading of ${formatDate(r.recorded_at)}`}
                            checked={selected.has(r.id)}
                            onChange={(e) =>
                              setSelected((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) next.add(r.id);
                                else next.delete(r.id);
                                return next;
                              })
                            }
                          />
                        </td>
                        <td className="px-4 py-2">{formatDate(r.recorded_at)}</td>
                        <td className="px-4 py-2 font-mono">{r.truck_number ?? "—"}</td>
                        <td className="px-4 py-2">{r.weighbridge_ref ?? "—"}</td>
                        <td data-numeric className="px-4 py-2">
                          {formatQuantity(r.source_quantity, code(r.from_unit_id))}
                        </td>
                        <td data-numeric className="px-4 py-2">
                          {formatQuantity(r.target_quantity, code(r.to_unit_id))}
                        </td>
                        <td data-numeric className="px-4 py-2 font-mono">
                          {trim(r.implied_factor)}
                        </td>
                        <td className="px-4 py-2 text-right">
                          {canManage && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setDiscarding(r)}
                              aria-label="Discard reading"
                            >
                              <Trash2 />
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>
          </>
        )}
      </PageBody>

      {dialog === "log" && (
        <LogReadingDialog
          context={key}
          fromCode={code(key.from_unit_id)}
          toCode={code(key.to_unit_id)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "confirm" && stats.data && (
        <ConfirmFactorDialog
          context={key}
          readings={chosen}
          suggested={stats.data.median_factor ?? stats.data.average_factor ?? ""}
          fromCode={code(key.from_unit_id)}
          toCode={code(key.to_unit_id)}
          onClose={() => setDialog(null)}
        />
      )}
      {discarding && (
        <ConfirmDialog
          open
          onOpenChange={(o) => !o && setDiscarding(null)}
          title="Discard this reading?"
          description="It is kept for the record but no longer counts towards any factor."
          confirmLabel="Discard reading"
          destructive
          reason={{ label: "Reason", required: true, placeholder: "e.g. truck was part-loaded" }}
          onConfirm={async (reason) => {
            await api.post(`/unit-conversions/calibrations/${discarding.id}/discard`, { reason });
            await queryClient.invalidateQueries({ queryKey: ["calibration"] });
            toast.success("Reading discarded.");
          }}
        />
      )}
    </>
  );
}

interface CalibrationContext {
  material_id: string;
  from_unit_id: string;
  to_unit_id: string;
  vendor_id: string;
}

const positive = z
  .string()
  .trim()
  .refine((v) => DECIMAL_RE.test(v) && Number(v) > 0, "A positive number");

const logSchema = z.object({
  source_quantity: positive,
  target_quantity: positive,
  recorded_at: z.string().min(1, "Required"),
  truck_number: z.string(),
  weighbridge_ref: z.string(),
  notes: z.string(),
});
type LogValues = z.infer<typeof logSchema>;

function LogReadingDialog({
  context,
  fromCode,
  toCode,
  onClose,
}: {
  context: CalibrationContext;
  fromCode: string;
  toCode: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<LogValues>({
    resolver: zodResolver(logSchema),
    defaultValues: {
      source_quantity: "",
      target_quantity: "",
      recorded_at: today(),
      truck_number: "",
      weighbridge_ref: "",
      notes: "",
    },
  });
  const save = useMutation({
    mutationFn: (v: LogValues) =>
      api.post("/unit-conversions/calibrations", {
        ...emptyToNull(v),
        ...context,
        vendor_id: context.vendor_id || null,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["calibration"] });
      toast.success("Reading logged. No live factor has changed.");
      form.reset({
        ...form.getValues(),
        source_quantity: "",
        target_quantity: "",
        truck_number: "",
        weighbridge_ref: "",
        notes: "",
      });
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, Object.keys(logSchema.shape));
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;
  const s = form.watch("source_quantity");
  const t = form.watch("target_quantity");
  const implied =
    DECIMAL_RE.test(s) && DECIMAL_RE.test(t) && Number(s) > 0
      ? (Number(t) / Number(s)).toFixed(4)
      : null;

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Log weighbridge reading"
      description="Save several in a row; the form stays open."
      variant="drawer"
      footer={
        <>
          <Button onClick={onClose}>Done</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => void form.handleSubmit((v) => save.mutate(v))()}
          >
            Save reading
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormGrid>
          <FormField
            label={`Measured (${fromCode})`}
            required
            error={errors.source_quantity?.message}
          >
            <Input
              inputMode="decimal"
              className="tabular"
              autoFocus
              {...form.register("source_quantity")}
            />
          </FormField>
          <FormField label={`Weighed (${toCode})`} required error={errors.target_quantity?.message}>
            <Input inputMode="decimal" className="tabular" {...form.register("target_quantity")} />
          </FormField>
          <FormField label="Date" required error={errors.recorded_at?.message}>
            <Input type="date" {...form.register("recorded_at")} />
          </FormField>
          <FormField label="Truck number">
            <Input className="font-mono uppercase" {...form.register("truck_number")} />
          </FormField>
          <FormField label="Weighbridge slip / ref" className="sm:col-span-2">
            <Input {...form.register("weighbridge_ref")} />
          </FormField>
          <FormField label="Notes" className="sm:col-span-2">
            <Textarea rows={2} {...form.register("notes")} />
          </FormField>
        </FormGrid>
        {implied && (
          <p className="text-sm text-fg-muted">
            This reading implies 1 {fromCode} = <span className="font-mono text-fg">{implied}</span>{" "}
            {toCode}.
          </p>
        )}
      </div>
    </Dialog>
  );
}

const confirmSchema = z.object({
  factor: z
    .string()
    .trim()
    .refine((v) => /^\d+(\.\d{1,12})?$/.test(v) && Number(v) > 0, "A positive number"),
  effective_from: z.string().min(1, "Required"),
  basis_note: z.string(),
});
type ConfirmValues = z.infer<typeof confirmSchema>;

function ConfirmFactorDialog({
  context,
  readings,
  suggested,
  fromCode,
  toCode,
  onClose,
}: {
  context: CalibrationContext;
  readings: CalibrationReadingRead[];
  suggested: string;
  fromCode: string;
  toCode: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<ConfirmValues>({
    resolver: zodResolver(confirmSchema),
    defaultValues: {
      factor: trim(suggested),
      effective_from: today(),
      basis_note: `Median of ${readings.length} weighbridge reading${readings.length === 1 ? "" : "s"}`,
    },
  });
  const confirm = useMutation({
    mutationFn: (v: ConfirmValues) =>
      api.post("/unit-conversions/calibrations/confirm", {
        ...emptyToNull(v),
        ...context,
        vendor_id: context.vendor_id || null,
        reading_ids: readings.map((r) => r.id),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["calibration"] });
      await queryClient.invalidateQueries({ queryKey: ["unit-conversions"] });
      toast.success("Factor confirmed and in effect from the chosen date.");
      onClose();
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, Object.keys(confirmSchema.shape));
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Confirm conversion factor"
      description={`Creates a new effective-dated factor for 1 ${fromCode} → ${toCode} and marks these ${readings.length} readings as applied. The previous factor ends the day before.`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={confirm.isPending}
            onClick={() => void form.handleSubmit((v) => confirm.mutate(v))()}
          >
            Confirm factor
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormField
          label={`Factor (1 ${fromCode} = ? ${toCode})`}
          required
          hint="Pre-filled with the median; you decide."
          error={errors.factor?.message}
        >
          <Input inputMode="decimal" className="font-mono" autoFocus {...form.register("factor")} />
        </FormField>
        <FormField label="Effective from" required error={errors.effective_from?.message}>
          <Input type="date" {...form.register("effective_from")} />
        </FormField>
        <FormField label="Basis" hint="Kept with the factor permanently">
          <Textarea rows={2} {...form.register("basis_note")} />
        </FormField>
      </div>
    </Dialog>
  );
}
