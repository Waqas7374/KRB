import { api, type Page } from "@/lib/api";

/** Hard cap on client-side export; larger sets belong to the async report jobs of Phase 6. */
export const EXPORT_CAP = 5_000;

/**
 * Fetches every row of the current filtered view, 200 at a time, up to
 * EXPORT_CAP. Pagination params are overridden; filters and sort are kept.
 */
export async function fetchAllPages<T>(
  path: string,
  query: Record<string, string | number | undefined>,
): Promise<{ rows: T[]; truncated: boolean }> {
  const rows: T[] = [];
  let offset = 0;
  for (;;) {
    const page = await api.get<Page<T>>(path, { query: { ...query, offset, limit: 200 } });
    rows.push(...page.items);
    offset += page.items.length;
    if (!page.page.has_more || page.items.length === 0) return { rows, truncated: false };
    if (rows.length >= EXPORT_CAP) return { rows: rows.slice(0, EXPORT_CAP), truncated: true };
  }
}
