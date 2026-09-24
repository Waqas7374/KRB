import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Plus, UserX } from "lucide-react";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { useParams } from "react-router-dom";
import { z } from "zod";

import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { useAuthStore } from "@/features/auth/auth-store";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { dirtyValues, emptyToNull, str } from "@/lib/forms";
import {
  labelFor,
  useDepartmentOptions,
  useProjectOptions,
  useRoleOptions,
  useSiteOptions,
  type Option,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, humanize } from "@/lib/utils";
import type { RoleGrantRead, RoleRead, UserAdminRead } from "@/types/models";

export function UserDetailPage() {
  const { userId = "" } = useParams();
  const queryClient = useQueryClient();
  const myId = useAuthStore((s) => s.me?.user.id);
  const canUpdate = useCan("users.update");
  const canDeactivate = useCan("users.deactivate");
  const canReset = useCan("users.reset_password");
  const canAssign = useCan("users.assign_roles");
  const projects = useProjectOptions();
  const sites = useSiteOptions();
  const departments = useDepartmentOptions();
  const [dialog, setDialog] = useState<null | "edit" | "deactivate" | "reset" | "grant">(null);
  const [revoking, setRevoking] = useState<RoleGrantRead | null>(null);

  const user = useQuery({
    queryKey: ["users", "detail", userId],
    queryFn: () => api.get<UserAdminRead>(`/users/${userId}`),
  });
  const grants = useQuery({
    queryKey: ["users", "grants", userId],
    queryFn: () => api.get<RoleGrantRead[]>(`/users/${userId}/role-grants`),
  });

  const scopeLabel = useMemo(() => {
    const byType: Record<string, Option[]> = {
      PROJECT: projects.options,
      SITE: sites.options,
      DEPARTMENT: departments.options,
    };
    return (g: RoleGrantRead) =>
      g.scope_id
        ? labelFor(byType[g.scope_type] ?? [], g.scope_id)
        : g.scope_type === "GLOBAL"
          ? "Everything"
          : "Whole company";
  }, [projects.options, sites.options, departments.options]);

  if (user.isLoading) return <PageSkeleton />;
  if (user.error || !user.data)
    return <ErrorState error={user.error} onRetry={() => void user.refetch()} />;
  const u = user.data;
  const isSelf = u.id === myId;
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });
  const active = (grants.data ?? []).filter((g) => !g.revoked_at);
  const revoked = (grants.data ?? []).filter((g) => g.revoked_at);

  return (
    <>
      <PageHeader
        title={u.full_name}
        crumbs={[{ label: "Users", to: "/users" }, { label: u.full_name }]}
        meta={
          <>
            <StatusBadge status={u.status} />
            {u.must_change_password && <Tag>Must set a new password</Tag>}
          </>
        }
        actions={
          u.status !== "DEACTIVATED" && (
            <>
              {canUpdate && (
                <Button onClick={() => setDialog("edit")}>
                  <Pencil /> Edit
                </Button>
              )}
              {canReset && (
                <Button onClick={() => setDialog("reset")}>
                  <KeyRound /> Reset password
                </Button>
              )}
              {canDeactivate && !isSelf && (
                <Button variant="danger" onClick={() => setDialog("deactivate")}>
                  <UserX /> Deactivate
                </Button>
              )}
            </>
          )
        }
      />
      <PageBody>
        <Section title="Account">
          <FieldGrid
            items={[
              { label: "Email", value: u.email },
              { label: "Phone", value: u.phone, mono: true },
              { label: "Default project", value: labelFor(projects.options, u.default_project_id) },
              { label: "Default site", value: labelFor(sites.options, u.default_site_id) },
              {
                label: "Last sign-in",
                value: u.last_login_at ? formatDateTime(u.last_login_at) : "Never",
              },
              { label: "Created", value: formatDateTime(u.created_at) },
            ]}
          />
        </Section>

        <Section
          title="Role grants"
          actions={
            canAssign &&
            u.status !== "DEACTIVATED" && (
              <Button size="sm" variant="ghost" onClick={() => setDialog("grant")}>
                <Plus /> Grant role
              </Button>
            )
          }
        >
          {grants.isError ? (
            <ErrorState error={grants.error} onRetry={() => void grants.refetch()} />
          ) : active.length === 0 ? (
            <p className="px-4 py-4 text-sm text-fg-muted">
              {grants.isLoading
                ? "Loading…"
                : "No active roles. This user can sign in but cannot see anything."}
            </p>
          ) : (
            <GrantTable
              grants={active}
              scopeLabel={scopeLabel}
              onRevoke={canAssign ? setRevoking : undefined}
            />
          )}
          {revoked.length > 0 && (
            <details className="border-t border-border">
              <summary className="cursor-pointer px-4 py-2 text-xs text-fg-muted">
                {revoked.length} revoked grant{revoked.length === 1 ? "" : "s"}
              </summary>
              <GrantTable grants={revoked} scopeLabel={scopeLabel} />
            </details>
          )}
        </Section>
      </PageBody>

      {dialog === "edit" && <EditUserDialog user={u} onClose={() => setDialog(null)} />}
      {dialog === "grant" && <GrantRoleDialog userId={u.id} onClose={() => setDialog(null)} />}
      <ConfirmDialog
        open={dialog === "deactivate"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`Deactivate ${u.full_name}?`}
        description="They are signed out everywhere immediately and cannot sign in again. Their history is kept."
        confirmLabel={`Deactivate ${u.full_name}`}
        destructive
        reason={{ label: "Reason" }}
        onConfirm={async (reason) => {
          await api.post(`/users/${u.id}/deactivate`, { reason: reason || null });
          await refresh();
          toast.success(`${u.full_name} deactivated.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "reset"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`Reset password for ${u.full_name}?`}
        description={
          u.email
            ? `A single-use link is emailed to ${u.email}. They must choose a new password before continuing.`
            : "This user has no email address, so the link cannot be sent automatically. SMS delivery is not built yet."
        }
        confirmLabel="Send reset link"
        onConfirm={async () => {
          await api.post(`/users/${u.id}/reset-password`);
          await refresh();
          toast.success(u.email ? `Reset link sent to ${u.email}.` : "Reset issued.");
        }}
      />
      {revoking && (
        <ConfirmDialog
          open
          onOpenChange={(o) => !o && setRevoking(null)}
          title={`Revoke ${revoking.role_name ?? "role"}?`}
          description={`${u.full_name} loses this access immediately.`}
          confirmLabel={`Revoke ${revoking.role_code ?? "role"}`}
          destructive
          reason={{ label: "Reason" }}
          onConfirm={async (reason) => {
            await api.post(`/users/role-grants/${revoking.id}/revoke`, { reason: reason || null });
            await queryClient.invalidateQueries({ queryKey: ["users", "grants", u.id] });
            toast.success("Role revoked.");
          }}
        />
      )}
    </>
  );
}

function GrantTable({
  grants,
  scopeLabel,
  onRevoke,
}: {
  grants: RoleGrantRead[];
  scopeLabel: (g: RoleGrantRead) => string | null;
  onRevoke?: (g: RoleGrantRead) => void;
}) {
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Role grants</caption>
      <thead className="bg-surface text-left text-xs text-fg-muted">
        <tr>
          <th scope="col" className="px-4 py-2 font-semibold">
            Role
          </th>
          <th scope="col" className="px-4 py-2 font-semibold">
            Scope
          </th>
          <th scope="col" className="px-4 py-2 font-semibold">
            Valid
          </th>
          <th scope="col" className="px-4 py-2 font-semibold">
            Reason
          </th>
          <th scope="col" className="px-4 py-2" />
        </tr>
      </thead>
      <tbody>
        {grants.map((g) => (
          <tr key={g.id} className="border-t border-border">
            <td className="px-4 py-2 font-medium">{g.role_name ?? g.role_code}</td>
            <td className="px-4 py-2">
              <Tag>{humanize(g.scope_type)}</Tag> <span className="ml-1">{scopeLabel(g)}</span>
            </td>
            <td className="px-4 py-2 text-fg-muted">
              {g.revoked_at
                ? `Revoked ${formatDate(g.revoked_at)}`
                : g.valid_to
                  ? `${formatDate(g.valid_from) || "Now"} – ${formatDate(g.valid_to)}`
                  : "No end date"}
            </td>
            <td className="px-4 py-2 text-fg-muted">{g.grant_reason ?? "—"}</td>
            <td className="px-4 py-2 text-right">
              {onRevoke && (
                <Button size="sm" variant="ghost" onClick={() => onRevoke(g)}>
                  Revoke
                </Button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const editSchema = z.object({
  full_name: z.string().trim().min(2, "At least 2 characters"),
  email: z
    .string()
    .trim()
    .refine((v) => v === "" || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v), "Not a valid email"),
  phone: z.string().trim(),
  default_project_id: z.string(),
  default_site_id: z.string(),
});
type EditValues = z.infer<typeof editSchema>;

function EditUserDialog({ user, onClose }: { user: UserAdminRead; onClose: () => void }) {
  const queryClient = useQueryClient();
  const projects = useProjectOptions();
  const sites = useSiteOptions();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      full_name: user.full_name,
      email: str(user.email),
      phone: str(user.phone),
      default_project_id: str(user.default_project_id),
      default_site_id: str(user.default_site_id),
    },
  });
  const save = useMutation({
    mutationFn: (v: EditValues) =>
      api.patch(`/users/${user.id}`, emptyToNull(dirtyValues(form.formState.dirtyFields, v))),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["users"] });
      toast.success("User updated.");
      onClose();
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, Object.keys(editSchema.shape));
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Edit ${user.full_name}`}
      variant="drawer"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => void form.handleSubmit((v) => save.mutate(v))()}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormGrid>
          <FormField
            label="Full name"
            required
            error={errors.full_name?.message}
            className="sm:col-span-2"
          >
            <Input {...form.register("full_name")} />
          </FormField>
          <FormField label="Email" error={errors.email?.message}>
            <Input type="email" {...form.register("email")} />
          </FormField>
          <FormField label="Phone" error={errors.phone?.message}>
            <Input type="tel" {...form.register("phone")} />
          </FormField>
          <FormField label="Default project">
            <Select {...form.register("default_project_id")}>
              <option value="">None</option>
              {projects.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Default site">
            <Select {...form.register("default_site_id")}>
              <option value="">None</option>
              {sites.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
        </FormGrid>
      </div>
    </Dialog>
  );
}

const grantSchema = z
  .object({
    role_id: z.string().min(1, "Choose a role"),
    scope_type: z.string().min(1, "Choose a scope"),
    scope_id: z.string(),
    valid_from: z.string(),
    valid_to: z.string(),
    reason: z.string(),
  })
  .refine((v) => ["GLOBAL", "COMPANY"].includes(v.scope_type) || v.scope_id, {
    path: ["scope_id"],
    message: "Choose where this role applies",
  })
  .refine((v) => !v.valid_from || !v.valid_to || v.valid_to >= v.valid_from, {
    path: ["valid_to"],
    message: "Ends before it starts",
  });
type GrantValues = z.infer<typeof grantSchema>;

function GrantRoleDialog({ userId, onClose }: { userId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const roles = useRoleOptions();
  const projects = useProjectOptions();
  const sites = useSiteOptions();
  const departments = useDepartmentOptions();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<GrantValues>({
    resolver: zodResolver(grantSchema),
    defaultValues: {
      role_id: "",
      scope_type: "",
      scope_id: "",
      valid_from: "",
      valid_to: "",
      reason: "",
    },
  });
  const roleId = form.watch("role_id");
  const scopeType = form.watch("scope_type");
  const role: RoleRead | undefined = roles.rows.find((r) => r.id === roleId);
  const assignable = roles.rows.filter((r) => r.is_assignable);
  const scopeOptions: Option[] =
    scopeType === "PROJECT"
      ? projects.options
      : scopeType === "SITE"
        ? sites.options
        : scopeType === "DEPARTMENT"
          ? departments.options
          : [];

  const grant = useMutation({
    mutationFn: (v: GrantValues) =>
      api.post(`/users/${userId}/role-grants`, {
        ...emptyToNull(v),
        scope_id: ["GLOBAL", "COMPANY"].includes(v.scope_type) ? null : v.scope_id,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["users", "grants", userId] });
      toast.success("Role granted. The user is notified.");
      onClose();
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "role_id",
        "scope_type",
        "scope_id",
        "valid_from",
        "valid_to",
        "reason",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Grant role"
      description="You can only grant permissions you hold yourself; the server refuses anything more."
      variant="drawer"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={grant.isPending}
            onClick={() => void form.handleSubmit((v) => grant.mutate(v))()}
          >
            Grant role
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormGrid>
          <FormField
            label="Role"
            required
            error={errors.role_id?.message}
            className="sm:col-span-2"
          >
            <Select
              {...form.register("role_id", {
                onChange: () => {
                  form.setValue("scope_type", "");
                  form.setValue("scope_id", "");
                },
              })}
            >
              <option value="">Choose…</option>
              {assignable.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Applies to" required error={errors.scope_type?.message}>
            <Select
              {...form.register("scope_type", { onChange: () => form.setValue("scope_id", "") })}
              disabled={!role}
            >
              <option value="">Choose…</option>
              {(role?.allowed_scope_types ?? []).map((t) => (
                <option key={t} value={t}>
                  {t === "GLOBAL"
                    ? "Everything (global)"
                    : t === "COMPANY"
                      ? "Whole company"
                      : `One ${humanize(t).toLowerCase()}`}
                </option>
              ))}
            </Select>
          </FormField>
          {scopeType && !["GLOBAL", "COMPANY"].includes(scopeType) && (
            <FormField label={humanize(scopeType)} required error={errors.scope_id?.message}>
              <Select {...form.register("scope_id")}>
                <option value="">Choose…</option>
                {scopeOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FormField>
          )}
          <FormField label="Valid from">
            <Input type="date" {...form.register("valid_from")} />
          </FormField>
          <FormField
            label="Valid until"
            hint="Leave empty for no end date"
            error={errors.valid_to?.message}
          >
            <Input type="date" {...form.register("valid_to")} />
          </FormField>
          <FormField label="Reason" className="sm:col-span-2" hint="Kept in the audit log">
            <Textarea rows={2} {...form.register("reason")} />
          </FormField>
        </FormGrid>
      </div>
    </Dialog>
  );
}
