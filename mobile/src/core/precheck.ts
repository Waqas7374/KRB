import type { SiteRef, TruckTypeRef } from "./reference";

/**
 * Advice while the driver is still standing there (docs/06 §4, docs/05). The server re-checks
 * everything on arrival and is the authority; these only let the app say "this looks overweight"
 * or "you seem to be away from the site" while it can still be put right.
 *
 * They never stop a save. A delivery is always recorded; oddities are flagged, not refused.
 */
export interface Advice {
  code: "OUTSIDE_SITE" | "POOR_SIGNAL" | "NO_POSITION" | "OVERWEIGHT";
  message: string;
}

const EARTH_M = 6_371_000;

/** Great-circle distance in metres. */
export function distanceMeters(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const dLat = rad(b.latitude - a.latitude);
  const dLng = rad(b.longitude - a.longitude);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(a.latitude)) * Math.cos(rad(b.latitude)) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** A position worse than this is "poor signal": the reading cannot be trusted either way. */
export const POOR_ACCURACY_M = 100;

export function locationAdvice(
  site: SiteRef | undefined,
  position: { latitude: number; longitude: number; accuracy: number | null } | null,
): Advice[] {
  if (!position) {
    return [{ code: "NO_POSITION", message: "No position yet. The entry is still saved, and reviewed at head office." }];
  }
  const advice: Advice[] = [];
  if (position.accuracy != null && position.accuracy > POOR_ACCURACY_M) {
    advice.push({
      code: "POOR_SIGNAL",
      message: `Weak GPS signal (±${Math.round(position.accuracy)} m). Try stepping into the open.`,
    });
  }
  if (site?.latitude != null && site.longitude != null) {
    const away = distanceMeters(position, { latitude: site.latitude, longitude: site.longitude });
    // Give the reading the benefit of its own uncertainty: only a clear miss is a miss.
    const allowed = Number(site.geofence_radius_m) + (position.accuracy ?? 0);
    if (away > allowed) {
      advice.push({
        code: "OUTSIDE_SITE",
        message: `This looks like ${Math.round(away)} m from the site (limit ${Math.round(Number(site.geofence_radius_m))} m). Head office will ask about it.`,
      });
    }
  }
  return advice;
}

/** Only for loads counted in tonnes: a volume needs the material's density, which the server knows. */
export function weightAdvice(
  quantityTonnes: number | null,
  truck: TruckTypeRef | undefined,
): Advice[] {
  if (quantityTonnes == null || !truck) return [];
  const limit = Number(truck.default_max_tonnage);
  if (quantityTonnes > limit) {
    return [
      {
        code: "OVERWEIGHT",
        message: `${quantityTonnes} t is over the ${limit} t a ${truck.name} may carry. It will be flagged.`,
      },
    ];
  }
  return [];
}
