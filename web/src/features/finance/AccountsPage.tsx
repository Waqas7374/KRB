import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import { useMemo, useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { codeField } from "@/lib/forms";
import { describeError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { humanize } from "@/lib/utils";
import type {
  AccountCreate,
  AccountEdit,
  AccountNodeRead,
  AccountRead,
  AccountType,
} from "@/types/models";

const ACCOUNT_TYPES: AccountType[] = [
  "ASSET",
  "LIABILITY",
  "EQUITY",
  "REVENUE",
  "EXPENSE",
  "COGS_DEV_COST",
];

interface FlatRow {
  account: AccountRead;
  depth: number;
}

function flatten(nodes: AccountNodeRead[], depth = 0): FlatRow[] {
  return nodes.flatMap((n) => [{ account: n.account, depth }, ...flatten(n.children, depth + 1)]);
}

/** Every account, code first, for the parent picker — a new account may be
 * attached under any existing one, which then becomes a group automatically. */
function flatAll(nodes: AccountNodeRead[]): AccountRead[] {
  return flatten(nodes).map((r) => r.account);
}

export function AccountsPage() {
  const canManage = useCan("finance.coa.manage");
  const [editing, setEditing] = useState<AccountRead | "new" | null>(null);
  const tree = useQuery({
    queryKey: ["finance-accounts", "tree"],
    queryFn: () => api.get<AccountNodeRead[]>("/finance/accounts/tree"),
  });

  const rows = useMemo(() => (tree.data ? flatten(tree.data) : []), [tree.data]);
  const allAccounts = useMemo(() => (tree.data ? flatAll(tree.data) : []), [tree.data]);

  if (tree.isLoading) return <PageSkeleton />;
  if (tree.error) return <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />;

  return (
    <>
      <PageHeader
        title="Chart of accounts"
        subtitle="Every account the ledger can post to, and the groups that add them up. A group has no
          postings of its own — attach a child under it and it becomes one automatically."
        actions={
          <PermissionGate permission="finance.coa.manage">
            <Button variant="primary" onClick={() => setEditing("new")}>
              <Plus /> New account
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Chart of accounts</caption>
            <thead className="bg-surface text-left text-xs text-fg-muted">
              <tr>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Code
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Name
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Type
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Kind
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  State
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ account, depth }) => (
                <tr key={account.id} className="border-t border-border">
                  <td className="px-4 py-2 font-mono text-xs text-fg-muted">{account.code}</td>
                  <td className="px-4 py-2" style={{ paddingLeft: `${1 + depth * 1.5}rem` }}>
                    <span className={depth === 0 || !account.is_postable ? "font-semibold" : ""}>
                      {account.name}
                    </span>
                    {(account.requires_project || account.requires_cost_center) && (
                      <span className="ml-2 inline-flex gap-1">
                        {account.requires_project && <Tag>Needs project</Tag>}
                        {account.requires_cost_center && <Tag>Needs cost centre</Tag>}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-fg-muted">{humanize(account.account_type)}</td>
                  <td className="px-4 py-2">
                    {account.is_postable ? (
                      <Tag>Postable · {account.normal_balance === "DR" ? "Debit" : "Credit"}</Tag>
                    ) : (
                      <Tag>Group</Tag>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={account.is_active ? "ACTIVE" : "INACTIVE"} />
                  </td>
                  <td className="px-4 py-2">
                    {canManage && (
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Edit ${account.name}`}
                        onClick={() => setEditing(account)}
                      >
                        <Pencil />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </PageBody>

      {editing && (
        <AccountDialog
          account={editing === "new" ? undefined : editing}
          parentOptions={allAccounts}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

function AccountDialog({
  account,
  parentOptions,
  onClose,
}: {
  account?: AccountRead;
  parentOptions: AccountRead[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const editing = Boolean(account);
  const [code, setCode] = useState(account?.code ?? "");
  const [name, setName] = useState(account?.name ?? "");
  const [accountType, setAccountType] = useState<AccountType>(
    (account?.account_type as AccountType) ?? "ASSET",
  );
  const [parentId, setParentId] = useState(account?.parent_id ?? "");
  const [requiresProject, setRequiresProject] = useState(account?.requires_project ?? false);
  const [requiresCostCenter, setRequiresCostCenter] = useState(
    account?.requires_cost_center ?? false,
  );
  const [isActive, setIsActive] = useState(account?.is_active ?? true);
  const [description, setDescription] = useState(account?.description ?? "");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      if (account) {
        const body: AccountEdit = {
          name,
          description: description.trim() || null,
          requires_project: requiresProject,
          requires_cost_center: requiresCostCenter,
          is_active: isActive,
        };
        return api.patch<AccountRead>(`/finance/accounts/${account.id}`, body, {
          ifMatch: String(account.version),
        });
      }
      const parsed = codeField.safeParse(code);
      if (!parsed.success) throw new Error(parsed.error.issues[0]?.message ?? "Invalid code");
      const body: AccountCreate = {
        code: parsed.data,
        name,
        account_type: accountType,
        parent_id: parentId || null,
        requires_project: requiresProject,
        requires_cost_center: requiresCostCenter,
        description: description.trim() || null,
      };
      return api.post<AccountRead>("/finance/accounts", body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["finance-accounts"] });
      toast.success(editing ? "Account updated." : "Account created.");
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      variant="drawer"
      title={editing ? `Edit ${account?.code}` : "New account"}
      description={
        editing
          ? "Code, type and parent are fixed once created."
          : "Attach it under an existing account and that one becomes a group."
      }
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => {
              setError(null);
              save.mutate();
            }}
          >
            {editing ? "Save changes" : "Create account"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormGrid>
          {!editing && (
            <FormField label="Code" required hint="e.g. 1150">
              <Input className="uppercase" value={code} onChange={(e) => setCode(e.target.value)} />
            </FormField>
          )}
          <FormField label="Name" required className={editing ? "sm:col-span-2" : undefined}>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </FormField>
          {!editing && (
            <>
              <FormField label="Type" required>
                <Select
                  value={accountType}
                  onChange={(e) => setAccountType(e.target.value as AccountType)}
                >
                  {ACCOUNT_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {humanize(t)}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Parent" hint="Leave blank for a top-level account.">
                <Select value={parentId} onChange={(e) => setParentId(e.target.value)}>
                  <option value="">None — top level</option>
                  {parentOptions.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.code} — {p.name}
                    </option>
                  ))}
                </Select>
              </FormField>
            </>
          )}
        </FormGrid>

        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={requiresProject}
            onChange={(e) => setRequiresProject(e.target.checked)}
          />
          Every line on this account needs a project
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={requiresCostCenter}
            onChange={(e) => setRequiresCostCenter(e.target.checked)}
          />
          Every line on this account needs a cost centre
        </label>
        {editing && (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
            Active
          </label>
        )}

        <FormField label="Description">
          <Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormField>

        {error && (
          <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
