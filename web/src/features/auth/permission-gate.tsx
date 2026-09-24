import { type ReactNode } from "react";

import { ForbiddenState } from "@/components/ui/states";

import { useCan } from "./use-can";

/**
 * Renders children only when the user holds `permission`. Use `fallback` for
 * page-level gates (a Forbidden panel); omit it for buttons, which should
 * simply not appear.
 */
export function PermissionGate({
  permission,
  children,
  fallback = null,
}: {
  permission: string;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  return useCan(permission) ? <>{children}</> : <>{fallback}</>;
}

/** Page-level gate: the route renders a Forbidden panel instead of the page. */
export function RequirePermission({
  permission,
  children,
}: {
  permission: string;
  children: ReactNode;
}) {
  return (
    <PermissionGate permission={permission} fallback={<ForbiddenState />}>
      {children}
    </PermissionGate>
  );
}
