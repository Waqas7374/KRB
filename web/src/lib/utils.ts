import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes, letting later classes win over earlier ones. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

const LOCALE = import.meta.env.VITE_LOCALE ?? "en-PK";
const CURRENCY = import.meta.env.VITE_CURRENCY ?? "PKR";

/**
 * Format an amount that arrived from the API as a decimal string.
 *
 * Amounts cross the wire as strings precisely so they never pass through a
 * JavaScript number; this function is the only place that converts, and it
 * does so for display alone.
 */
export function formatMoney(
  amount: string | number | null | undefined,
  options: { currency?: string; decimals?: number; showCurrency?: boolean } = {},
): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const { currency = CURRENCY, decimals = 2, showCurrency = true } = options;
  const value = typeof amount === "string" ? Number(amount) : amount;
  if (!Number.isFinite(value)) return "—";

  return new Intl.NumberFormat(LOCALE, {
    style: showCurrency ? "currency" : "decimal",
    currency,
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

/** Quantities keep up to 4 decimals but drop trailing zeros for readability. */
export function formatQuantity(
  quantity: string | number | null | undefined,
  unit?: string,
): string {
  if (quantity === null || quantity === undefined || quantity === "") return "—";
  const value = typeof quantity === "string" ? Number(quantity) : quantity;
  if (!Number.isFinite(value)) return "—";

  const formatted = new Intl.NumberFormat(LOCALE, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  }).format(value);
  return unit ? `${formatted} ${unit}` : formatted;
}

export function formatDate(value: string | Date | null | undefined, timeZone?: string): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone,
  }).format(date);
}

export function formatDateTime(value: string | Date | null | undefined, timeZone?: string): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(date);
}
