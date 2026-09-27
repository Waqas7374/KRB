import type { Catalogue, OpenPoRef, Ref } from "./reference";
import type { DeliveryDraft, LocalDelivery } from "./types";
import { coordinate, normalisePlate } from "./validate";

/**
 * The thinking behind the New Delivery screen (docs/06 §2), kept out of the UI so it can be
 * tested: what the screen offers, what it fills in for the person, and what it sends.
 */

export interface FormState {
  siteId: string;
  materialId: string;
  vendorId: string;
  poId: string;
  truckNumber: string;
  truckTypeId: string;
  quantity: string;
  unitId: string;
  driverName: string;
  driverPhone: string;
  challanNumber: string;
  remarks: string;
}

export const emptyForm = (siteId = ""): FormState => ({
  siteId,
  materialId: "",
  vendorId: "",
  poId: "",
  truckNumber: "",
  truckTypeId: "",
  quantity: "",
  unitId: "",
  driverName: "",
  driverPhone: "",
  challanNumber: "",
  remarks: "",
});

/** Choosing a material sets the unit to its own, which is nearly always what is wanted. */
export function withMaterial(form: FormState, materialId: string, cat: Catalogue): FormState {
  const material = cat.materials.find((m) => m.id === materialId);
  return {
    ...form,
    materialId,
    unitId: material?.data.base_unit_id ?? form.unitId,
    // A different material may mean a different order.
    poId: poFor({ ...form, materialId }, cat)?.id ?? "",
  };
}

/**
 * The open orders that could cover this load: same vendor, has this material, is for this site
 * (or for no site in particular). The order is optional; a load with none is saved and flagged.
 */
export function poCandidates(form: FormState, cat: Catalogue): Ref<OpenPoRef>[] {
  if (!form.vendorId) return [];
  return cat.openPos.filter(
    (po) =>
      po.data.vendor_id === form.vendorId &&
      (po.data.site_id === null || po.data.site_id === form.siteId) &&
      (!form.materialId || po.data.items.some((i) => i.material_id === form.materialId)),
  );
}

/** When exactly one order fits it is chosen for the person; with several they choose. */
export function poFor(form: FormState, cat: Catalogue): Ref<OpenPoRef> | null {
  const found = poCandidates(form, cat);
  return found.length === 1 ? found[0]! : null;
}

/** Ordered against received so far, for the picker: "40 ordered · 12.5 received". */
export function balanceLine(po: OpenPoRef, materialId: string): string | null {
  const item = po.items.find((i) => i.material_id === materialId);
  if (!item) return null;
  const left = Number(item.quantity) - Number(item.received_quantity);
  return `${trim(item.quantity)} ordered · ${trim(item.received_quantity)} received · ${trim(String(left))} left`;
}

/** The last plates used at this site, most recent first: most trucks come back. */
export function plateChips(deliveries: LocalDelivery[], siteId: string, max = 20): string[] {
  const seen = new Set<string>();
  const plates: string[] = [];
  for (const d of deliveries) {
    const plate = d.payload.truck_number ? normalisePlate(d.payload.truck_number) : "";
    if (d.payload.site_id !== siteId || !plate || seen.has(plate)) continue;
    seen.add(plate);
    plates.push(plate);
    if (plates.length >= max) break;
  }
  return plates;
}

export interface Position {
  latitude: number;
  longitude: number;
  accuracy: number | null;
  source: "GPS" | "MANUAL";
}

/** What goes into the outbox. There is nowhere here to put a price. */
export function buildDraft(
  form: FormState,
  position: Position | null,
  cat: Catalogue,
): Omit<DeliveryDraft, "id" | "captured_at"> {
  const po = cat.openPos.find((p) => p.id === form.poId);
  const poItem = po?.data.items.find((i) => i.material_id === form.materialId);
  return {
    site_id: form.siteId,
    vendor_id: form.vendorId,
    purchase_order_id: po?.id ?? null,
    po_item_id: poItem?.id ?? null,
    truck_number: form.truckNumber ? normalisePlate(form.truckNumber) : null,
    truck_type_id: form.truckTypeId || null,
    driver_name: form.driverName.trim() || null,
    driver_phone: form.driverPhone.trim() || null,
    challan_number: form.challanNumber.trim() || null,
    latitude: position ? coordinate(position.latitude) : null,
    longitude: position ? coordinate(position.longitude) : null,
    gps_accuracy_m: position?.accuracy != null ? position.accuracy.toFixed(1) : null,
    location_source: position?.source ?? "MANUAL",
    remarks: form.remarks.trim() || null,
    items: [
      {
        material_id: form.materialId,
        unit_id: form.unitId,
        quantity: form.quantity.trim(),
      },
    ],
  };
}

/** A saved entry back into the form, to correct it or fix a refusal. */
export function formFrom(draft: DeliveryDraft): FormState {
  const line = draft.items[0];
  return {
    siteId: draft.site_id,
    materialId: line?.material_id ?? "",
    vendorId: draft.vendor_id,
    poId: draft.purchase_order_id ?? "",
    truckNumber: draft.truck_number ?? "",
    truckTypeId: draft.truck_type_id ?? "",
    quantity: line?.quantity ? trim(line.quantity) : "",
    unitId: line?.unit_id ?? "",
    driverName: draft.driver_name ?? "",
    driverPhone: draft.driver_phone ?? "",
    challanNumber: draft.challan_number ?? "",
    remarks: draft.remarks ?? "",
  };
}

/** The most-used materials at the top of the form: the "pill row". */
export function topMaterials(cat: Catalogue, deliveries: LocalDelivery[], count = 6): Ref<Catalogue["materials"][number]["data"]>[] {
  const uses = new Map<string, number>();
  for (const d of deliveries) for (const i of d.payload.items) uses.set(i.material_id, (uses.get(i.material_id) ?? 0) + 1);
  return [...cat.materials]
    .sort((a, b) => (uses.get(b.id) ?? 0) - (uses.get(a.id) ?? 0) || a.data.name.localeCompare(b.data.name))
    .slice(0, count);
}

const trim = (v: string) => (v.includes(".") ? v.replace(/\.?0+$/, "") || "0" : v);
