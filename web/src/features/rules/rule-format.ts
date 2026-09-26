import { formatMoney, humanize } from "@/lib/utils";

/** A scalar as text; anything else (a rule value is untyped JSON) as empty. */
export function scalar(value: unknown): string {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : "";
}

const trim = (value: unknown): string => {
  const text = scalar(value);
  return text.includes(".") ? text.replace(/\.?0+$/, "") || "0" : text;
};

/**
 * A rule's value in the words an administrator would use. Falls back to
 * "key: value" pairs for a type this function does not know yet, so a new rule
 * type is readable before anyone remembers to teach the screen about it.
 */
export function describeValue(ruleType: string, value: Record<string, unknown>): string {
  switch (ruleType) {
    case "TONNAGE_MAX":
      return `${trim(value.max)} ${scalar(value.unit) || "TON"} max`;
    case "GEOFENCE_RADIUS":
      return `${trim(value.radius_m)} m`;
    case "QTY_TOLERANCE": {
      const parts = [];
      if (Number(value.pct)) parts.push(`${trim(value.pct)}%`);
      if (Number(value.abs)) parts.push(`${trim(value.abs)} units`);
      return parts.length ? `${parts.join(" or ")}, whichever is greater` : "none";
    }
    case "PRICE_TOLERANCE":
      return `${trim(value.pct)}%`;
    case "APPROVAL_LIMIT":
    case "PURCHASE_LIMIT":
      return `up to ${formatMoney(scalar(value.limit), {
        currency: scalar(value.currency) || "PKR",
        decimals: 0,
      })}`;
    case "REORDER_LEVEL":
      return `at or below ${trim(value.min_quantity)}`;
    case "LATE_SUBMISSION":
      return `older than ${trim(value.max_age_days)} days`;
    case "DUPLICATE_WINDOW":
      return `${trim(value.minutes)} minutes`;
    case "DAILY_DELIVERY_CAP": {
      const parts = [];
      if (value.max_deliveries != null) parts.push(`${trim(value.max_deliveries)} deliveries`);
      if (value.max_quantity != null) parts.push(`${trim(value.max_quantity)} quantity`);
      return parts.join(" / ");
    }
    case "CLOCK_SKEW_MAX":
      return `${trim(value.seconds)} s`;
    default:
      return Object.entries(value)
        .map(([k, v]) => `${humanize(k)}: ${trim(v)}`)
        .join(", ");
  }
}

/** Scope keys in the order they read best: what, where, who. */
export const SCOPE_ORDER = [
  "doc_type",
  "role_id",
  "truck_type_id",
  "material_id",
  "material_category_id",
  "vendor_id",
  "project_id",
  "site_id",
];

export const SCOPE_LABELS: Record<string, string> = {
  doc_type: "Document type",
  role_id: "Role",
  truck_type_id: "Truck type",
  material_id: "Material",
  material_category_id: "Material category",
  vendor_id: "Vendor",
  project_id: "Project",
  site_id: "Site",
};
