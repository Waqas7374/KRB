import { describe, expect, it } from "vitest";

import { describeValue } from "./rule-format";

describe("describeValue", () => {
  it("reads a tonnage rule as a weight", () => {
    expect(describeValue("TONNAGE_MAX", { max: "16.0000", unit: "TON" })).toBe("16 TON max");
  });

  it("reads a geofence in metres", () => {
    expect(describeValue("GEOFENCE_RADIUS", { radius_m: 500 })).toBe("500 m");
  });

  it("reads a quantity tolerance as the greater of its parts, and omits a zero part", () => {
    expect(describeValue("QTY_TOLERANCE", { pct: "2", abs: "0.5" })).toBe(
      "2% or 0.5 units, whichever is greater",
    );
    expect(describeValue("QTY_TOLERANCE", { pct: "5", abs: "0" })).toBe("5%, whichever is greater");
    expect(describeValue("QTY_TOLERANCE", { pct: "0", abs: "0" })).toBe("none");
  });

  it("formats approval limits as money", () => {
    expect(describeValue("APPROVAL_LIMIT", { limit: "500000", currency: "PKR" })).toMatch(
      /^up to .*500,000$/,
    );
  });

  it("falls back to key/value pairs for a rule type it does not know", () => {
    expect(describeValue("SOMETHING_NEW", { max_things: "3.5000", label: "x" })).toBe(
      "Max things: 3.5, Label: x",
    );
  });
});
