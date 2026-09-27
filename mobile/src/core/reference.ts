import type { LocalStore } from "./store";

/**
 * The reference data the phone holds, typed as the server's `GET /sync/pull` sends it
 * (docs/06 §4). All of it is read-only on the device. Nothing here is a price.
 */
export interface MaterialRef {
  sku: string;
  name: string;
  base_unit_id: string;
  category_id: string | null;
  unit_ids: string[];
  is_purchasable: boolean;
  is_stockable: boolean;
}

export interface UnitRef {
  code: string;
  name: string;
  symbol: string | null;
  dimension: string;
  precision: number;
}

export interface TruckTypeRef {
  code: string;
  name: string;
  default_max_tonnage: string;
  typical_volume_cft: string | null;
}

export interface VendorRef {
  code: string;
  name: string;
  status: string;
  phone: string | null;
}

export interface SiteRef {
  code: string;
  name: string;
  project_id: string | null;
  latitude: number | null;
  longitude: number | null;
  geofence_radius_m: string;
  timezone: string;
}

export interface OpenPoRef {
  po_number: string;
  vendor_id: string;
  project_id: string;
  site_id: string | null;
  status: string;
  expected_delivery_date: string | null;
  items: { id: string; material_id: string; unit_id: string; quantity: string; received_quantity: string }[];
}

export interface Ref<T> {
  id: string;
  data: T;
}

/** Everything a capture screen needs, read once from the local database. */
export interface Catalogue {
  materials: Ref<MaterialRef>[];
  units: Ref<UnitRef>[];
  truckTypes: Ref<TruckTypeRef>[];
  vendors: Ref<VendorRef>[];
  sites: Ref<SiteRef>[];
  openPos: Ref<OpenPoRef>[];
}

export async function loadCatalogue(store: LocalStore): Promise<Catalogue> {
  const [materials, units, truckTypes, vendors, sites, openPos] = await Promise.all([
    store.reference<MaterialRef>("materials"),
    store.reference<UnitRef>("units"),
    store.reference<TruckTypeRef>("truck_types"),
    store.reference<VendorRef>("vendors"),
    store.reference<SiteRef>("sites"),
    store.reference<OpenPoRef>("open_pos"),
  ]);
  return {
    materials: materials.filter((m) => m.data.is_stockable).sort(byName),
    units,
    truckTypes,
    vendors: vendors.filter((v) => v.data.status === "ACTIVE").sort(byName),
    sites,
    openPos,
  };
}

const byName = (a: { data: { name: string } }, b: { data: { name: string } }) =>
  a.data.name.localeCompare(b.data.name);
