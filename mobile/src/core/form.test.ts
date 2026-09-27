import { describe, expect, it } from "vitest";

import { balanceLine, buildDraft, emptyForm, formFrom, plateChips, poCandidates, poFor, topMaterials, withMaterial } from "./form";
import { distanceMeters, locationAdvice, weightAdvice } from "./precheck";
import type { Catalogue, SiteRef } from "./reference";
import type { LocalDelivery } from "./types";
import { validateDraft } from "./validate";

const cat: Catalogue = {
  materials: [
    { id: "crush", data: { sku: "AGG-1", name: "Crush", base_unit_id: "ton", category_id: null, unit_ids: ["ton", "cft"], is_purchasable: true, is_stockable: true } },
    { id: "sand", data: { sku: "SND-1", name: "Sand", base_unit_id: "cft", category_id: null, unit_ids: ["cft"], is_purchasable: true, is_stockable: true } },
  ],
  units: [],
  truckTypes: [],
  vendors: [{ id: "v1", data: { code: "V1", name: "Shree", status: "ACTIVE", phone: null } }],
  sites: [],
  openPos: [
    { id: "po-a", data: { po_number: "PO-A", vendor_id: "v1", project_id: "p", site_id: "s1", status: "APPROVED", expected_delivery_date: null, items: [{ id: "poi-1", material_id: "crush", unit_id: "ton", quantity: "40.0000", received_quantity: "12.5000" }] } },
    { id: "po-b", data: { po_number: "PO-B", vendor_id: "v1", project_id: "p", site_id: null, status: "SENT", expected_delivery_date: null, items: [{ id: "poi-2", material_id: "crush", unit_id: "ton", quantity: "10.0000", received_quantity: "0.0000" }, { id: "poi-3", material_id: "sand", unit_id: "cft", quantity: "500.0000", received_quantity: "0.0000" }] } },
    { id: "po-c", data: { po_number: "PO-C", vendor_id: "other", project_id: "p", site_id: "s1", status: "APPROVED", expected_delivery_date: null, items: [{ id: "poi-4", material_id: "crush", unit_id: "ton", quantity: "5.0000", received_quantity: "0.0000" }] } },
    { id: "po-d", data: { po_number: "PO-D", vendor_id: "v1", project_id: "p", site_id: "s2", status: "APPROVED", expected_delivery_date: null, items: [{ id: "poi-5", material_id: "crush", unit_id: "ton", quantity: "5.0000", received_quantity: "0.0000" }] } },
  ],
};

describe("the New Delivery form", () => {
  it("fills in the unit when a material is chosen", () => {
    const f = withMaterial(emptyForm("s1"), "sand", cat);
    expect(f.unitId).toBe("cft");
    expect(withMaterial(f, "crush", cat).unitId).toBe("ton");
  });

  it("offers only the orders that could cover this load", () => {
    const f = { ...emptyForm("s1"), vendorId: "v1", materialId: "crush" };
    // Right vendor, has the material, for this site or for no site in particular.
    expect(poCandidates(f, cat).map((p) => p.id)).toEqual(["po-a", "po-b"]);
    expect(poCandidates({ ...f, materialId: "sand" }, cat).map((p) => p.id)).toEqual(["po-b"]);
    expect(poCandidates({ ...f, vendorId: "" }, cat)).toEqual([]);
  });

  it("chooses the order for the person only when exactly one fits", () => {
    const f = { ...emptyForm("s1"), vendorId: "v1", materialId: "sand" };
    expect(poFor(f, cat)?.id).toBe("po-b");
    expect(poFor({ ...f, materialId: "crush" }, cat)).toBeNull(); // two fit: they choose
    expect(withMaterial({ ...emptyForm("s1"), vendorId: "v1" }, "sand", cat).poId).toBe("po-b");
  });

  it("shows what is left on an order", () => {
    expect(balanceLine(cat.openPos[0]!.data, "crush")).toBe("40 ordered · 12.5 received · 27.5 left");
    expect(balanceLine(cat.openPos[0]!.data, "sand")).toBeNull();
  });

  it("remembers the plates used at this site, newest first, each once", () => {
    const d = (plate: string, site: string): LocalDelivery => ({
      id: plate + site, payload: { id: "x", site_id: site, vendor_id: "v", captured_at: "", location_source: "GPS", truck_number: plate, items: [] },
      local_status: "SYNCED", server_status: null, server_number: null, open_flags: [], review: null, last_error: null, created_at: "", updated_at: "",
    });
    const list = [d("LEB-1", "s1"), d("LEA-2", "s1"), d("leb-1", "s1"), d("XYZ-9", "s2"), d("LEC-3", "s1")];
    expect(plateChips(list, "s1")).toEqual(["LEB-1", "LEA-2", "LEC-3"]);
    expect(plateChips(list, "s1", 2)).toEqual(["LEB-1", "LEA-2"]);
  });

  it("puts the most used materials first", () => {
    const uses = (m: string): LocalDelivery => ({
      id: m, payload: { id: "x", site_id: "s", vendor_id: "v", captured_at: "", location_source: "GPS", items: [{ material_id: m, unit_id: "u", quantity: "1" }] },
      local_status: "SYNCED", server_status: null, server_number: null, open_flags: [], review: null, last_error: null, created_at: "", updated_at: "",
    });
    expect(topMaterials(cat, [uses("sand"), uses("sand"), uses("crush")], 1).map((m) => m.id)).toEqual(["sand"]);
  });

  it("builds an entry with no price anywhere in it, and one that passes the phone's own checks", () => {
    const form = { ...emptyForm("s1"), materialId: "crush", vendorId: "v1", poId: "po-a", truckNumber: " leb - 1234 ", quantity: "12.5", unitId: "ton", driverName: " Ali " };
    const draft = buildDraft(form, { latitude: 31.411, longitude: 74.2461, accuracy: 8.04, source: "GPS" }, cat);
    expect(draft).toMatchObject({
      site_id: "s1", vendor_id: "v1", purchase_order_id: "po-a", po_item_id: "poi-1",
      truck_number: "LEB-1234", driver_name: "Ali", latitude: "31.4110000", longitude: "74.2461000",
      gps_accuracy_m: "8.0", location_source: "GPS",
    });
    expect(Object.keys(draft).some((k) => /rate|price|amount/i.test(k))).toBe(false);
    expect(validateDraft({ ...draft, id: "i", captured_at: "2026-09-27T08:00:00Z" })).toEqual([]);
  });

  it("saves without a position, marked as entered by hand", () => {
    const draft = buildDraft({ ...emptyForm("s1"), materialId: "crush", vendorId: "v1", quantity: "1", unitId: "ton" }, null, cat);
    expect(draft.location_source).toBe("MANUAL");
    expect(draft.latitude).toBeNull();
  });

  it("puts a saved entry back in the form to be corrected", () => {
    const draft = { ...buildDraft({ ...emptyForm("s1"), materialId: "crush", vendorId: "v1", quantity: "12.5000", unitId: "ton", truckNumber: "A1" }, null, cat), id: "i", captured_at: "" };
    const f = formFrom(draft);
    expect(f).toMatchObject({ siteId: "s1", materialId: "crush", quantity: "12.5", truckNumber: "A1" });
  });
});

describe("advice while the driver is still there", () => {
  const site: SiteRef = { code: "GVH-S1", name: "Block A", project_id: null, latitude: 31.411, longitude: 74.2461, geofence_radius_m: "500.00", timezone: "Asia/Karachi" };

  it("measures distance on the ground", () => {
    expect(distanceMeters({ latitude: 31.411, longitude: 74.2461 }, { latitude: 31.411, longitude: 74.2461 })).toBe(0);
    // 0.009 degrees of latitude is about a kilometre.
    const km = distanceMeters({ latitude: 31.411, longitude: 74.2461 }, { latitude: 31.42, longitude: 74.2461 });
    expect(km).toBeGreaterThan(990);
    expect(km).toBeLessThan(1010);
  });

  it("says nothing when the load is at the site", () => {
    expect(locationAdvice(site, { latitude: 31.4112, longitude: 74.2462, accuracy: 8 })).toEqual([]);
  });

  it("warns, without refusing, when it looks like a kilometre away", () => {
    const advice = locationAdvice(site, { latitude: 31.42, longitude: 74.2461, accuracy: 8 });
    expect(advice.map((a) => a.code)).toEqual(["OUTSIDE_SITE"]);
    expect(advice[0]!.message).toMatch(/limit 500 m/);
  });

  it("gives an uncertain reading the benefit of the doubt", () => {
    // 600 m out with ±200 m of uncertainty could well be inside a 500 m fence.
    const p = { latitude: 31.411 + 0.0054, longitude: 74.2461, accuracy: 200 };
    expect(locationAdvice(site, p).map((a) => a.code)).toEqual(["POOR_SIGNAL"]);
  });

  it("explains the absence of a position", () => {
    expect(locationAdvice(site, null)[0]!.code).toBe("NO_POSITION");
  });

  it("warns about a load over what the truck may carry, and stays quiet otherwise", () => {
    const truck = { code: "10-W", name: "10-Wheeler", default_max_tonnage: "16.000", typical_volume_cft: null };
    expect(weightAdvice(12.5, truck)).toEqual([]);
    expect(weightAdvice(18, truck)[0]!.message).toMatch(/over the 16 t/);
    expect(weightAdvice(null, truck)).toEqual([]);
    expect(weightAdvice(18, undefined)).toEqual([]);
  });
});
