import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { fetchAllPages } from "@/components/data-table/export";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Input, Select } from "@/components/ui/input";
import { StatusBadge } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api } from "@/lib/api";
import { userStatusOptions } from "@/lib/enums";
import { applyServerErrors } from "@/lib/errors";
import { emptyToNull } from "@/lib/forms";
import { usePagedList, useProjectOptions, useSiteOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDateTime } from "@/lib/utils";
import type { UserAdminRead, UserInviteResponse } from "@/types/models";

const col = createColumnHelper<UserAdminRead>();

const columns = [
  col.accessor("full_name", {
    header: "Name",
    meta: { sortKey: "full_name", alwaysVisible: true },
    cell: (c) => (
      <Link to={`/users/${c.row.original.id}`} className="font-medium text-primary hover:underline">
        {c.getValue()}
      </Link>
    ),
  }),
  col.accessor("email", {
    header: "Email",
    meta: { sortKey: "email" },
    cell: (c) => c.getValue() ?? "—",
  }),
  col.accessor("phone", { header: "Phone", cell: (c) => c.getValue() ?? "—" }),
  col.accessor("status", {
    header: "Status",
    meta: { sortKey: "status" },
    cell: (c) => <StatusBadge status={c.getValue()} />,
  }),
  col.accessor("must_change_password", {
    header: "Temp. password",
    cell: (c) => (c.getValue() ? "Yes" : "—"),
  }),
  col.accessor("last_login_at", {
    header: "Last sign-in",
    meta: { sortKey: "last_login_at" },
    cell: (c) => (c.getValue() ? formatDateTime(c.getValue()) : "Never"),
  }),
];

export function UsersListPage() {
  const list = useListParams({ sort: "full_name" });
  const query = usePagedList<UserAdminRead>("users", "/users", list.query);
  const [inviting, setInviting] = useState(false);

  return (
    <>
      <PageHeader
        title="Users"
        subtitle="People who can sign in. Access comes from role grants on each user's page."
        actions={
          <PermissionGate permission="users.create">
            <Button variant="primary" onClick={() => setInviting(true)}>
              <UserPlus /> Invite user
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="users"
          caption="Users"
          columns={columns}
          rows={query.data?.items}
          total={query.data?.page.total}
          isLoading={query.isLoading}
          isFetching={query.isFetching}
          error={query.error}
          onRetry={() => void query.refetch()}
          sort={list.params.sort}
          onSortChange={list.setSort}
          offset={list.params.offset}
          limit={list.params.limit}
          onOffsetChange={list.setOffset}
          onLimitChange={list.setLimit}
          getRowId={(r) => r.id}
          getRowHref={(r) => `/users/${r.id}`}
          exportRows={() => fetchAllPages<UserAdminRead>("/users", list.query)}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Name, email or phone…  ( / )"
              savedViewsId="users"
              filters={[{ key: "status", label: "Status", options: userStatusOptions }]}
            />
          }
        />
      </PageBody>
      {inviting && <InviteDialog onClose={() => setInviting(false)} />}
    </>
  );
}

const inviteSchema = z
  .object({
    full_name: z.string().trim().min(2, "At least 2 characters"),
    email: z
      .string()
      .trim()
      .refine((v) => v === "" || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v), "Not a valid email"),
    phone: z.string().trim(),
    default_project_id: z.string(),
    default_site_id: z.string(),
  })
  .refine((v) => v.email || v.phone, {
    path: ["email"],
    message: "An email or a phone number is required to sign in",
  });
type InviteValues = z.infer<typeof inviteSchema>;

function InviteDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: {
      full_name: "",
      email: "",
      phone: "",
      default_project_id: "",
      default_site_id: "",
    },
  });
  const projects = useProjectOptions();
  const sites = useSiteOptions(form.watch("default_project_id") || undefined);

  const invite = useMutation({
    mutationFn: (v: InviteValues) => api.post<UserInviteResponse>("/users", emptyToNull(v)),
    onSuccess: async (res) => {
      await queryClient.invalidateQueries({ queryKey: ["users"] });
      toast.success(res.message ?? `Invited ${res.user.full_name}.`);
      navigate(`/users/${res.user.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "full_name",
        "email",
        "phone",
        "default_project_id",
        "default_site_id",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Invite user"
      description="An email with a link to choose a password is sent to the user (valid 72 hours). Phone-only users get no email yet — use Reset password later and relay the link. Grant roles on the next screen."
      variant="drawer"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={invite.isPending}
            onClick={() => void form.handleSubmit((v) => invite.mutate(v))()}
          >
            Invite
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
            <Input {...form.register("full_name")} autoFocus />
          </FormField>
          <FormField label="Email" error={errors.email?.message}>
            <Input type="email" {...form.register("email")} />
          </FormField>
          <FormField
            label="Mobile"
            hint="Site staff usually sign in by phone"
            error={errors.phone?.message}
          >
            <Input type="tel" placeholder="0300-1234567" {...form.register("phone")} />
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
