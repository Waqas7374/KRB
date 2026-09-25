import { describe, expect, it } from "vitest";

import { plain, previewLine, sumPreviews } from "./sourcing";

describe("previewLine", () => {
  it("takes the discount off before tax, as the server does", () => {
    // 100 x 80 = 8000; 10% off = 7200; 17% tax on that = 1224.
    expect(previewLine("100", "80", "10", "17")).toEqual({
      gross: 8000,
      discount: 800,
      net: 7200,
      tax: 1224,
      total: 8424,
    });
  });

  it("treats an empty discount or tax as none", () => {
    expect(previewLine("2.5", "40", "", "")?.total).toBe(100);
  });

  it("is null until every number is valid, rather than showing NaN", () => {
    expect(previewLine("", "40", "", "")).toBeNull();
    expect(previewLine("2", "abc", "", "")).toBeNull();
    expect(previewLine("2", "40", "101", "")).toBeNull();
    expect(previewLine("2", "40", "", "-1")).toBeNull();
  });

  it("keeps four decimals, as the server does", () => {
    expect(previewLine("3", "0.33335", "", "")?.gross).toBe(1.0001);
  });
});

describe("sumPreviews", () => {
  it("adds valid lines and skips unfinished ones", () => {
    const total = sumPreviews([
      previewLine("100", "80", "10", "17"),
      null,
      previewLine("50", "60", "", ""),
    ]);
    expect(total.total).toBe(11424);
    expect(total.discount).toBe(800);
    expect(total.tax).toBe(1224);
  });
});

describe("plain", () => {
  it("drops the trailing zeros of a fixed-scale decimal", () => {
    expect(plain("100.0000")).toBe("100");
    expect(plain("12.5000")).toBe("12.5");
    expect(plain("100")).toBe("100");
    expect(plain(null)).toBe("");
  });
});
