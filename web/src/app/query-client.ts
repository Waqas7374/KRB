import { QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/lib/api";

/**
 * TanStack Query is the model layer: server data lives in its cache and is not
 * duplicated into a store. See docs/08-frontend-and-design-system.md §1.
 *
 * Kept out of providers.tsx so that file exports only a component and React
 * Fast Refresh keeps working.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        // Permission and not-found errors are rendered by the screen that
        // asked for the data; anything else is worth a console breadcrumb.
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) return;
        console.error("query failed", error);
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          // Never retry a request the server has definitively rejected.
          if (error instanceof ApiError && !error.isRetryable) return false;
          return failureCount < 2;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      },
      mutations: {
        // Mutations are never retried automatically: a retried POST without an
        // idempotency key is how duplicate purchase orders get created.
        retry: false,
      },
    },
  });
}
