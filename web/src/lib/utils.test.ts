import { describe, expect, it } from "vitest";

import { cn, formatDate, formatMoney, formatQuantity } from "./utils";

describe("cn", () => {
  it("lets a later class override an earlier conflicting one", () => {
    expect(cn("px-2 py-1", "px-4")).toBe("py-1 px-4");
  });

  it("drops falsy values", () => {
    expect(cn("text-sm", false, undefined, null, "font-medium")).toBe("text-sm font-medium");
  });
});

describe("formatMoney", () => {
  it("formats a decimal string without passing through a float", () => {
    // 23400.4567 as a float would render 23,400.46 too, but the point is that
    // the string arrives intact from the API and is converted only for display.
    expect(formatMoney("23400.4567")).toMatch(/23,400\.46/);
  });

  it("renders an em dash for missing values", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
    expect(formatMoney("")).toBe("—");
  });

  it("renders an em dash rather than NaN for unparseable input", () => {
    expect(formatMoney("not-a-number")).toBe("—");
  });

  it("can omit the currency symbol for dense table columns", () => {
    expect(formatMoney("1500", { showCurrency: false })).toMatch(/1,500\.00/);
  });
});

describe("formatQuantity", () => {
  it("keeps up to four decimals and drops trailing zeros", () => {
    expect(formatQuantity("12.5000")).toBe("12.5");
    expect(formatQuantity("0.0001")).toBe("0.0001");
  });

  it("appends the unit when given", () => {
    expect(formatQuantity("12.5", "t")).toBe("12.5 t");
  });

  it("renders an em dash for missing values", () => {
    expect(formatQuantity(null)).toBe("—");
  });
});

describe("formatDate", () => {
  it("formats an ISO date", () => {
    // The separator is the locale's business (en-PK uses hyphens); assert the
    // parts, so changing VITE_LOCALE does not break the suite.
    expect(formatDate("2026-08-12T06:30:00Z", "UTC")).toMatch(/12\W+Aug\W+2026/);
  });

  it("respects the supplied timezone at a day boundary", () => {
    // 19:30 UTC is already the next day in Asia/Karachi (UTC+5).
    expect(formatDate("2026-08-12T19:30:00Z", "UTC")).toMatch(/12\W+Aug/);
    expect(formatDate("2026-08-12T19:30:00Z", "Asia/Karachi")).toMatch(/13\W+Aug/);
  });

  it("renders an em dash for an invalid date", () => {
    expect(formatDate("not-a-date")).toBe("—");
  });
});
