import { type ReactNode } from "react";

/** Centred single-card layout for the signed-out screens. */
export function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="flex min-h-screen items-start justify-center bg-surface px-4 pt-[14vh]">
      <div className="w-full max-w-sm">
        <p className="mb-6 text-center text-sm font-semibold tracking-tight text-fg-muted">
          {import.meta.env.VITE_APP_NAME ?? "KRB ERP"}
        </p>
        <div className="rounded-lg border border-border bg-bg p-6">
          <h1 className="text-lg font-semibold">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-fg-muted">{subtitle}</p>}
          <div className="mt-5">{children}</div>
        </div>
      </div>
    </main>
  );
}

export function FormAlert({
  children,
  tone = "danger",
}: {
  children: ReactNode;
  tone?: "danger" | "success";
}) {
  return (
    <p
      role={tone === "danger" ? "alert" : "status"}
      className={
        tone === "danger"
          ? "rounded-md bg-danger-bg px-3 py-2 text-sm text-danger"
          : "rounded-md bg-success-bg px-3 py-2 text-sm text-success"
      }
    >
      {children}
    </p>
  );
}
