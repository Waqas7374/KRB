import { describe, expect, it } from "vitest";

import { codeField, dirtyValues, emptyToNull } from "./forms";
import { formatDateRange, humanize } from "./utils";

describe("emptyToNull", () => {
  it("turns blank strings into null, recursively, and trims the rest", () => {
    expect(
      emptyToNull({ a: "", b: "  ", c: " x ", d: { e: "" }, f: [""], g: false, h: 0 }),
    ).toEqual({ a: null, b: null, c: "x", d: { e: null }, f: [null], g: false, h: 0 });
  });
});

describe("dirtyValues", () => {
  it("keeps only the fields the user changed", () => {
    const values = { name: "New", phone: "0300", email: "same@x" };
    expect(dirtyValues({ name: true, phone: false }, values)).toEqual({ name: "New" });
  });
});

describe("codeField", () => {
  it("upper-cases what the user typed, matching the uppercase display", () => {
    expect(codeField.parse(" gvh-s3 ")).toBe("GVH-S3");
  });

  it("rejects characters the backend Code type rejects", () => {
    expect(codeField.safeParse("GVH S3").success).toBe(false);
    expect(codeField.safeParse("-GVH").success).toBe(false);
    expect(codeField.safeParse("G").success).toBe(false);
  });
});

describe("display helpers", () => {
  it("humanizes enum values", () => {
    expect(humanize("PENDING_APPROVAL")).toBe("Pending approval");
    expect(humanize(null)).toBe("");
  });

  it("formats date ranges with open ends", () => {
    expect(formatDateRange(null, null)).toBe("—");
    expect(formatDateRange("2026-10-01", null)).toMatch(/2026 – …$/);
    expect(formatDateRange(null, "2027-06-30")).toMatch(/^… – /);
  });
});
