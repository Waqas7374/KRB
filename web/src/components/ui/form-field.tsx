import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

import { cn } from "@/lib/utils";

interface FormFieldProps {
  label: string;
  error?: string | undefined;
  hint?: ReactNode;
  required?: boolean;
  className?: string;
  /** A single input-like element; it receives id and aria wiring. */
  children: ReactElement<Record<string, unknown>>;
}

/**
 * Label + control + hint/error, with the accessibility wiring done once:
 * the label targets the control, the error is announced and linked with
 * aria-describedby, and aria-invalid drives the red border.
 */
export function FormField({ label, error, hint, required, className, children }: FormFieldProps) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;

  const control = isValidElement(children)
    ? cloneElement(children, {
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": describedBy,
        "aria-required": required || undefined,
      })
    : children;

  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <label htmlFor={id} className="text-xs font-medium text-fg-muted">
        {label}
        {required && (
          <span className="ml-0.5 text-danger" aria-hidden>
            *
          </span>
        )}
      </label>
      {control}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-fg-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** Two-column form grid that collapses to one column on narrow screens. */
export function FormGrid({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2", className)}>
      {children}
    </div>
  );
}

export function FormSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="border-t border-border pt-3">
      <legend className="pr-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">
        {title}
      </legend>
      <div className="mt-2">{children}</div>
    </fieldset>
  );
}
