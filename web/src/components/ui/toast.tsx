import { CheckCircle2, CircleAlert, X } from "lucide-react";

import { useToastStore } from "@/lib/toast";
import { cn } from "@/lib/utils";

/** Mounted once in the shell; a polite live region announces each toast. */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.kind === "error" ? "alert" : "status"}
          className={cn(
            "pointer-events-auto flex items-start gap-2 rounded-md border bg-surface-raised px-3 py-2.5 text-sm shadow-[var(--shadow-float)]",
            t.kind === "error" ? "border-danger" : "border-border",
          )}
        >
          {t.kind === "success" ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
          ) : (
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
          )}
          <p className="flex-1">{t.message}</p>
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            className="text-fg-muted hover:text-fg"
            aria-label="Dismiss"
          >
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
