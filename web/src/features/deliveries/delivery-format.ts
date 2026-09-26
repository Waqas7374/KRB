import type { DeliveryRead } from "@/types/models";

/** A fixed-scale decimal from the API without its trailing zeros ("12.5000" → "12.5"). */
export function plainQty(value: string | null | undefined): string {
  if (!value) return "";
  return value.includes(".") ? value.replace(/\.?0+$/, "") || "0" : value;
}

/** How long ago something happened, in the two most useful units. */
export function ageLabel(iso: string, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.floor(hours / 24)} days`;
}

/** Where the truck was, in words a reviewer can act on. */
export function geofenceSummary(
  d: Pick<
    DeliveryRead,
    | "captured_lat"
    | "captured_lng"
    | "gps_accuracy_m"
    | "location_source"
    | "is_inside_geofence"
    | "distance_from_site_m"
  >,
): string {
  if (d.captured_lat == null || d.captured_lng == null) {
    return "No position captured (entered by hand)";
  }
  const accuracy = d.gps_accuracy_m ? ` (±${Math.round(Number(d.gps_accuracy_m))} m)` : "";
  if (d.is_inside_geofence) return `Inside the site${accuracy}`;
  const metres = Math.round(Number(d.distance_from_site_m ?? 0));
  return `${metres} m outside the site fence${accuracy}`;
}

/** The device clock's offset from the server's, when the device reported one. */
export function skewLabel(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  const abs = Math.abs(seconds);
  if (abs < 60) return "In step with the server";
  const minutes = Math.round(abs / 60);
  return `${minutes} min ${seconds > 0 ? "ahead of" : "behind"} the server`;
}
