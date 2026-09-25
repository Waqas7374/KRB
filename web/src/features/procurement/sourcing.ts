import { humanize } from "@/lib/utils";

/**
 * Display-only pricing for forms, mirroring backend `procurement/domain/pricing.py`
 * (gross, discount off gross, tax on the discounted amount). The server
 * recomputes every figure and is the authority; this only lets a person see a
 * line's effect as they type.
 */
export interface LinePreview {
  gross: number;
  discount: number;
  net: number;
  tax: number;
  total: number;
}

const round4 = (n: number) => Math.round((n + Number.EPSILON) * 10_000) / 10_000;

/** Returns null while any input is not yet a valid number. */
export function previewLine(
  quantity: string,
  rate: string,
  discountPct: string,
  taxPct: string,
): LinePreview | null {
  const q = toNumber(quantity);
  const r = toNumber(rate);
  const d = discountPct.trim() === "" ? 0 : toNumber(discountPct);
  const t = taxPct.trim() === "" ? 0 : toNumber(taxPct);
  if (q === null || r === null || d === null || t === null) return null;
  if (d > 100 || t > 100) return null;
  const gross = round4(q * r);
  const discount = round4((gross * d) / 100);
  const net = gross - discount;
  const tax = round4((net * t) / 100);
  return { gross, discount, net, tax, total: net + tax };
}

function toNumber(value: string): number | null {
  const v = value.trim();
  if (!/^\d+(\.\d+)?$/.test(v)) return null;
  return Number(v);
}

export function sumPreviews(lines: (LinePreview | null)[]): LinePreview {
  return lines.reduce<LinePreview>(
    (acc, l) =>
      l
        ? {
            gross: acc.gross + l.gross,
            discount: acc.discount + l.discount,
            net: acc.net + l.net,
            tax: acc.tax + l.tax,
            total: acc.total + l.total,
          }
        : acc,
    { gross: 0, discount: 0, net: 0, tax: 0, total: 0 },
  );
}

const statusOptions = (values: string[]) => values.map((v) => ({ value: v, label: humanize(v) }));

export const RFQ_STATUS_OPTIONS = statusOptions(["DRAFT", "ISSUED", "CLOSED", "CANCELLED"]);
export const PO_STATUS_OPTIONS = statusOptions([
  "DRAFT",
  "PENDING_APPROVAL",
  "APPROVED",
  "REJECTED",
  "CHANGES_REQUESTED",
  "SENT",
  "ACKNOWLEDGED",
  "PARTIALLY_RECEIVED",
  "RECEIVED",
  "CLOSED",
  "CANCELLED",
]);

/** Trim the trailing zeros the API's fixed-scale decimals carry ("100.0000" → "100"). */
export function plain(value: string | null | undefined): string {
  if (!value) return "";
  return value.includes(".") ? value.replace(/\.?0+$/, "") || "0" : value;
}
