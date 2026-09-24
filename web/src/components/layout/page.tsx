import { ChevronRight } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { cn } from "@/lib/utils";

export interface Crumb {
  label: string;
  to?: string;
}

/**
 * Breadcrumbs reflect the record's real hierarchy, not navigation history
 * (docs/08 §2), so every detail route passes them explicitly.
 */
export function PageHeader({
  title,
  subtitle,
  crumbs,
  actions,
  meta,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  crumbs?: Crumb[];
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-1.5 border-b border-border bg-bg px-6 pb-3 pt-4">
      {crumbs && crumbs.length > 0 && (
        <nav aria-label="Breadcrumb">
          <ol className="flex flex-wrap items-center gap-1 text-xs text-fg-muted">
            {crumbs.map((crumb, i) => (
              <Fragment key={`${crumb.label}-${i}`}>
                {i > 0 && <ChevronRight className="size-3 text-fg-subtle" aria-hidden />}
                <li>
                  {crumb.to ? (
                    <Link to={crumb.to} className="hover:text-fg hover:underline">
                      {crumb.label}
                    </Link>
                  ) : (
                    <span aria-current="page">{crumb.label}</span>
                  )}
                </li>
              </Fragment>
            ))}
          </ol>
        </nav>
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-0.5 text-sm text-fg-muted">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {meta && <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">{meta}</div>}
    </header>
  );
}

export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex flex-col gap-4 p-6", className)}>{children}</div>;
}

export function Section({
  title,
  actions,
  children,
  className,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-md border border-border bg-bg", className)}>
      <header className="flex items-center justify-between gap-2 border-b border-border bg-surface px-4 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-fg-muted">{title}</h2>
        {actions && <div className="flex items-center gap-1">{actions}</div>}
      </header>
      <div>{children}</div>
    </section>
  );
}

export interface FieldItem {
  label: string;
  value: ReactNode;
  mono?: boolean;
  /** Spans the full width (long text). */
  wide?: boolean;
}

/** A definition list for read-only record fields. Empty values show an em dash. */
export function FieldGrid({ items, columns = 3 }: { items: FieldItem[]; columns?: 2 | 3 | 4 }) {
  return (
    <dl
      className={cn(
        "grid grid-cols-1 gap-x-6 gap-y-3 p-4 sm:grid-cols-2",
        columns === 3 && "lg:grid-cols-3",
        columns === 4 && "lg:grid-cols-4",
      )}
    >
      {items.map((item) => (
        <div key={item.label} className={cn("min-w-0", item.wide && "sm:col-span-full")}>
          <dt className="text-xs text-fg-muted">{item.label}</dt>
          <dd className={cn("mt-0.5 break-words", item.mono && "font-mono text-sm")}>
            {item.value === null || item.value === undefined || item.value === ""
              ? "—"
              : item.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
