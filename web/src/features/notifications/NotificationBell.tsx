import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CheckCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/menu";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { formatDateTime, cn } from "@/lib/utils";
import type { NotificationInbox, NotificationRead } from "@/types/models";

const notificationKeys = {
  all: ["notifications"] as const,
  inbox: () => [...notificationKeys.all, "inbox"] as const,
};

/**
 * Polls every 60 s. docs/08 §2 specifies SSE; the SSE endpoint lands with the
 * live review queue in Phase 6, and this hook is the only thing to swap.
 */
export function NotificationBell() {
  const allowed = useCan("notifications.view_own");
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const inbox = useQuery({
    queryKey: notificationKeys.inbox(),
    queryFn: () => api.get<NotificationInbox>("/notifications", { query: { limit: 10 } }),
    refetchInterval: 60_000,
    enabled: allowed,
  });

  // Optimistic is allowed here: marking read is reversible and low-stakes
  // (docs/08 §1), unlike anything financial.
  const markRead = useMutation({
    mutationFn: (id: string) => api.post(`/notifications/${id}/read`),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: notificationKeys.inbox() });
      const previous = queryClient.getQueryData<NotificationInbox>(notificationKeys.inbox());
      if (previous) {
        queryClient.setQueryData<NotificationInbox>(notificationKeys.inbox(), {
          ...previous,
          unread_count: Math.max(0, previous.unread_count - 1),
          items: previous.items.map((n) =>
            n.id === id ? { ...n, read_at: new Date().toISOString() } : n,
          ),
        });
      }
      return { previous };
    },
    onError: (_err, _id, context) => {
      if (context?.previous) queryClient.setQueryData(notificationKeys.inbox(), context.previous);
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: notificationKeys.all }),
  });

  const markAll = useMutation({
    mutationFn: () => api.post("/notifications/read-all"),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: notificationKeys.all }),
  });

  if (!allowed) return null;
  const unread = inbox.data?.unread_count ?? 0;

  const open = (n: NotificationRead) => {
    if (!n.read_at) markRead.mutate(n.id);
    if (n.link_path) navigate(n.link_path);
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        >
          <Bell className="!size-4" />
          {unread > 0 && (
            <span className="absolute right-1 top-1 min-w-4 rounded-full bg-danger px-1 text-2xs font-semibold leading-4 text-white">
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-96 p-0">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <p className="text-sm font-semibold">Notifications</p>
          {unread > 0 && (
            <Button size="sm" variant="ghost" onClick={() => markAll.mutate()}>
              <CheckCheck /> Mark all read
            </Button>
          )}
        </div>
        <div className="max-h-96 overflow-y-auto p-1">
          {inbox.isError && (
            <p className="px-3 py-6 text-center text-sm text-danger">
              Could not load notifications.
            </p>
          )}
          {inbox.data?.items.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-fg-muted">You are all caught up.</p>
          )}
          {inbox.data?.items.map((n) => (
            <DropdownMenuItem key={n.id} onSelect={() => open(n)}>
              <div className="flex w-full items-start gap-2">
                <span
                  className={cn(
                    "mt-1.5 size-1.5 shrink-0 rounded-full",
                    n.read_at ? "bg-transparent" : "bg-primary",
                  )}
                  aria-label={n.read_at ? undefined : "Unread"}
                />
                <div className="min-w-0 flex-1">
                  <p className={cn("truncate", !n.read_at && "font-medium")}>{n.title}</p>
                  <p className="line-clamp-2 text-xs text-fg-muted">{n.body}</p>
                  <p className="mt-0.5 text-2xs text-fg-subtle">{formatDateTime(n.created_at)}</p>
                </div>
              </div>
            </DropdownMenuItem>
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
