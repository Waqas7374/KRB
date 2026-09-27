import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Input, Select, Textarea } from "@/components/ui/input";
import { FormAlert } from "@/features/auth/auth-layout";
import { useWarehousePicker } from "@/features/stock/stock-helpers";
import { api } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import { useMaterialOptions, useUnitOptions, useVendorOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatMoney } from "@/lib/utils";
import type { GrnRead } from "@/types/models";

const RATE_RE = /^\d+(\.\d{1,6})?$/;

const lineSchema = z.object({
  material_id: z.string().min(1, "Choose a material"),
  unit_id: z.string().min(1, "Choose a unit"),
  quantity: z
    .string()
    .regex(DECIMAL_RE, "A number, up to 4 decimals")
    .refine((v) => Number(v) > 0, "More than zero"),
  rate: z
    .string()
    .regex(RATE_RE, "A number, up to 6 decimals")
    .refine((v) => Number(v) > 0, "More than zero"),
});

const schema = z.object({
  warehouse_id: z.string().min(1, "Choose the store"),
  vendor_id: z.string().min(1, "Choose the vendor"),
  reference: z.string().trim().min(2, "Enter the bill or receipt number").max(60),
  received_date: z.string(),
  remarks: z.string().max(1000),
  lines: z.array(lineSchema).min(1, "Add at least one line"),
});
type Values = z.infer<typeof schema>;

const emptyLine = { material_id: "", unit_id: "", quantity: "", rate: "" };

/**
 * Stock bought over the counter: no delivery, no purchase order. The bill number and a rate
 * per line come from the bill in the person's hand, and the note is drafted like any other
 * GRN; it moves no stock until someone with the right to post it does.
 */
export function CounterPurchasePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const stores = useWarehousePicker("receive");
  const vendors = useVendorOptions();
  const materials = useMaterialOptions();
  const units = useUnitOptions();
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      warehouse_id: "",
      vendor_id: "",
      reference: "",
      received_date: "",
      remarks: "",
      lines: [{ ...emptyLine }],
    },
  });
  const lines = useFieldArray({ control: form.control, name: "lines" });
  const errors = form.formState.errors;
  const watched = form.watch("lines");
  const total = watched.reduce((sum, l) => {
    const q = Number(l.quantity);
    const r = Number(l.rate);
    return sum + (Number.isFinite(q) && Number.isFinite(r) ? q * r : 0);
  }, 0);

  const save = useMutation({
    mutationFn: (values: Values) =>
      api.post<GrnRead>("/grns", {
        ...emptyToNull({
          warehouse_id: values.warehouse_id,
          vendor_id: values.vendor_id,
          reference: values.reference,
          received_date: values.received_date,
          remarks: values.remarks,
        }),
        lines: values.lines,
      }),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      toast.success(
        `${saved.grn_number} drafted. Inspect it, then have it posted to move the stock.`,
      );
      navigate(`/grns/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "warehouse_id",
        "vendor_id",
        "reference",
        "received_date",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title="New counter purchase"
        subtitle="Stock bought over the counter, with no delivery or purchase order. Enter it from the bill."
        crumbs={[{ label: "Goods received", to: "/grns" }, { label: "Counter purchase" }]}
      />
      <PageBody className="max-w-4xl">
        <form
          onSubmit={(e) => {
            setFormError(null);
            void form.handleSubmit((v) => save.mutate(v))(e);
          }}
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}
          <FormSection title="The bill">
            <FormGrid>
              <FormField label="Vendor" required error={errors.vendor_id?.message}>
                <Select {...form.register("vendor_id")}>
                  <option value="">Choose…</option>
                  {vendors.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField
                label="Bill or receipt number"
                required
                error={errors.reference?.message}
                hint="Entered once per vendor: the same bill cannot be received twice."
              >
                <Input {...form.register("reference")} />
              </FormField>
              <FormField label="Received into" required error={errors.warehouse_id?.message}>
                <Select {...form.register("warehouse_id")}>
                  <option value="">Choose…</option>
                  {stores.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Date" hint="Left blank, today.">
                <Input type="date" {...form.register("received_date")} />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="What was bought">
            {errors.lines?.message && (
              <p className="mb-2 text-xs text-danger">{errors.lines.message}</p>
            )}
            <div className="flex flex-col gap-3">
              {lines.fields.map((field, i) => {
                const row = errors.lines?.[i];
                return (
                  <div key={field.id} className="rounded-md border border-border p-3">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_1fr_1fr_auto]">
                      <FormField label={`Line ${i + 1} material`} error={row?.material_id?.message}>
                        <Select
                          {...form.register(`lines.${i}.material_id`, {
                            onChange: (e: { target: { value: string } }) => {
                              const picked = materials.rows.find((m) => m.id === e.target.value);
                              if (picked) form.setValue(`lines.${i}.unit_id`, picked.base_unit_id);
                            },
                          })}
                        >
                          <option value="">Choose…</option>
                          {materials.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Quantity" error={row?.quantity?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`lines.${i}.quantity`)}
                        />
                      </FormField>
                      <FormField label="Unit" error={row?.unit_id?.message}>
                        <Select {...form.register(`lines.${i}.unit_id`)}>
                          <option value="">Choose…</option>
                          {units.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Rate per unit" error={row?.rate?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`lines.${i}.rate`)}
                        />
                      </FormField>
                      <div className="flex items-end pb-0.5">
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Remove line ${i + 1}`}
                          onClick={() => lines.remove(i)}
                          disabled={lines.fields.length === 1}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
              <div className="flex items-center justify-between">
                <Button size="sm" onClick={() => lines.append({ ...emptyLine })}>
                  <Plus /> Add line
                </Button>
                <span className="text-sm text-fg-muted">
                  Bill total{" "}
                  <span className="tabular font-medium text-fg">
                    {formatMoney(total.toFixed(2))}
                  </span>
                </span>
              </div>
            </div>
          </FormSection>

          <FormSection title="Notes">
            <FormField label="Remarks">
              <Textarea rows={2} {...form.register("remarks")} />
            </FormField>
            <p className="mt-2 text-xs text-fg-subtle">
              Attach a photo of the bill on the next screen: a purchase with no delivery is only as
              good as the paper behind it.
            </p>
          </FormSection>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              Save draft
            </Button>
            <Button variant="ghost" onClick={() => navigate(-1)} disabled={save.isPending}>
              Cancel
            </Button>
          </div>
        </form>
      </PageBody>
    </>
  );
}
