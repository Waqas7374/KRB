import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Modal and side drawer share Radix Dialog, which provides the focus trap,
 * focus restore, Escape-to-close and aria-modal wiring (docs/08 §4).
 *
 * Modal: only for a short, blocking decision.
 * Drawer: create/edit from a list without losing the list's state.
 */

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** `drawer` slides in from the right and is wider. */
  variant?: "modal" | "drawer";
  className?: string;
}

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  variant = "modal",
  className,
}: DialogProps) {
  const drawer = variant === "drawer";
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <DialogPrimitive.Content
          className={cn(
            "fixed z-50 flex flex-col border border-border bg-bg text-fg shadow-[var(--shadow-float)] focus:outline-none",
            drawer
              ? "inset-y-0 right-0 w-full max-w-xl border-y-0 border-r-0"
              : "left-1/2 top-[12vh] max-h-[76vh] w-[calc(100%-2rem)] max-w-md -translate-x-1/2 rounded-lg",
            className,
          )}
          // Radix warns without a description; ours is optional.
          {...(description ? {} : { "aria-describedby": undefined })}
        >
          <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <DialogPrimitive.Title className="text-base font-semibold">
                {title}
              </DialogPrimitive.Title>
              {description && (
                <DialogPrimitive.Description className="mt-0.5 text-sm text-fg-muted">
                  {description}
                </DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close
              className="rounded-sm p-1 text-fg-muted hover:bg-surface hover:text-fg"
              aria-label="Close"
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{children}</div>
          {footer && (
            <footer className="flex justify-end gap-2 border-t border-border bg-surface px-4 py-2.5">
              {footer}
            </footer>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
