import { AlertTriangle, Inbox, Lock, RefreshCw } from "lucide-react";
import { type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { describeError } from "@/lib/errors";
import { cn } from "@/lib/utils";

/**
 * Empty, loading and error states must look different from each other
 * (docs/08 §3): an empty grid that looks like a failed one gets reported as a
 * bug, and a failed one that looks empty gets trusted.
 */

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <Inbox className="size-6 text-fg-subtle" aria-hidden />
      <p className="font-medium">{title}</p>
      {description && <p className="max-w-sm text-sm text-fg-muted">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (error instanceof ApiError && error.isPermissionError) {
    return <ForbiddenState />;
  }
  return (
    <div role="alert" className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <AlertTriangle className="size-6 text-danger" aria-hidden />
      <p className="font-medium">Could not load this data</p>
      <p className="max-w-md text-sm text-fg-muted">{describeError(error)}</p>
      {onRetry && (
        <Button size="sm" onClick={onRetry} className="mt-2">
          <RefreshCw /> Retry
        </Button>
      )}
    </div>
  );
}

export function ForbiddenState({ message }: { message?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <Lock className="size-6 text-fg-subtle" aria-hidden />
      <p className="font-medium">You do not have access to this</p>
      <p className="max-w-sm text-sm text-fg-muted">
        {message ?? "Ask an administrator if you need this permission for your work."}
      </p>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-sm bg-surface", className)} aria-hidden />;
}

export function PageSkeleton() {
  return (
    <div className="flex flex-col gap-3 p-6" aria-busy aria-label="Loading">
      <Skeleton className="h-6 w-64" />
      <Skeleton className="h-4 w-96" />
      <Skeleton className="mt-4 h-40 w-full" />
    </div>
  );
}
