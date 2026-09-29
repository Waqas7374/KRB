import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import { useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input, Select } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { api, type Page } from "@/lib/api";
import { describeError } from "@/lib/errors";
import { useAccountOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { BankAccountCreate, BankAccountEdit, BankAccountRead } from "@/types/models";

export function BankAccountsPage() {
  const canManage = useCan("finance.coa.manage");
  const [editing, setEditing] = useState<BankAccountRead | "new" | null>(null);
  const query = useQuery({
    queryKey: ["bank-accounts"],
    queryFn: () =>
      api.get<Page<BankAccountRead>>("/finance/bank-accounts", { query: { limit: 100 } }),
  });
  const accounts = useAccountOptions();

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  return (
    <>
      <PageHeader
        title="Bank accounts"
        subtitle="The company's own accounts — where a payment's own credit leg comes from. Never a
          vendor's bank details."
        actions={
          <PermissionGate permission="finance.coa.manage">
            <Button variant="primary" onClick={() => setEditing("new")}>
              <Plus /> New bank account
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Bank accounts</caption>
            <thead className="bg-surface text-left text-xs text-fg-muted">
              <tr>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Title
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Account no.
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Bank
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  GL account
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
              {query.data.items.map((account) => (
                <tr key={account.id} className="border-t border-border">
                  <td className="px-4 py-2 font-medium">{account.account_title}</td>
                  <td className="px-4 py-2 font-mono text-xs">{account.account_no}</td>
                  <td className="px-4 py-2">{account.bank_name}</td>
                  <td className="px-4 py-2 font-mono text-xs text-fg-muted">
                    {account.gl_account_code}
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={account.is_active ? "ACTIVE" : "INACTIVE"} />
                  </td>
                  <td className="px-4 py-2">
                    {canManage && (
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Edit ${account.account_title}`}
                        onClick={() => setEditing(account)}
                      >
                        <Pencil />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {query.data.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-fg-muted">
                    No bank accounts yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </PageBody>

      {editing && (
        <BankAccountDialog
          account={editing === "new" ? undefined : editing}
          accountOptions={accounts.options}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

function BankAccountDialog({
  account,
  accountOptions,
  onClose,
}: {
  account?: BankAccountRead;
  accountOptions: { value: string; label: string }[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const editing = Boolean(account);
  const [title, setTitle] = useState(account?.account_title ?? "");
  const [accountNo, setAccountNo] = useState(account?.account_no ?? "");
  const [bankName, setBankName] = useState(account?.bank_name ?? "");
  const [iban, setIban] = useState(account?.iban ?? "");
  const [glAccountId, setGlAccountId] = useState(account?.gl_account_id ?? "");
  const [isActive, setIsActive] = useState(account?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      if (account) {
        const body: BankAccountEdit = {
          account_title: title,
          iban: iban || null,
          bank_name: bankName,
          is_active: isActive,
        };
        return api.patch<BankAccountRead>(`/finance/bank-accounts/${account.id}`, body, {
          ifMatch: String(account.version),
        });
      }
      const body: BankAccountCreate = {
        account_title: title,
        account_no: accountNo,
        bank_name: bankName,
        gl_account_id: glAccountId,
        iban: iban || null,
        currency_code: "PKR",
        opening_balance: "0",
        is_active: true,
      };
      return api.post<BankAccountRead>("/finance/bank-accounts", body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["bank-accounts"] });
      toast.success(editing ? "Bank account updated." : "Bank account created.");
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      variant="drawer"
      title={editing ? "Edit bank account" : "New bank account"}
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
            {editing ? "Save changes" : "Create bank account"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormGrid>
          <FormField label="Title" required className="sm:col-span-2">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          </FormField>
          <FormField label="Account number" required>
            <Input
              value={accountNo}
              disabled={editing}
              onChange={(e) => setAccountNo(e.target.value)}
            />
          </FormField>
          <FormField label="Bank name" required>
            <Input value={bankName} onChange={(e) => setBankName(e.target.value)} />
          </FormField>
          <FormField label="IBAN">
            <Input value={iban} onChange={(e) => setIban(e.target.value)} />
          </FormField>
          <FormField label="GL account" required hint="What a payment from this account credits">
            <Select
              value={glAccountId}
              disabled={editing}
              onChange={(e) => setGlAccountId(e.target.value)}
            >
              <option value="">Choose…</option>
              {accountOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          <label className="flex items-center gap-2 pt-6 text-sm">
            <Checkbox checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
            Active
          </label>
        </FormGrid>

        {error && (
          <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
