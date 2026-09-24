import {
  forwardRef,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { cn } from "@/lib/utils";

const fieldBase =
  "w-full rounded-md border border-border-strong bg-surface-raised px-2.5 text-sm text-fg placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-danger";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(fieldBase, "h-8", className)} {...props} />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, rows = 3, ...props }, ref) => (
  <textarea ref={ref} rows={rows} className={cn(fieldBase, "py-1.5", className)} {...props} />
));
Textarea.displayName = "Textarea";

/**
 * A native select. Deliberately not a custom listbox: it is keyboard- and
 * screen-reader-correct for free, and typing a letter jumps to the option,
 * which matters for long master-data lists.
 */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn(fieldBase, "h-8 pr-7", className)} {...props}>
      {children}
    </select>
  ),
);
Select.displayName = "Select";

export const Checkbox = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      type="checkbox"
      className={cn("size-3.5 rounded-sm accent-[var(--primary)]", className)}
      {...props}
    />
  ),
);
Checkbox.displayName = "Checkbox";
