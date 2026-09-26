import { describe, expect, it } from "vitest";

import { ageLabel, geofenceSummary, plainQty, skewLabel } from "./delivery-format";

const NOW = new Date("2026-09-25T12:00:00Z");

describe("ageLabel", () => {
  it("uses minutes, hours, then days", () => {
    expect(ageLabel("2026-09-25T11:45:00Z", NOW)).toBe("15 min");
    expect(ageLabel("2026-09-25T07:00:00Z", NOW)).toBe("5 h");
    expect(ageLabel("2026-09-20T12:00:00Z", NOW)).toBe("5 days");
  });

  it("never goes negative for a capture time slightly in the future", () => {
    expect(ageLabel("2026-09-25T12:03:00Z", NOW)).toBe("0 min");
  });
});

describe("geofenceSummary", () => {
  const base = {
    captured_lat: "31.4110000",
    captured_lng: "74.2461000",
    gps_accuracy_m: "8.20",
    location_source: "GPS",
    is_inside_geofence: true,
    distance_from_site_m: "0.00",
  };

  it("says inside, with the accuracy", () => {
    expect(geofenceSummary(base)).toBe("Inside the site (±8 m)");
  });

  it("says how far outside", () => {
    expect(
      geofenceSummary({ ...base, is_inside_geofence: false, distance_from_site_m: "639.6" }),
    ).toBe("640 m outside the site fence (±8 m)");
  });

  it("says so when there was no position", () => {
    expect(geofenceSummary({ ...base, captured_lat: null, captured_lng: null })).toBe(
      "No position captured (entered by hand)",
    );
  });
});

describe("skewLabel", () => {
  it("is quiet when the device reported nothing, and calm when in step", () => {
    expect(skewLabel(null)).toBeNull();
    expect(skewLabel(20)).toBe("In step with the server");
  });

  it("gives the direction", () => {
    expect(skewLabel(7200)).toBe("120 min ahead of the server");
    expect(skewLabel(-600)).toBe("10 min behind the server");
  });
});

describe("plainQty", () => {
  it("drops trailing zeros", () => {
    expect(plainQty("12.5000")).toBe("12.5");
    expect(plainQty("10.0000")).toBe("10");
    expect(plainQty(null)).toBe("");
  });
});
