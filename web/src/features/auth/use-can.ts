import { useAuthStore } from "./auth-store";

/**
 * Mirrors the server's permission check (docs/03-rbac.md §7) for UI gating
 * only. Hiding a button is a courtesy; the API still enforces every rule,
 * including scope, which this hook does not attempt to reproduce.
 */
export function useCan(permission: string | undefined): boolean {
  return useAuthStore((state) => {
    if (!permission) return true;
    const me = state.me;
    if (!me) return false;
    if (me.is_superuser) return true;
    return me.permissions.includes(permission);
  });
}

/** True when the user holds at least one of the permissions. */
export function useCanAny(permissions: readonly string[]): boolean {
  return useAuthStore((state) => {
    const me = state.me;
    if (!me) return false;
    if (me.is_superuser) return true;
    return permissions.some((p) => me.permissions.includes(p));
  });
}
