import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * List state lives in the URL (docs/08 §1): a filtered, sorted, paged grid is
 * shareable, bookmarkable and correct under the back button.
 *
 * Reserved keys: q, sort, offset, limit. Every other key is a filter and is
 * passed to the API unchanged, so a filter's URL name is its API name.
 */

export const PAGE_SIZES = [25, 50, 100, 200] as const;
const RESERVED = new Set(["q", "sort", "offset", "limit"]);

export interface ListParams {
  q: string;
  sort: string | undefined;
  offset: number;
  limit: number;
  filters: Record<string, string>;
}

export interface ListParamsApi {
  params: ListParams;
  /** Query object ready for `api.get(..., { query })`. */
  query: Record<string, string | number | undefined>;
  setFilter: (key: string, value: string | undefined) => void;
  setSearch: (q: string) => void;
  setSort: (sort: string | undefined) => void;
  setOffset: (offset: number) => void;
  setLimit: (limit: number) => void;
  /** The raw search string, used by saved views. */
  search: string;
  applySearch: (search: string) => void;
  clearFilters: () => void;
}

function toInt(value: string | null, fallback: number, max: number): number {
  const parsed = value === null ? NaN : Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) return fallback;
  return Math.min(parsed, max);
}

export function useListParams(defaults: { sort?: string; limit?: number } = {}): ListParamsApi {
  const [searchParams, setSearchParams] = useSearchParams();
  const defaultLimit = defaults.limit ?? 50;

  const params = useMemo<ListParams>(() => {
    const filters: Record<string, string> = {};
    searchParams.forEach((value, key) => {
      if (!RESERVED.has(key) && value !== "") filters[key] = value;
    });
    return {
      q: searchParams.get("q") ?? "",
      sort: searchParams.get("sort") ?? defaults.sort,
      offset: toInt(searchParams.get("offset"), 0, 10_000),
      limit: toInt(searchParams.get("limit"), defaultLimit, 200) || defaultLimit,
      filters,
    };
  }, [searchParams, defaults.sort, defaultLimit]);

  const update = useCallback(
    (mutate: (next: URLSearchParams) => void, resetOffset = true) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          mutate(next);
          if (resetOffset) next.delete("offset");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const setOrDelete = (next: URLSearchParams, key: string, value: string | undefined) => {
    if (value === undefined || value === "") next.delete(key);
    else next.set(key, value);
  };

  return {
    params,
    query: {
      ...params.filters,
      q: params.q || undefined,
      sort: params.sort,
      offset: params.offset,
      limit: params.limit,
    },
    search: searchParams.toString(),
    setFilter: (key, value) => update((next) => setOrDelete(next, key, value)),
    setSearch: (q) => update((next) => setOrDelete(next, "q", q.trim())),
    setSort: (sort) => update((next) => setOrDelete(next, "sort", sort)),
    setOffset: (offset) =>
      update((next) => setOrDelete(next, "offset", offset > 0 ? String(offset) : undefined), false),
    setLimit: (limit) =>
      update((next) =>
        setOrDelete(next, "limit", limit === defaultLimit ? undefined : String(limit)),
      ),
    applySearch: (search) => setSearchParams(new URLSearchParams(search), { replace: true }),
    clearFilters: () =>
      update((next) => {
        for (const key of [...next.keys()]) {
          if (key !== "sort" && key !== "limit") next.delete(key);
        }
      }),
  };
}
