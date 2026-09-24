import { Bookmark, Search, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/menu";

import { type ListParamsApi } from "./use-list-params";

export interface FilterOption {
  value: string;
  label: string;
}

export interface FilterDef {
  /** URL and API query parameter name. */
  key: string;
  label: string;
  options: FilterOption[];
}

interface FilterBarProps {
  list: ListParamsApi;
  searchPlaceholder?: string;
  filters?: FilterDef[];
  /** Enables personal saved views, stored under this id. */
  savedViewsId?: string;
  /** False for endpoints without a `q` parameter — a search box that
   *  silently does nothing is worse than no search box. */
  search?: boolean;
}

/**
 * Search box + select filters bound to the URL. `/` focuses the search from
 * anywhere on the page (docs/08 §3). Typing is debounced so each keystroke
 * does not become a request.
 */
export function FilterBar({
  list,
  searchPlaceholder,
  filters = [],
  savedViewsId,
  search = true,
}: FilterBarProps) {
  const [text, setText] = useState(list.params.q);
  const inputRef = useRef<HTMLInputElement>(null);
  const { setSearch } = list;

  // Follow the URL when it changes underneath us (back button, saved view).
  useEffect(() => setText(list.params.q), [list.params.q]);

  useEffect(() => {
    if (text === list.params.q) return;
    const handle = setTimeout(() => setSearch(text), 300);
    return () => clearTimeout(handle);
  }, [text, list.params.q, setSearch]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key !== "/" || target.closest("input,textarea,select,[contenteditable]")) return;
      event.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const activeCount = Object.keys(list.params.filters).length + (list.params.q ? 1 : 0);

  return (
    <>
      {search && (
        <div className="relative w-full max-w-64">
          <Search
            className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-fg-subtle"
            aria-hidden
          />
          <Input
            ref={inputRef}
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={searchPlaceholder ?? "Search…  ( / )"}
            aria-label="Search"
            className="h-7 pl-7"
          />
        </div>
      )}
      {filters.map((filter) => (
        <Select
          key={filter.key}
          aria-label={filter.label}
          className="h-7 w-auto max-w-48"
          value={list.params.filters[filter.key] ?? ""}
          onChange={(e) => list.setFilter(filter.key, e.target.value || undefined)}
        >
          <option value="">{filter.label}: all</option>
          {filter.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      ))}
      {activeCount > 0 && (
        <Button size="sm" variant="ghost" onClick={list.clearFilters}>
          <X /> Clear
        </Button>
      )}
      {savedViewsId && <SavedViews id={savedViewsId} list={list} />}
    </>
  );
}

interface SavedView {
  name: string;
  search: string;
}

function loadViews(id: string): SavedView[] {
  try {
    return JSON.parse(localStorage.getItem(`krb.views.${id}`) ?? "[]") as SavedView[];
  } catch {
    return [];
  }
}

/**
 * Personal saved views: a named snapshot of the URL's filter/sort state.
 * Stored in this browser only. Shared (team) views need a server table and
 * are listed as open work in docs/15.
 */
function SavedViews({ id, list }: { id: string; list: ListParamsApi }) {
  const [views, setViews] = useState<SavedView[]>(() => loadViews(id));

  const persist = (next: SavedView[]) => {
    setViews(next);
    try {
      localStorage.setItem(`krb.views.${id}`, JSON.stringify(next));
    } catch {
      // Preference only.
    }
  };

  const save = () => {
    const name = window.prompt("Name this view", "")?.trim();
    if (!name) return;
    const search = new URLSearchParams(list.search);
    search.delete("offset");
    persist([...views.filter((v) => v.name !== name), { name, search: search.toString() }]);
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="ghost">
          <Bookmark /> Views
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>Saved views</DropdownMenuLabel>
        {views.length === 0 && <p className="px-2 py-1.5 text-xs text-fg-subtle">None saved yet</p>}
        {views.map((view) => (
          <div key={view.name} className="flex items-center">
            <div className="flex-1">
              <DropdownMenuItem onSelect={() => list.applySearch(view.search)}>
                {view.name}
              </DropdownMenuItem>
            </div>
            <button
              type="button"
              aria-label={`Delete view ${view.name}`}
              className="rounded-sm p-1 text-fg-subtle hover:text-danger"
              onClick={() => persist(views.filter((v) => v.name !== view.name))}
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={save}>Save current view…</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
