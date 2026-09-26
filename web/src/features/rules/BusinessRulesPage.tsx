import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Pencil, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { describeError } from "@/lib/errors";
import {
  type Option,
  useCategoryOptions,
  useMaterialOptions,
  usePagedList,
  useProjectOptions,
  useRoleOptions,
  useSiteOptions,
  useTruckTypeOptions,
  useVendorOptions,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate } from "@/lib/utils";
import type {
  ApprovalDocumentType,
  BusinessRuleRead,
  BusinessRuleType,
  RuleResolution,
} from "@/types/models";

import { describeValue, scalar, SCOPE_LABELS, SCOPE_ORDER } from "./rule-format";

const today = () => new Date().toISOString().slice(0, 10);

/** One place to turn a scope key into its picker options. */
function useScopeOptions(): Record<string, Option[]> {
  const projects = useProjectOptions();
  const sites = useSiteOptions();
  const materials = useMaterialOptions();
  const categories = useCategoryOptions();
  const vendors = useVendorOptions();
  const trucks = useTruckTypeOptions();
  const roles = useRoleOptions();
  const docTypes = useQuery({
    queryKey: ["approval-workflows", "document-types"],
    queryFn: () => api.get<ApprovalDocumentType[]>("/approval-workflows/document-types"),
    staleTime: 5 * 60_000,
  });
  return {
    project_id: projects.options,
    site_id: sites.options,
    material_id: materials.options,
    material_category_id: categories.options,
    vendor_id: vendors.options,
    truck_type_id: trucks.options,
    role_id: roles.options,
    doc_type: (docTypes.data ?? []).map((d) => ({ value: d.doc_type, label: d.label })),
  };
}

function useRuleTypes() {
  return useQuery({
    queryKey: ["business-rules", "types"],
    queryFn: () => api.get<BusinessRuleType[]>("/business-rules/types"),
    staleTime: 10 * 60_000,
  });
}

// --- List -----------------------------------------------------------------------

const col = createColumnHelper<BusinessRuleRead>();

export function BusinessRulesPage() {
  const list = useListParams({ sort: "rule_type" });
  const query = usePagedList<BusinessRuleRead>("business-rules", "/business-rules", list.query);
  const types = useRuleTypes();
  const scopeOptions = useScopeOptions();
  const canManage = useCan("settings.manage_rules");
  const [editing, setEditing] = useState<BusinessRuleRead | "new" | null>(null);
  const [resolving, setResolving] = useState(false);

  const columns = useMemo(
    () => [
      col.accessor("rule_type_label", {
        header: "Rule",
        meta: { sortKey: "rule_type", alwaysVisible: true },
        cell: (c) => (
          <div>
            <span className="font-medium">{c.getValue() ?? c.row.original.rule_type}</span>
            {c.row.original.name && <p className="text-xs text-fg-muted">{c.row.original.name}</p>}
          </div>
        ),
      }),
      col.display({
        id: "value",
        header: "Value",
        cell: (c) => describeValue(c.row.original.rule_type, c.row.original.value),
      }),
      col.display({
        id: "scope",
        header: "Applies to",
        cell: (c) => {
          const scope = Object.entries(c.row.original.scope as Record<string, string>).sort(
            ([a], [b]) => SCOPE_ORDER.indexOf(a) - SCOPE_ORDER.indexOf(b),
          );
          if (scope.length === 0) return <Tag>Everything (company default)</Tag>;
          return (
            <span className="flex flex-wrap gap-1">
              {scope.map(([key, id]) => (
                <Tag key={key}>
                  {SCOPE_LABELS[key] ?? key}:{" "}
                  {scopeOptions[key]?.find((o) => o.value === id)?.label ?? id.slice(0, 8)}
                </Tag>
              ))}
            </span>
          );
        },
      }),
      col.accessor("priority", {
        header: "Priority",
        meta: { numeric: true, sortKey: "priority" },
      }),
      col.display({
        id: "period",
        header: "Effective",
        cell: (c) =>
          `${formatDate(c.row.original.effective_from)} → ${
            c.row.original.effective_to ? formatDate(c.row.original.effective_to) : "open"
          }`,
      }),
      col.accessor("is_active", {
        header: "State",
        cell: (c) => <StatusBadge status={c.getValue() ? "ACTIVE" : "INACTIVE"} />,
      }),
      col.display({
        id: "actions",
        header: () => <span className="sr-only">Actions</span>,
        meta: { alwaysVisible: true },
        cell: (c) =>
          canManage ? (
            <Button
              size="icon"
              variant="ghost"
              aria-label={`Edit ${c.row.original.name ?? c.row.original.rule_type}`}
              onClick={() => setEditing(c.row.original)}
            >
              <Pencil />
            </Button>
          ) : null,
      }),
    ],
    [scopeOptions, canManage],
  );

  return (
    <>
      <PageHeader
        title="Business rules"
        subtitle="Every configurable threshold — tonnage, geofence, tolerances, approval limits — in one place. The most specific active rule wins."
        actions={
          <>
            <Button onClick={() => setResolving(true)}>
              <Search /> Which rule applies?
            </Button>
            <PermissionGate permission="settings.manage_rules">
              <Button variant="primary" onClick={() => setEditing("new")}>
                <Plus /> New rule
              </Button>
            </PermissionGate>
          </>
        }
      />
      <PageBody>
        <DataTable
          tableId="business-rules"
          caption="Business rules"
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
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Name or description…  ( / )"
              savedViewsId="business-rules"
              filters={[
                {
                  key: "rule_type",
                  label: "Type",
                  options: (types.data ?? []).map((t) => ({ value: t.rule_type, label: t.label })),
                },
                {
                  key: "is_active",
                  label: "State",
                  options: [
                    { value: "true", label: "Active" },
                    { value: "false", label: "Inactive" },
                  ],
                },
              ]}
            />
          }
        />
      </PageBody>

      {editing && (
        <RuleDialog
          key={editing === "new" ? "new" : editing.id}
          rule={editing === "new" ? undefined : editing}
          types={types.data ?? []}
          scopeOptions={scopeOptions}
          onClose={() => setEditing(null)}
        />
      )}
      {resolving && (
        <ResolveDialog
          types={types.data ?? []}
          scopeOptions={scopeOptions}
          onClose={() => setResolving(false)}
        />
      )}
    </>
  );
}

// --- Create / edit --------------------------------------------------------------

interface FormState {
  rule_type: string;
  name: string;
  description: string;
  scope: Record<string, string>;
  value: Record<string, string>;
  condition: string;
  priority: string;
  effective_from: string;
  effective_to: string;
  is_active: boolean;
}

function valueFields(type: BusinessRuleType | undefined): string[] {
  const properties = (type?.value_schema as { properties?: Record<string, unknown> } | undefined)
    ?.properties;
  return properties ? Object.keys(properties) : Object.keys(type?.example ?? {});
}

function initial(rule: BusinessRuleRead | undefined, types: BusinessRuleType[]): FormState {
  if (rule) {
    return {
      rule_type: rule.rule_type,
      name: rule.name ?? "",
      description: rule.description ?? "",
      scope: rule.scope as Record<string, string>,
      value: Object.fromEntries(Object.entries(rule.value).map(([k, v]) => [k, scalar(v)])),
      condition: rule.condition == null ? "" : JSON.stringify(rule.condition, null, 2),
      priority: String(rule.priority),
      effective_from: rule.effective_from,
      effective_to: rule.effective_to ?? "",
      is_active: rule.is_active,
    };
  }
  const first = types[0];
  return {
    rule_type: first?.rule_type ?? "",
    name: "",
    description: "",
    scope: {},
    value: Object.fromEntries(Object.entries(first?.example ?? {}).map(([k, v]) => [k, String(v)])),
    condition: "",
    priority: "0",
    effective_from: today(),
    effective_to: "",
    is_active: true,
  };
}

function RuleDialog({
  rule,
  types,
  scopeOptions,
  onClose,
}: {
  rule?: BusinessRuleRead;
  types: BusinessRuleType[];
  scopeOptions: Record<string, Option[]>;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(() => initial(rule, types));
  const [error, setError] = useState<string | null>(null);
  const type = types.find((t) => t.rule_type === form.rule_type);
  const editing = Boolean(rule);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const changeType = (rule_type: string) => {
    const next = types.find((t) => t.rule_type === rule_type);
    setForm((f) => ({
      ...f,
      rule_type,
      scope: {},
      value: Object.fromEntries(
        Object.entries(next?.example ?? {}).map(([k, v]) => [k, String(v)]),
      ),
    }));
  };

  const save = useMutation({
    mutationFn: () => {
      let condition: unknown = null;
      if (form.condition.trim()) {
        try {
          condition = JSON.parse(form.condition);
        } catch {
          throw new Error("The condition must be valid JSON, or left empty.");
        }
      }
      const value = Object.fromEntries(
        Object.entries(form.value).filter(([, v]) => v.trim() !== ""),
      );
      if (rule) {
        return api.patch<BusinessRuleRead>(
          `/business-rules/${rule.id}`,
          {
            name: form.name || null,
            description: form.description || null,
            value,
            condition,
            priority: Number(form.priority),
            effective_to: form.effective_to || null,
            is_active: form.is_active,
          },
          { ifMatch: String(rule.version) },
        );
      }
      return api.post<BusinessRuleRead>("/business-rules", {
        rule_type: form.rule_type,
        name: form.name || null,
        description: form.description || null,
        scope: Object.fromEntries(Object.entries(form.scope).filter(([, v]) => v)),
        value,
        condition,
        priority: Number(form.priority),
        effective_from: form.effective_from,
        effective_to: form.effective_to || null,
        is_active: form.is_active,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["business-rules"] });
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
      title={editing ? "Edit rule" : "New rule"}
      description={
        editing
          ? "The type, scope and start date are fixed. To change what a rule applies to, end it and create another."
          : type?.description
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
            {editing ? "Save changes" : "Create rule"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormField label="Rule type" required>
          <Select
            value={form.rule_type}
            disabled={editing}
            onChange={(e) => changeType(e.target.value)}
          >
            {types.map((t) => (
              <option key={t.rule_type} value={t.rule_type}>
                {t.label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Name">
          <Input value={form.name} onChange={(e) => set("name", e.target.value)} />
        </FormField>

        <fieldset className="flex flex-col gap-2 rounded-md border border-border p-3">
          <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
            Applies to
          </legend>
          {!editing && (
            <p className="text-xs text-fg-muted">
              Leave everything blank for a company-wide default. Each field you set narrows the
              rule; the most specific rule wins.
            </p>
          )}
          {(type?.scope_keys ?? [])
            .slice()
            .sort((a, b) => SCOPE_ORDER.indexOf(a) - SCOPE_ORDER.indexOf(b))
            .map((key) => (
              <FormField key={key} label={SCOPE_LABELS[key] ?? key}>
                <Select
                  value={form.scope[key] ?? ""}
                  disabled={editing}
                  onChange={(e) => set("scope", { ...form.scope, [key]: e.target.value })}
                >
                  <option value="">Any</option>
                  {(scopeOptions[key] ?? []).map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
            ))}
        </fieldset>

        <fieldset className="flex flex-col gap-2 rounded-md border border-border p-3">
          <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
            Value
          </legend>
          {valueFields(type).map((key) => (
            <FormField key={key} label={key.replace(/_/g, " ")}>
              <Input
                className="tabular"
                inputMode="decimal"
                value={form.value[key] ?? ""}
                onChange={(e) => set("value", { ...form.value, [key]: e.target.value })}
              />
            </FormField>
          ))}
        </fieldset>

        <div className="grid grid-cols-2 gap-3">
          <FormField label="Effective from" required>
            <Input
              type="date"
              value={form.effective_from}
              disabled={editing}
              onChange={(e) => set("effective_from", e.target.value)}
            />
          </FormField>
          <FormField label="Effective to" hint="Blank means open-ended.">
            <Input
              type="date"
              value={form.effective_to}
              onChange={(e) => set("effective_to", e.target.value)}
            />
          </FormField>
          <FormField label="Priority" hint="Breaks a tie between equally specific rules.">
            <Input
              inputMode="numeric"
              className="tabular"
              value={form.priority}
              onChange={(e) => set("priority", e.target.value)}
            />
          </FormField>
          <label className="flex items-center gap-2 pt-6 text-sm">
            <Checkbox
              checked={form.is_active}
              onChange={(e) => set("is_active", e.target.checked)}
            />
            Active
          </label>
        </div>

        <FormField
          label="Condition (advanced)"
          hint='Optional JSON in the approval engine language, e.g. {">": [{"var": "amount"}, 100000]}'
        >
          <Textarea
            className="font-mono text-xs"
            value={form.condition}
            onChange={(e) => set("condition", e.target.value)}
          />
        </FormField>
        <FormField label="Notes">
          <Textarea
            rows={2}
            value={form.description}
            onChange={(e) => set("description", e.target.value)}
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

// --- "Which rule applies?" -------------------------------------------------------

function ResolveDialog({
  types,
  scopeOptions,
  onClose,
}: {
  types: BusinessRuleType[];
  scopeOptions: Record<string, Option[]>;
  onClose: () => void;
}) {
  const [ruleType, setRuleType] = useState(types[0]?.rule_type ?? "");
  const [context, setContext] = useState<Record<string, string>>({});
  const [at, setAt] = useState(today());
  const type = types.find((t) => t.rule_type === ruleType);

  const ask = useMutation({
    mutationFn: () =>
      api.post<RuleResolution>("/business-rules/resolve", {
        rule_type: ruleType,
        context: Object.fromEntries(Object.entries(context).filter(([, v]) => v)),
        at,
      }),
    onError: (err) => toast.error(describeError(err)),
  });
  const result = ask.data;

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      variant="drawer"
      title="Which rule applies?"
      description="Describe a situation and see which rule would be used, and why each other rule was not."
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <Button variant="primary" loading={ask.isPending} onClick={() => ask.mutate()}>
            Check
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormField label="Rule type">
          <Select
            value={ruleType}
            onChange={(e) => {
              setRuleType(e.target.value);
              setContext({});
              ask.reset();
            }}
          >
            {types.map((t) => (
              <option key={t.rule_type} value={t.rule_type}>
                {t.label}
              </option>
            ))}
          </Select>
        </FormField>
        {(type?.scope_keys ?? []).map((key) => (
          <FormField key={key} label={SCOPE_LABELS[key] ?? key}>
            <Select
              value={context[key] ?? ""}
              onChange={(e) => setContext({ ...context, [key]: e.target.value })}
            >
              <option value="">Not specified</option>
              {(scopeOptions[key] ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
        ))}
        <FormField label="On date">
          <Input type="date" value={at} onChange={(e) => setAt(e.target.value)} />
        </FormField>

        {result && (
          <div className="rounded-md border border-border" aria-live="polite">
            <p className="border-b border-border bg-surface px-3 py-2 text-sm">
              {result.winner ? (
                <>
                  <span className="font-medium">
                    {describeValue(result.winner.rule_type, result.winner.value)}
                  </span>{" "}
                  <span className="text-fg-muted">
                    — {result.winner.name ?? result.winner.rule_type_label}
                  </span>
                </>
              ) : (
                <span className="text-fg-muted">
                  No rule applies. Whatever uses this rule type will not flag anything.
                </span>
              )}
            </p>
            <ul className="divide-y divide-border">
              {result.considered.map((v) => (
                <li
                  key={v.rule_id}
                  className="flex items-start justify-between gap-2 px-3 py-2 text-sm"
                >
                  <span>
                    {v.name ?? "Unnamed rule"}
                    <span className="block text-xs text-fg-muted">
                      {Object.keys(v.scope).length === 0
                        ? "company default"
                        : `${Object.keys(v.scope).length} scope key(s)`}{" "}
                      · priority {v.priority}
                    </span>
                  </span>
                  <span className={v.winner ? "font-semibold text-success" : "text-fg-muted"}>
                    {v.winner ? "Used" : v.applies ? "Applies, but outranked" : v.reason}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Dialog>
  );
}
