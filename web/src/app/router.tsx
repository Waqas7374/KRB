import { createBrowserRouter, Navigate } from "react-router-dom";

import { ErrorBoundary } from "@/components/error-boundary";
import { SystemStatusPage } from "@/features/system/SystemStatusPage";

/**
 * Routes are declared centrally so each one can carry the permission it
 * requires (from Phase 1), letting the router 404 before rendering a page
 * whose first query would fail.
 */
export const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to="/status" replace />,
  },
  {
    path: "/status",
    element: (
      <ErrorBoundary label="Platform status">
        <SystemStatusPage />
      </ErrorBoundary>
    ),
  },
  {
    path: "*",
    element: (
      <main className="mx-auto max-w-3xl px-4 py-16">
        <h1 className="text-xl font-semibold">Page not found</h1>
        <p className="mt-2 text-sm" style={{ color: "var(--fg-muted)" }}>
          The address you opened does not match any screen in this application.
        </p>
      </main>
    ),
  },
]);
