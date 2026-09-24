import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import { ApiError, NetworkError } from "@/lib/api";

/** One human sentence for any thrown value, for toasts and inline alerts. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isPermissionError) return "You do not have permission to do that.";
    if (error.problem.type.endsWith("/version-conflict")) {
      return "Someone else changed this record while you were editing. Reload it and try again.";
    }
    const base = error.message || `Request failed (${error.status})`;
    return error.requestId ? `${base} (ref ${error.requestId.slice(-8)})` : base;
  }
  if (error instanceof NetworkError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

/**
 * Map an RFC 9457 validation response onto react-hook-form fields.
 *
 * Returns the messages that did not match any known field so the caller can
 * show them in a form-level alert instead of losing them.
 */
export function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  knownFields: readonly string[],
): string[] {
  if (!(error instanceof ApiError) || error.fieldErrors.length === 0) {
    return [describeError(error)];
  }
  const unmatched: string[] = [];
  for (const fieldError of error.fieldErrors) {
    const field = fieldError.field.replace(/^body\./, "");
    if (knownFields.includes(field)) {
      setError(field as Path<T>, { type: "server", message: fieldError.message });
    } else {
      unmatched.push(`${field}: ${fieldError.message}`);
    }
  }
  return unmatched;
}
