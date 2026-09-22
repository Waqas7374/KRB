import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, Loader2, RefreshCw } from "lucide-react";

import { systemApi } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

/**
 * Phase 0 verification screen.
 *
 * It exists to prove the browser really reaches the API, and that the API
 * really reaches PostgreSQL and Redis. Every value on it comes from a live
 * request — nothing here is mocked. It stays in the app as an operator
 * diagnostics page once the ERP screens land.
 */
export function SystemStatusPage() {
  const ready = useQuery({
    queryKey: ["system", "ready"],
    queryFn: systemApi.ready,
    refetchInterval: 15_000,
    retry: 1,
  });

  const version = useQuery({
    queryKey: ["system", "version"],
    queryFn: systemApi.version,
    staleTime: 5 * 60_000,
  });

  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <header className="mb-6 flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">KRB ERP</h1>
          <p className="text-sm" style={{ color: "var(--fg-muted)" }}>
            Platform status
          </p>
        </div>
        <button
          type="button"
          onClick={() => void ready.refetch()}
          disabled={ready.isFetching}
          className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
          style={{ borderColor: "var(--border-strong)" }}
        >
          <RefreshCw className={`size-3.5 ${ready.isFetching ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </header>

      <section
        className="rounded-md border"
        style={{ borderColor: "var(--border)", background: "var(--surface)" }}
        aria-labelledby="dependencies-heading"
      >
        <h2
          id="dependencies-heading"
          className="border-b px-4 py-2.5 text-xs font-semibold uppercase tracking-wide"
          style={{ borderColor: "var(--border)", color: "var(--fg-muted)" }}
        >
          Dependencies
        </h2>

        {ready.isPending && (
          <p
            className="flex items-center gap-2 px-4 py-6 text-sm"
            style={{ color: "var(--fg-muted)" }}
          >
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Contacting the API…
          </p>
        )}

        {ready.isError && (
          <p className="px-4 py-6 text-sm" style={{ color: "var(--danger)" }} role="alert">
            Cannot reach the API. Is the backend running on{" "}
            <code className="font-mono">{import.meta.env.VITE_API_BASE_URL}</code>?
          </p>
        )}

        {ready.data && (
          <ul className="divide-y" style={{ borderColor: "var(--border)" }}>
            {ready.data.components.map((component) => (
              <li key={component.name} className="flex items-center gap-3 px-4 py-3">
                {component.ready ? (
                  <CheckCircle2
                    className="size-4 shrink-0"
                    style={{ color: "var(--success)" }}
                    aria-hidden
                  />
                ) : (
                  <CircleAlert
                    className="size-4 shrink-0"
                    style={{ color: "var(--danger)" }}
                    aria-hidden
                  />
                )}
                <span className="flex-1 font-medium capitalize">{component.name}</span>
                <span
                  className="text-sm"
                  style={{ color: component.ready ? "var(--success)" : "var(--danger)" }}
                >
                  {component.ready ? "reachable" : (component.detail ?? "unreachable")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {ready.data && (
        <p className="mt-2 text-xs" style={{ color: "var(--fg-subtle)" }}>
          Checked {formatDateTime(ready.data.checked_at)}
        </p>
      )}

      <section className="mt-8">
        <h2
          className="mb-2 text-xs font-semibold uppercase tracking-wide"
          style={{ color: "var(--fg-muted)" }}
        >
          Build
        </h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
          <dt style={{ color: "var(--fg-muted)" }}>Environment</dt>
          <dd className="font-mono">{version.data?.environment ?? "—"}</dd>
          <dt style={{ color: "var(--fg-muted)" }}>Version</dt>
          <dd className="font-mono">{version.data?.version ?? "—"}</dd>
          <dt style={{ color: "var(--fg-muted)" }}>Commit</dt>
          <dd className="font-mono">{version.data?.git_sha ?? "—"}</dd>
        </dl>
      </section>

      <p className="mt-10 text-xs" style={{ color: "var(--fg-subtle)" }}>
        Phase 0 — foundation. ERP modules land from Phase 1; see{" "}
        <code className="font-mono">docs/10-roadmap.md</code>.
      </p>
    </main>
  );
}
