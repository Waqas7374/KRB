import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import { useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { api, type Page } from "@/lib/api";
import { describeError } from "@/lib/errors";
import { useAccountOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { humanize } from "@/lib/utils";
import type { PostingRuleCreate, PostingRuleEdit, PostingRuleRead } from "@/types/models";

const SOURCE_TYPES = [
  "MANUAL",
  "GRN",
  "INVENTORY",
  "INVOICE",
  "PAYMENT",
  "PAYROLL",
  "DEPRECIATION",
  "OPENING_BALANCE",
];

export function PostingRulesPage() {
  const canManage = useCan("finance.coa.manage");
  const [editing, setEditing] = useState<PostingRuleRead | "new" | null>(null);
  const query = useQuery({
    queryKey: ["posting-rules"],
    queryFn: () =>
      api.get<Page<PostingRuleRead>>("/finance/posting-rules", { query: { limit: 100 } }),
  });
  const accounts = useAccountOptions();

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  return (
    <>
      <PageHeader
        title="Posting rules"
        subtitle="Which accounts a receipt hits, as configuration — an accountant can change which account
          a GRN accrual lands on without a code change."
        actions={
          <PermissionGate permission="finance.coa.manage">
            <Button variant="primary" onClick={() => setEditing("new")}>
              <Plus /> New rule
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Posting rules</caption>
            <thead className="bg-surface text-left text-xs text-fg-muted">
              <tr>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Source
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Event
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Rule
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Debit
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Credit
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Priority
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
              {query.data.items.map((rule) => (
                <tr key={rule.id} className="border-t border-border">
                  <td className="px-4 py-2">{humanize(rule.source_type)}</td>
                  <td className="px-4 py-2 font-mono text-xs text-fg-muted">{rule.event}</td>
                  <td className="px-4 py-2">
                    {rule.name ?? <span className="text-fg-subtle">—</span>}
                    {rule.condition != null && (
                      <p className="max-w-sm truncate text-xs text-fg-muted">
                        {JSON.stringify(rule.condition)}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{rule.debit_account_code}</td>
                  <td className="px-4 py-2 font-mono text-xs">{rule.credit_account_code}</td>
                  <td className="px-4 py-2 text-fg-muted">{rule.priority}</td>
                  <td className="px-4 py-2">
                    <StatusBadge status={rule.is_active ? "ACTIVE" : "INACTIVE"} />
                  </td>
                  <td className="px-4 py-2">
                    {canManage && (
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Edit ${rule.name ?? rule.event}`}
                        onClick={() => setEditing(rule)}
                      >
                        <Pencil />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {query.data.items.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-6 text-center text-fg-muted">
                    No posting rules yet — nothing can post until at least one exists for its source
                    and event.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </PageBody>

      {editing && (
        <RuleDialog
          rule={editing === "new" ? undefined : editing}
          accountOptions={accounts.options}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

function RuleDialog({
  rule,
  accountOptions,
  onClose,
}: {
  rule?: PostingRuleRead;
  accountOptions: { value: string; label: string }[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const editing = Boolean(rule);
  const [sourceType, setSourceType] = useState(rule?.source_type ?? "GRN");
  const [event, setEvent] = useState(rule?.event ?? "RECEIPT");
  const [name, setName] = useState(rule?.name ?? "");
  const [condition, setCondition] = useState(
    rule?.condition != null ? JSON.stringify(rule.condition, null, 2) : "",
  );
  const [debitAccountId, setDebitAccountId] = useState(rule?.debit_account_id ?? "");
  const [creditAccountId, setCreditAccountId] = useState(rule?.credit_account_id ?? "");
  const [priority, setPriority] = useState(String(rule?.priority ?? 0));
  const [isActive, setIsActive] = useState(rule?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      let parsedCondition: unknown = null;
      if (condition.trim()) {
        try {
          parsedCondition = JSON.parse(condition);
        } catch {
          throw new Error("The condition must be valid JSON, or left empty to always match.");
        }
      }
      if (rule) {
        const body: PostingRuleEdit = {
          name: name || null,
          condition: parsedCondition,
          debit_account_id: debitAccountId,
          credit_account_id: creditAccountId,
          priority: Number(priority),
          is_active: isActive,
        };
        return api.patch<PostingRuleRead>(`/finance/posting-rules/${rule.id}`, body, {
          ifMatch: String(rule.version),
        });
      }
      const body: PostingRuleCreate = {
        source_type: sourceType as PostingRuleCreate["source_type"],
        event,
        name: name || null,
        condition: parsedCondition,
        debit_account_id: debitAccountId,
        credit_account_id: creditAccountId,
        priority: Number(priority),
        is_active: isActive,
      };
      return api.post<PostingRuleRead>("/finance/posting-rules", body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["posting-rules"] });
      toast.success(editing ? "Rule updated." : "Rule created.");
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      variant="drawer"
      title={editing ? "Edit posting rule" : "New posting rule"}
      description="Whichever active rule for this source and event matches its condition, at the highest
        priority, decides the accounts. A rule with no condition always matches."
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
            {editing ? "Save changes" : "Create rule"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormGrid>
          <FormField label="Source" required>
            <Select
              value={sourceType}
              disabled={editing}
              onChange={(e) => setSourceType(e.target.value)}
            >
              {SOURCE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {humanize(t)}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Event" required hint="e.g. RECEIPT">
            <Input value={event} disabled={editing} onChange={(e) => setEvent(e.target.value)} />
          </FormField>
          <FormField label="Name" className="sm:col-span-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </FormField>
          <FormField label="Debit account" required>
            <Select value={debitAccountId} onChange={(e) => setDebitAccountId(e.target.value)}>
              <option value="">Choose…</option>
              {accountOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Credit account" required>
            <Select value={creditAccountId} onChange={(e) => setCreditAccountId(e.target.value)}>
              <option value="">Choose…</option>
              {accountOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Priority" hint="Breaks a tie between equally matching rules.">
            <Input
              inputMode="numeric"
              className="tabular"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            />
          </FormField>
          <label className="flex items-center gap-2 pt-6 text-sm">
            <Checkbox checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
            Active
          </label>
        </FormGrid>

        <FormField
          label="Condition (advanced)"
          hint='Optional JSON, e.g. {"==": [{"var": "is_stockable"}, true]}. Left blank, always matches.'
        >
          <Textarea
            rows={4}
            className="font-mono text-xs"
            value={condition}
            onChange={(e) => setCondition(e.target.value)}
          />
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
