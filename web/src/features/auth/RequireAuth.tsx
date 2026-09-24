import { Loader2 } from "lucide-react";
import { type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuthStore } from "./auth-store";

/**
 * Gate for every signed-in route.
 * - booting: the stored refresh token is being exchanged; render nothing
 *   route-specific so no query fires without a token.
 * - anonymous: to /login, remembering where the user was going.
 * - must_change_password: to /change-password until it is done. The API does
 *   not enforce this flag itself (recorded in docs/15 as a backend gap), so
 *   this redirect is currently the only thing enforcing it.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);
  const mustChange = useAuthStore((s) => s.me?.user.must_change_password ?? false);
  const location = useLocation();

  if (status === "booting") {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-fg-muted" aria-busy>
        <Loader2 className="mr-2 size-4 animate-spin" aria-hidden /> Signing you in…
      </div>
    );
  }
  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  if (mustChange && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace />;
  }
  return <>{children}</>;
}
