import { StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { AppProviders } from "@/app/providers";
import { router } from "@/app/router";
import { ErrorBoundary } from "@/components/error-boundary";
import { PageSkeleton } from "@/components/ui/states";
import { useAuthStore } from "@/features/auth/auth-store";
import { applyTheme, useUiStore } from "@/lib/ui-store";
import "@/styles/index.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root is missing from index.html");
}

// Before first paint, so the sign-in screen is not flashed in the wrong theme.
applyTheme(useUiStore.getState().theme);
// Exchange a stored refresh token for a session. Routes render a
// "signing you in" state until this settles, so no query fires unauthenticated.
void useAuthStore.getState().boot();

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <AppProviders>
        <Suspense fallback={<PageSkeleton />}>
          <RouterProvider router={router} />
        </Suspense>
      </AppProviders>
    </ErrorBoundary>
  </StrictMode>,
);
