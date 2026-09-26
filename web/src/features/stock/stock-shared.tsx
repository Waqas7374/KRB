import { Plus, Trash2 } from "lucide-react";
import { useFieldArray, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { FormField, FormSection } from "@/components/ui/form-field";
import { Input, Select } from "@/components/ui/input";
import { useMaterialOptions, useUnitOptions } from "@/lib/queries";
import { formatMoney, formatQuantity } from "@/lib/utils";
import type { StockLineRead } from "@/types/models";

import { emptyLine, type LineValues } from "./stock-helpers";

interface WithLines {
  lines: LineValues[];
}

/** The material / quantity / unit rows shared by issues and transfers. */
export function LinesSection<T extends WithLines>({
  form,
  error,
}: {
  form: UseFormReturn<T>;
  error?: string | undefined;
}) {
  const typed = form as unknown as UseFormReturn<WithLines>;
  const materials = useMaterialOptions();
  const units = useUnitOptions();
  const lines = useFieldArray({ control: typed.control, name: "lines" });
  const errors = typed.formState.errors.lines;

  return (
    <FormSection title="Material">
      {error && <p className="mb-2 text-xs text-danger">{error}</p>}
      <div className="flex flex-col gap-3">
        {lines.fields.map((field, i) => {
          const row = errors?.[i];
          return (
            <div key={field.id} className="rounded-md border border-border p-3">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_1fr_auto]">
                <FormField label={`Line ${i + 1} material`} error={row?.material_id?.message}>
                  <Select
                    {...typed.register(`lines.${i}.material_id`, {
                      onChange: (e: { target: { value: string } }) => {
                        const picked = materials.rows.find((m) => m.id === e.target.value);
                        if (picked) typed.setValue(`lines.${i}.unit_id`, picked.base_unit_id);
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
                    {...typed.register(`lines.${i}.quantity`)}
                  />
                </FormField>
                <FormField label="Unit" error={row?.unit_id?.message}>
                  <Select {...typed.register(`lines.${i}.unit_id`)}>
                    <option value="">Choose…</option>
                    {units.options.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
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
        <div>
          <Button size="sm" onClick={() => lines.append({ ...emptyLine })}>
            <Plus /> Add line
          </Button>
        </div>
      </div>
      <p className="mt-2 text-xs text-fg-subtle">
        Count in any unit the material has a conversion for; the stock ledger holds its base unit.
      </p>
    </FormSection>
  );
}

/** A read-only table of movement lines. */
export function MovementLinesTable({
  lines,
  hidden,
  caption,
}: {
  lines: StockLineRead[];
  hidden: boolean;
  caption: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface text-left text-xs text-fg-muted">
          <tr>
            <th scope="col" className="px-4 py-2 font-semibold">
              Material
            </th>
            <th scope="col" data-numeric className="px-4 py-2 font-semibold">
              Quantity
            </th>
            <th scope="col" data-numeric className="px-4 py-2 font-semibold">
              In stock units
            </th>
            <th scope="col" data-numeric className="px-4 py-2 font-semibold">
              Cost per unit
            </th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.id} className="border-t border-border">
              <td className="px-4 py-2">
                <span className="font-mono text-xs text-fg-muted">{l.material_sku}</span>{" "}
                <span className="font-medium">{l.material_name}</span>
                {l.remarks && <p className="text-xs text-fg-muted">{l.remarks}</p>}
              </td>
              <td data-numeric className="px-4 py-2">
                {formatQuantity(l.quantity, l.unit_code ?? undefined)}
              </td>
              <td data-numeric className="px-4 py-2">
                {l.base_quantity
                  ? formatQuantity(l.base_quantity, l.base_unit_code ?? undefined)
                  : "—"}
              </td>
              <td data-numeric className="px-4 py-2">
                {hidden ? (
                  <span className="text-fg-subtle">Hidden</span>
                ) : l.unit_cost ? (
                  formatMoney(l.unit_cost, { decimals: 2 })
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
