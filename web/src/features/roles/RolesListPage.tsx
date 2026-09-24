import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input, Textarea } from "@/components/ui/input";
import { Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { api } from "@/lib/api";
import { SCOPE_TYPES } from "@/lib/enums";
import { applyServerErrors } from "@/lib/errors";
import { codeField, emptyToNull } from "@/lib/forms";
import { usePagedList } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { humanize } from "@/lib/utils";
import type { RoleRead } from "@/types/models";

const col = createColumnHelper<RoleRead>();

const columns = [
  col.accessor("name", {
    header: "Role",
    meta: { sortKey: "name", alwaysVisible: true },
    cell: (c) => (
      <Link to={`/roles/${c.row.original.id}`} className="font-medium text-primary hover:underline">
        {c.getValue()}
      </Link>
    ),
  }),
  col.accessor("code", {
    header: "Code",
    meta: { sortKey: "code" },
    cell: (c) => <span className="font-mono">{c.getValue()}</span>,
  }),
  col.accessor("allowed_scope_types", {
    header: "Can apply to",
    meta: { csv: (r) => r.allowed_scope_types.join(" ") },
    cell: (c) => c.getValue().map(humanize).join(", "),
  }),
  col.display({
    id: "flags",
    header: "Flags",
    meta: {
      csv: (r) =>
        [r.is_system && "system", r.is_locked && "locked", r.is_read_only && "read-only"]
          .filter(Boolean)
          .join(" "),
    },
    cell: (c) => {
      const r = c.row.original;
      return (
        <span className="flex gap-1">
          {r.is_system && <Tag>System</Tag>}
          {r.is_locked && <Tag>Locked</Tag>}
          {r.is_read_only && <Tag>Read-only</Tag>}
          {!r.is_assignable && <Tag>Not assignable</Tag>}
        </span>
      );
    },
  }),
  col.accessor("description", {
    header: "Description",
    cell: (c) => <span className="text-fg-muted">{c.getValue() ?? ""}</span>,
  }),
];

export function RolesListPage() {
  const list = useListParams({ sort: "name" });
  const query = usePagedList<RoleRead>("roles", "/roles", list.query);
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Roles"
        subtitle="A role is a named set of permissions. Where it applies is chosen when it is granted to a user."
        actions={
          <PermissionGate permission="roles.manage">
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus /> New role
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <DataTable
          tableId="roles"
          caption="Roles"
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
          getRowHref={(r) => `/roles/${r.id}`}
          toolbar={<FilterBar list={list} />}
        />
      </PageBody>
      {creating && <CreateRoleDialog onClose={() => setCreating(false)} />}
    </>
  );
}

const schema = z.object({
  code: codeField,
  name: z.string().trim().min(2, "At least 2 characters"),
  description: z.string(),
  allowed_scope_types: z.array(z.string()).min(1, "Choose at least one"),
});
type Values = z.infer<typeof schema>;

function CreateRoleDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      code: "",
      name: "",
      description: "",
      allowed_scope_types: ["COMPANY", "PROJECT", "SITE"],
    },
  });
  const create = useMutation({
    mutationFn: (v: Values) => api.post<RoleRead>("/roles", emptyToNull(v)),
    onSuccess: async (role) => {
      await queryClient.invalidateQueries({ queryKey: ["roles"] });
      toast.success(`Role ${role.name} created. Now choose its permissions.`);
      navigate(`/roles/${role.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, Object.keys(schema.shape));
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New role"
      variant="drawer"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={create.isPending}
            onClick={() => void form.handleSubmit((v) => create.mutate(v))()}
          >
            Create role
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormGrid>
          <FormField label="Code" required error={errors.code?.message}>
            <Input {...form.register("code")} className="font-mono uppercase" autoFocus />
          </FormField>
          <FormField label="Name" required error={errors.name?.message}>
            <Input {...form.register("name")} />
          </FormField>
          <FormField label="Description" className="sm:col-span-2">
            <Textarea rows={2} {...form.register("description")} />
          </FormField>
        </FormGrid>
        <fieldset>
          <legend className="text-xs font-medium text-fg-muted">Can be granted at</legend>
          <div className="mt-1 flex flex-wrap gap-4">
            {SCOPE_TYPES.map((t) => (
              <label key={t} className="flex items-center gap-2 text-sm">
                <Checkbox value={t} {...form.register("allowed_scope_types")} /> {humanize(t)}
              </label>
            ))}
          </div>
          {errors.allowed_scope_types && (
            <p className="mt-1 text-xs text-danger">{errors.allowed_scope_types.message}</p>
          )}
        </fieldset>
      </div>
    </Dialog>
  );
}
