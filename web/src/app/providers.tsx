import { QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";

import { createQueryClient } from "@/app/query-client";
import { useAuthStore } from "@/features/auth/auth-store";

export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);

  // When a session ends (sign-out, expiry, revocation), drop every cached
  // response. Otherwise the next person to sign in on this browser could be
  // shown the previous user's data — scoped to *their* access — until each
  // query happened to refetch.
  useEffect(
    () =>
      useAuthStore.subscribe((state, previous) => {
        if (previous.status === "authenticated" && state.status !== "authenticated") {
          queryClient.clear();
        }
      }),
    [queryClient],
  );

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
