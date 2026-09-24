import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, LogOut } from "lucide-react";
import { Link } from "react-router-dom";

import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/input";
import { ErrorState } from "@/components/ui/states";
import { Tag } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { describeError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { formatDateTime, humanize } from "@/lib/utils";
import type { SessionSummary } from "@/types/models";

import { useAuthStore } from "./auth-store";
import { useCan } from "./use-can";

interface Preference {
  notification_type: string;
  channel: string;
  is_enabled: boolean;
}

/** Short, human description of a user-agent string. */
function describeAgent(ua: string | null): string {
  if (!ua) return "Unknown device";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Chrome\//.test(ua)
      ? "Chrome"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Safari\//.test(ua)
          ? "Safari"
          : /okhttp|Expo|Dart/i.test(ua)
            ? "Mobile app"
            : "Browser";
  const os = /Windows/.test(ua)
    ? "Windows"
    : /Android/.test(ua)
      ? "Android"
      : /iPhone|iPad/.test(ua)
        ? "iOS"
        : /Mac OS/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : "";
  return os ? `${browser} on ${os}` : browser;
}

export function AccountPage() {
  const me = useAuthStore((s) => s.me);
  const queryClient = useQueryClient();
  const canNotify = useCan("notifications.view_own");

  const sessions = useQuery({
    queryKey: ["auth", "sessions"],
    queryFn: () => api.get<SessionSummary[]>("/auth/sessions"),
  });
  const prefs = useQuery({
    queryKey: ["notifications", "preferences"],
    queryFn: () => api.get<Preference[]>("/notifications/preferences"),
    enabled: canNotify,
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.delete(`/auth/sessions/${id}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["auth", "sessions"] });
      toast.success("That session has been signed out.");
    },
    onError: (err) => toast.error(describeError(err)),
  });

  const setPref = useMutation({
    mutationFn: (p: Preference) => api.put("/notifications/preferences", p),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["notifications", "preferences"] }),
    onError: (err) => toast.error(describeError(err)),
  });

  if (!me) return null;
  const types = [...new Set(prefs.data?.map((p) => p.notification_type) ?? [])];
  const channels = [...new Set(prefs.data?.map((p) => p.channel) ?? [])];
  const pref = (type: string, channel: string) =>
    prefs.data?.find((p) => p.notification_type === type && p.channel === channel);

  return (
    <>
      <PageHeader
        title="My account"
        actions={
          <Button asChild>
            <Link to="/change-password">
              <KeyRound /> Change password
            </Link>
          </Button>
        }
      />
      <PageBody className="max-w-4xl">
        <Section title="Profile">
          <FieldGrid
            items={[
              { label: "Name", value: me.user.full_name },
              { label: "Email", value: me.user.email },
              { label: "Phone", value: me.user.phone, mono: true },
              { label: "Company", value: me.company.name },
              { label: "Timezone", value: me.user.timezone ?? me.company.timezone },
              { label: "Last sign-in", value: formatDateTime(me.user.last_login_at) },
            ]}
          />
        </Section>

        <Section title="Where you are signed in">
          {sessions.isError ? (
            <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} />
          ) : (
            <ul className="divide-y divide-border">
              {sessions.data?.map((s) => (
                <li key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm">
                  <span className="font-medium">{describeAgent(s.user_agent)}</span>
                  {s.is_current && <Tag>This browser</Tag>}
                  <span className="text-fg-muted">
                    {s.ip ?? "unknown IP"} · last active{" "}
                    {formatDateTime(s.last_used_at ?? s.issued_at)}
                  </span>
                  {!s.is_current && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto"
                      loading={revoke.isPending && revoke.variables === s.id}
                      onClick={() => revoke.mutate(s.id)}
                    >
                      <LogOut /> Sign out
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Section>

        {canNotify && (
          <Section title="Notifications">
            {prefs.isError ? (
              <ErrorState error={prefs.error} onRetry={() => void prefs.refetch()} />
            ) : types.length === 0 ? (
              <p className="px-4 py-4 text-sm text-fg-muted">
                {prefs.isLoading ? "Loading…" : "No notification types yet."}
              </p>
            ) : (
              <table className="w-full text-sm">
                <caption className="sr-only">Notification preferences</caption>
                <thead className="bg-surface text-left text-xs text-fg-muted">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Notification
                    </th>
                    {channels.map((c) => (
                      <th key={c} scope="col" className="px-4 py-2 text-center font-semibold">
                        {humanize(c)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {types.map((t) => (
                    <tr key={t} className="border-t border-border">
                      <td className="px-4 py-2">{humanize(t)}</td>
                      {channels.map((c) => {
                        const p = pref(t, c);
                        return (
                          <td key={c} className="px-4 py-2 text-center">
                            <Checkbox
                              aria-label={`${humanize(t)} by ${humanize(c)}`}
                              checked={p?.is_enabled ?? false}
                              disabled={!p || setPref.isPending}
                              onChange={(e) =>
                                p && setPref.mutate({ ...p, is_enabled: e.target.checked })
                              }
                            />
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>
        )}
      </PageBody>
    </>
  );
}
