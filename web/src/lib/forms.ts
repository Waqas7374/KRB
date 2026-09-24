import { z } from "zod";

/**
 * Form <-> API payload helpers.
 *
 * Inputs hold strings; the API wants `null` for "not set". Decimal amounts
 * stay strings end to end (never parsed to a JS number) — the backend accepts
 * `number | string` and parses them as Decimal.
 */

type Json = string | number | boolean | null | undefined | Json[] | { [key: string]: Json };

/** Recursively turn "" (and whitespace-only strings) into null. */
export function emptyToNull<T>(value: T): T {
  if (typeof value === "string") return (value.trim() === "" ? null : value.trim()) as T;
  if (Array.isArray(value)) return value.map((v: unknown) => emptyToNull(v)) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, Json>).map(([k, v]) => [k, emptyToNull(v)]),
    ) as T;
  }
  return value;
}

/**
 * Only the fields the user changed, for PATCH. Sending the whole form would
 * overwrite a concurrent edit to a field this user never touched (and would
 * spam the audit log with no-op diffs). Nested objects (address) are sent
 * whole when any of their fields changed, since the API replaces them whole.
 */
export function dirtyValues<T extends Record<string, unknown>>(
  dirtyFields: Partial<Record<keyof T, unknown>>,
  values: T,
): Partial<T> {
  const out: Partial<T> = {};
  for (const key of Object.keys(dirtyFields) as (keyof T)[]) {
    if (dirtyFields[key]) out[key] = values[key];
  }
  return out;
}

/**
 * Record codes (GVH, VND-0042, ...): the backend's `Code` type is
 * ^[A-Z0-9][A-Z0-9\-_/]*$, 2–30 characters. Inputs show `uppercase` via CSS;
 * this makes the submitted value match what the user sees.
 */
export const codeField = z
  .string()
  .trim()
  .toUpperCase()
  .min(2, "At least 2 characters")
  .max(30, "At most 30 characters")
  .regex(/^[A-Z0-9][A-Z0-9\-_/]*$/, "Letters, digits, - _ / only; start with a letter or digit");

/** A decimal typed by a person: digits, one optional point, no exponent. */
export const DECIMAL_RE = /^\d+(\.\d{1,4})?$/;

/** `null`/`undefined` -> "" for form default values. */
export function str(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}
