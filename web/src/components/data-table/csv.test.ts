import { describe, expect, it } from "vitest";

import { toCsv, toCsvCell } from "./csv";

describe("CSV export", () => {
  it("quotes cells with commas, quotes and newlines", () => {
    expect(toCsvCell('Nawaz "Crush", Lahore')).toBe('"Nawaz ""Crush"", Lahore"');
    expect(toCsvCell("line1\nline2")).toBe('"line1\nline2"');
  });

  it("neutralises spreadsheet formulas in user-entered text", () => {
    expect(toCsvCell('=HYPERLINK("http://evil")')).toMatch(/^"?'=/);
    expect(toCsvCell("+92 300 1234567")).toBe("'+92 300 1234567");
    expect(toCsvCell("@SUM(A1)")).toBe("'@SUM(A1)");
  });

  it("leaves negative decimal amounts alone", () => {
    // Amounts arrive as strings; a credit note is data, not a formula.
    expect(toCsvCell("-1500.00")).toBe("-1500.00");
    expect(toCsvCell(-3)).toBe("-3");
  });

  it("renders null as an empty cell and writes a header row", () => {
    const csv = toCsv(
      [{ a: "x", b: null }],
      [
        { header: "A", value: (r) => r.a },
        { header: "B", value: (r) => r.b },
      ],
    );
    expect(csv).toBe("A,B\r\nx,");
  });
});
