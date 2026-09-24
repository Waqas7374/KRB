export interface CsvColumn<T> {
  header: string;
  value: (row: T) => string | number | boolean | null | undefined;
}

/**
 * RFC 4180 quoting, plus a guard against CSV formula injection: a cell that
 * starts with = + - @ is prefixed with a quote so Excel shows it as text
 * instead of executing it. Vendor names and notes are user-entered.
 */
export function toCsvCell(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return "";
  let text = String(value);
  // Amounts arrive as decimal strings; a negative one is data, not a formula.
  const isPlainNumber = /^-?\d+(\.\d+)?$/.test(text);
  if (typeof value === "string" && !isPlainNumber && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv<T>(rows: T[], columns: CsvColumn<T>[]): string {
  const lines = [columns.map((c) => toCsvCell(c.header)).join(",")];
  for (const row of rows) lines.push(columns.map((c) => toCsvCell(c.value(row))).join(","));
  return lines.join("\r\n");
}

export function downloadCsv<T>(fileName: string, rows: T[], columns: CsvColumn<T>[]): void {
  // BOM so Excel opens UTF-8 (Urdu names, the rupee sign) correctly.
  const blob = new Blob(["﻿", toCsv(rows, columns)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}
