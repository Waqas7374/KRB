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
import { toast } from "@/lib/toast";
import { humanize } from "@/lib/utils";
import type { TaxCodeCreate, TaxCodeEdit, TaxCodeRead } from "@/types/models";

const TAX_TYPES = ["SALES_TAX", "WITHHOLDING"];
const APPLIES_TO = ["GOODS", "SERVICES", "PAYMENT"];

export function TaxCodesPage() {
  const canManage = useCan("finance.coa.manage");
  const [editing, setEditing] = useState<TaxCodeRead | "new" | null>(null);
  const query = useQuery({
    queryKey: ["tax-codes"],
    queryFn: () => api.get<Page<TaxCodeRead>>("/finance/tax-codes", { query: { limit: 100 } }),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  return (
    <>
      <PageHeader
        title="Tax codes"
        subtitle="One rate, of one kind, on one thing — a sales-tax code prices an invoice line; a
          withholding code, with its own section, tells a payment how much to hold back."
        actions={
          <PermissionGate permission="finance.coa.manage">
            <Button variant="primary" onClick={() => setEditing("new")}>
              <Plus /> New tax code
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Tax codes</caption>
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
                  Applies to
                </th>
                <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                  Rate
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Section
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
              {query.data.items.map((code) => (
                <tr key={code.id} className="border-t border-border">
                  <td className="px-4 py-2 font-mono text-xs">{code.code}</td>
                  <td className="px-4 py-2">{code.name}</td>
                  <td className="px-4 py-2">{humanize(code.tax_type)}</td>
                  <td className="px-4 py-2 text-fg-muted">{humanize(code.applies_to)}</td>
                  <td data-numeric className="px-4 py-2 tabular">
                    {Number(code.rate_pct).toFixed(2)}%
                  </td>
                  <td className="px-4 py-2 text-xs text-fg-muted">{code.section_code ?? "—"}</td>
                  <td className="px-4 py-2">
                    <StatusBadge status={code.is_active ? "ACTIVE" : "INACTIVE"} />
                  </td>
                  <td className="px-4 py-2">
                    {canManage && (
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Edit ${code.name}`}
                        onClick={() => setEditing(code)}
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
                    No tax codes yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </PageBody>

      {editing && (
        <TaxCodeDialog
          code={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

function TaxCodeDialog({ code, onClose }: { code?: TaxCodeRead; onClose: () => void }) {
  const queryClient = useQueryClient();
  const editing = Boolean(code);
  const [codeValue, setCodeValue] = useState(code?.code ?? "");
  const [name, setName] = useState(code?.name ?? "");
  const [taxType, setTaxType] = useState(code?.tax_type ?? "SALES_TAX");
  const [appliesTo, setAppliesTo] = useState(code?.applies_to ?? "GOODS");
  const [ratePct, setRatePct] = useState(code?.rate_pct ?? "");
  const [sectionCode, setSectionCode] = useState(code?.section_code ?? "");
  const [isActive, setIsActive] = useState(code?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      if (code) {
        const body: TaxCodeEdit = {
          name,
          rate_pct: ratePct,
          section_code: sectionCode || null,
          is_active: isActive,
        };
        return api.patch<TaxCodeRead>(`/finance/tax-codes/${code.id}`, body, {
          ifMatch: String(code.version),
        });
      }
      const body: TaxCodeCreate = {
        code: codeValue,
        name,
        tax_type: taxType as TaxCodeCreate["tax_type"],
        applies_to: appliesTo as TaxCodeCreate["applies_to"],
        rate_pct: ratePct,
        section_code: sectionCode || null,
        is_active: isActive,
      };
      return api.post<TaxCodeRead>("/finance/tax-codes", body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["tax-codes"] });
      toast.success(editing ? "Tax code updated." : "Tax code created.");
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      variant="drawer"
      title={editing ? "Edit tax code" : "New tax code"}
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
            {editing ? "Save changes" : "Create tax code"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormGrid>
          <FormField label="Code" required>
            <Input
              value={codeValue}
              disabled={editing}
              onChange={(e) => setCodeValue(e.target.value.toUpperCase())}
            />
          </FormField>
          <FormField label="Name" required className="sm:col-span-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </FormField>
          <FormField label="Type" required>
            <Select value={taxType} disabled={editing} onChange={(e) => setTaxType(e.target.value)}>
              {TAX_TYPES.map((t) => (
                <option key={t} value={t}>
                  {humanize(t)}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Applies to" required>
            <Select
              value={appliesTo}
              disabled={editing}
              onChange={(e) => setAppliesTo(e.target.value)}
            >
              {APPLIES_TO.map((a) => (
                <option key={a} value={a}>
                  {humanize(a)}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Rate %" required>
            <Input
              inputMode="decimal"
              className="tabular"
              value={ratePct}
              onChange={(e) => setRatePct(e.target.value)}
            />
          </FormField>
          <FormField label="Section" hint="Withholding only, e.g. 153(1)(a)">
            <Input value={sectionCode} onChange={(e) => setSectionCode(e.target.value)} />
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
