import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, RotateCcw, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { describeError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { formatDateTime, humanize } from "@/lib/utils";
import type { PermissionRead, RoleDetail } from "@/types/models";

export function RoleDetailPage() {
  const { roleId = "" } = useParams();
  const queryClient = useQueryClient();
  const canManage = useCan("roles.manage");

  const role = useQuery({
    queryKey: ["roles", "detail", roleId],
    queryFn: () => api.get<RoleDetail>(`/roles/${roleId}`),
  });
  const catalogue = useQuery({
    queryKey: ["permissions"],
    queryFn: () => api.get<PermissionRead[]>("/permissions"),
    staleTime: Infinity, // defined in code; changes only with a deploy
  });

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (role.data) setSelected(new Set(role.data.permission_codes ?? []));
  }, [role.data]);

  const byModule = useMemo(() => {
    const groups = new Map<string, PermissionRead[]>();
    for (const p of catalogue.data ?? []) {
      const list = groups.get(p.module) ?? [];
      list.push(p);
      groups.set(p.module, list);
    }
    return [...groups.entries()];
  }, [catalogue.data]);

  const save = useMutation({
    mutationFn: () =>
      api.put<RoleDetail>(`/roles/${roleId}/permissions`, {
        permission_codes: [...selected].sort(),
      }),
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["roles"] });
      toast.success("Permissions saved. Users with this role get them on their next request.");
    },
    onError: (err) => setError(describeError(err)),
  });

  if (role.isLoading || catalogue.isLoading) return <PageSkeleton />;
  if (role.error || !role.data)
    return <ErrorState error={role.error} onRetry={() => void role.refetch()} />;
  if (catalogue.error)
    return <ErrorState error={catalogue.error} onRetry={() => void catalogue.refetch()} />;

  const r = role.data;
  const editable = canManage && !r.is_locked;
  const original = new Set(r.permission_codes ?? []);
  const dirty = selected.size !== original.size || [...selected].some((c) => !original.has(c));

  const toggle = (code: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(code);
      else next.delete(code);
      return next;
    });

  return (
    <>
      <PageHeader
        title={r.name}
        crumbs={[{ label: "Roles", to: "/roles" }, { label: r.name }]}
        meta={
          <>
            <span className="font-mono text-sm text-fg-muted">{r.code}</span>
            {r.is_system && <Tag>System</Tag>}
            {r.is_locked && (
              <span className="inline-flex items-center gap-1 text-sm text-fg-muted">
                <Lock className="size-3.5" /> Locked — permissions cannot be edited
              </span>
            )}
            {r.is_read_only && <Tag>Read-only</Tag>}
          </>
        }
        actions={
          editable && (
            <>
              <Button
                disabled={!dirty || save.isPending}
                onClick={() => setSelected(new Set(original))}
              >
                <RotateCcw /> Discard
              </Button>
              <Button
                variant="primary"
                disabled={!dirty}
                loading={save.isPending}
                onClick={() => save.mutate()}
              >
                <Save /> Save permissions
              </Button>
            </>
          )
        }
      />
      <PageBody>
        <Section title="Role">
          <FieldGrid
            items={[
              { label: "Can be granted at", value: r.allowed_scope_types.map(humanize).join(", ") },
              { label: "Permissions", value: `${selected.size} of ${catalogue.data?.length ?? 0}` },
              { label: "Created", value: formatDateTime(r.created_at) },
              { label: "Description", value: r.description, wide: true },
            ]}
          />
        </Section>
        {error && <FormAlert>{error}</FormAlert>}
        {editable && (
          <p className="text-sm text-fg-muted">
            You can only include permissions you hold yourself; the server refuses the rest.
            Permissions marked restricted are sensitive (salaries, bank details, posting) — grant
            them deliberately.
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {byModule.map(([module, perms]) => {
            const on = perms.filter((p) => selected.has(p.code)).length;
            return (
              <Section
                key={module}
                title={`${humanize(module)} (${on}/${perms.length})`}
                actions={
                  editable && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => perms.forEach((p) => toggle(p.code, on < perms.length))}
                    >
                      {on < perms.length ? "All" : "None"}
                    </Button>
                  )
                }
              >
                <ul className="divide-y divide-border">
                  {perms.map((p) => (
                    <li key={p.code}>
                      <label className="flex items-start gap-2.5 px-4 py-1.5 text-sm">
                        <Checkbox
                          className="mt-0.5"
                          checked={selected.has(p.code)}
                          disabled={!editable}
                          onChange={(e) => toggle(p.code, e.target.checked)}
                        />
                        <span className="min-w-0">
                          <span className="font-mono text-xs">{p.code}</span>
                          {p.is_restricted && <Tag className="ml-1.5">Restricted</Tag>}
                          <span className="block text-xs text-fg-muted">{p.description}</span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              </Section>
            );
          })}
        </div>
      </PageBody>
    </>
  );
}
