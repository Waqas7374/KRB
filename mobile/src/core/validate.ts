import type { DeliveryDraft } from "./types";

/**
 * The checks the phone can make on its own, so an obviously incomplete entry is caught at the
 * gate rather than after a round trip. They mirror what the server insists on for a request to
 * be understood at all (docs/05 §3): everything *else* — a heavy load, a wrong place, no order —
 * is not refused on the device or on the server. It is saved and flagged.
 */
export interface Issue {
  field: string;
  message: string;
}

const DECIMAL = /^\d+(\.\d{1,4})?$/;
const COORD = /^-?\d+(\.\d{1,7})?$/;

export function validateDraft(d: DeliveryDraft): Issue[] {
  const issues: Issue[] = [];
  if (!d.site_id) issues.push({ field: "site_id", message: "Choose the site" });
  if (!d.vendor_id) issues.push({ field: "vendor_id", message: "Choose the vendor" });
  if (!d.items.length) issues.push({ field: "items", message: "Add at least one material" });
  d.items.forEach((line, i) => {
    if (!line.material_id) issues.push({ field: `items.${i}.material_id`, message: "Choose the material" });
    if (!line.unit_id) issues.push({ field: `items.${i}.unit_id`, message: "Choose the unit" });
    if (!DECIMAL.test(line.quantity) || Number(line.quantity) <= 0) {
      issues.push({
        field: `items.${i}.quantity`,
        message: "Enter a quantity above zero (up to 4 decimals)",
      });
    }
  });
  const point = d.latitude != null || d.longitude != null;
  if (point) {
    const lat = Number(d.latitude);
    const lng = Number(d.longitude);
    if (!d.latitude || !COORD.test(d.latitude) || Math.abs(lat) > 90) {
      issues.push({ field: "latitude", message: "The position is not valid" });
    }
    if (!d.longitude || !COORD.test(d.longitude) || Math.abs(lng) > 180) {
      issues.push({ field: "longitude", message: "The position is not valid" });
    }
  }
  return issues;
}

/** Plates are typed with a thumb: upper-case, single spaces, nothing else. */
export function normalisePlate(value: string): string {
  return value
    .toUpperCase()
    .replace(/[^A-Z0-9-]+/g, " ")
    .replace(/\s*-\s*/g, "-") // "LEB - 1234" is "LEB-1234"
    .trim()
    .replace(/\s+/g, " ");
}

/** A coordinate as the server wants it: a decimal string with at most 7 places. */
export function coordinate(value: number): string {
  return value.toFixed(7);
}
