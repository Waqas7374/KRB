import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api";
import { DECIMAL_RE } from "@/lib/forms";
import { type Option } from "@/lib/queries";
import type { WarehouseOption } from "@/types/models";

export type WarehouseAction = "issue" | "adjust" | "transfer_from" | "transfer_to";

/** The stores a person may act on. Needs no warehouse-management permission. */
export function useWarehousePicker(action: WarehouseAction) {
  const query = useQuery({
    queryKey: ["warehouse-options", action],
    queryFn: () =>
      api.get<WarehouseOption[]>("/inventory/warehouse-options", { query: { action } }),
    staleTime: 60_000,
  });
  const options: Option[] = (query.data ?? []).map((w) => ({
    value: w.id,
    label: `${w.code} — ${w.name}`,
  }));
  return { options, rows: query.data ?? [], isLoading: query.isLoading };
}

/** Trim trailing zeros from a decimal string for editing. */
export const plain = (v: string | null | undefined) =>
  !v ? "" : v.includes(".") ? v.replace(/\.?0+$/, "") || "0" : v;

export const quantityField = z
  .string()
  .regex(DECIMAL_RE, "A number, up to 4 decimals")
  .refine((v) => Number(v) > 0, "More than zero");

export const lineSchema = z.object({
  material_id: z.string().min(1, "Choose a material"),
  unit_id: z.string().min(1, "Choose a unit"),
  quantity: quantityField,
  remarks: z.string().max(300),
});
export type LineValues = z.infer<typeof lineSchema>;
export const emptyLine: LineValues = { material_id: "", unit_id: "", quantity: "", remarks: "" };
