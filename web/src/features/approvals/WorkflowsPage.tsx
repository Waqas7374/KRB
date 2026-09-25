import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calculator, FilePlus2, Power } from "lucide-react";
import { useMemo, useState } from "react";

import { PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { useCan } from "@/features/auth/use-can";
import { ApiError, api } from "@/lib/api";
import { describeError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { formatDateTime, humanize } from "@/lib/utils";
import type {
  ApprovalDocumentType,
  ApprovalSimulation,
  ApprovalWorkflowRead,
} from "@/types/models";

interface Step {
  step_no: number;
  name: string;
  approver_type: string;
  approver_ref?: string | null;
  approver_refs?: string[];
  quorum_type?: string;
  sla_hours?: number;
  escalate_to_ref?: string | null;
}
interface Rule {
  sequence: number;
  name: string;
  condition: unknown;
  steps: Step[];
}

/** A condition as a sentence-ish string: {"<": [{"var": "total_amount"}, 100000]} -> total_amount < 100000 */
function describeCondition(expr: unknown): string {
  if (expr === true) return "everything else";
  if (expr === false) return "never";
  if (expr === null || typeof expr !== "object") return JSON.stringify(expr);
  const entries = Object.entries(expr as Record<string, unknown>);
  const [op, raw] = entries[0] ?? ["", []];
  const args = Array.isArray(raw) ? raw : [raw];
  if (op === "var") return String(args[0]);
  if (op === "and" || op === "or")
    return args.map((a) => `(${describeCondition(a)})`).join(` ${op} `);
  if (op === "not") return `not ${describeCondition(args[0])}`;
  if (op === "between")
    return `${describeCondition(args[0])} between ${describeCondition(args[1])} and ${describeCondition(args[2])}`;
  return args.map(describeCondition).join(` ${op} `);
}

function approverLabel(step: Step): string {
  if (step.approver_type === "GROUP") return `${step.approver_refs?.length ?? 0} named people`;
  if (step.approver_type === "USER") return "a named person";
  return humanize(step.approver_ref ?? "");
}

function WorkflowCard({
  workflow,
  canManage,
  onNewVersion,
  onDeactivate,
}: {
  workflow: ApprovalWorkflowRead;
  canManage: boolean;
  onNewVersion: (w: ApprovalWorkflowRead) => void;
  onDeactivate: (w: ApprovalWorkflowRead) => void;
}) {
  const rules = (workflow.definition as { rules: Rule[] }).rules;
  return (
    <Section
      title={`${humanize(workflow.doc_type)} · v${workflow.version}`}
      actions={
        canManage &&
        workflow.is_active && (
          <>
            <Button size="sm" variant="ghost" onClick={() => onNewVersion(workflow)}>
              <FilePlus2 /> New version
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onDeactivate(workflow)}>
              <Power /> Deactivate
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-4 py-2 text-sm">
        <span className="font-medium">{workflow.name}</span>
        <StatusBadge
          status={workflow.is_active ? "ACTIVE" : "INACTIVE"}
          tone={workflow.is_active ? "success" : "neutral"}
        />
        <Tag>{humanize(workflow.scope_type)}</Tag>
        <span className="text-fg-muted">Published {formatDateTime(workflow.created_at)}</span>
        {workflow.change_note && <span className="text-fg-muted">— {workflow.change_note}</span>}
      </div>
      <ol className="divide-y divide-border">
        {rules.map((rule) => (
          <li key={rule.sequence} className="px-4 py-3">
            <p className="text-sm">
              <span className="font-medium">{rule.name}</span>{" "}
              <span className="text-fg-muted">
                when <span className="font-mono text-xs">{describeCondition(rule.condition)}</span>
              </span>
            </p>
            {rule.steps.length === 0 ? (
              <p className="mt-1 text-sm text-warning">No steps: approved automatically.</p>
            ) : (
              <ol className="mt-1.5 flex flex-wrap items-center gap-1.5 text-sm">
                {rule.steps.map((step, i) => (
                  <li key={step.step_no} className="flex items-center gap-1.5">
                    {i > 0 && (
                      <span className="text-fg-subtle" aria-hidden>
                        →
                      </span>
                    )}
                    <span className="rounded-sm border border-border bg-surface px-2 py-0.5">
                      {step.name}
                      <span className="ml-1 text-xs text-fg-muted">
                        {approverLabel(step).toLowerCase() === step.name.toLowerCase()
                          ? ""
                          : `${approverLabel(step)} · `}
                        {step.sla_hours ?? 24}h
                      </span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </li>
        ))}
      </ol>
    </Section>
  );
}

export function WorkflowsPage() {
  const queryClient = useQueryClient();
  const canManage = useCan("settings.manage_workflows");
  const [showHistory, setShowHistory] = useState(false);
  const [editing, setEditing] = useState<ApprovalWorkflowRead | null>(null);
  const [deactivating, setDeactivating] = useState<ApprovalWorkflowRead | null>(null);

  const workflows = useQuery({
    queryKey: ["approval-workflows", { showHistory }],
    queryFn: () =>
      api.get<ApprovalWorkflowRead[]>("/approval-workflows", {
        query: { include_inactive: showHistory },
      }),
  });
  const types = useQuery({
    queryKey: ["approval-workflows", "document-types"],
    queryFn: () => api.get<ApprovalDocumentType[]>("/approval-workflows/document-types"),
    staleTime: Infinity,
  });

  if (workflows.isLoading) return <PageSkeleton />;
  if (workflows.error)
    return <ErrorState error={workflows.error} onRetry={() => void workflows.refetch()} />;
  const configured = new Set(
    (workflows.data ?? []).filter((w) => w.is_active).map((w) => w.doc_type),
  );
  const missing = (types.data ?? []).filter((t) => !configured.has(t.doc_type));

  return (
    <>
      <PageHeader
        title="Approval workflows"
        subtitle="Who must sign, in what order, depending on the document. Publishing a change creates a new version — requests already in flight keep the version they started with."
        actions={
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} />
            Show retired versions
          </label>
        }
      />
      <PageBody className="max-w-5xl">
        {missing.map((t) => (
          <div
            key={t.doc_type}
            className="rounded-md border border-warning bg-warning-bg px-4 py-3 text-sm"
          >
            <strong>{t.label}s have no active workflow</strong>, so they cannot be submitted.
            Publish one below.
            {canManage && (
              <Button
                size="sm"
                className="ml-3"
                onClick={() =>
                  setEditing({
                    id: "",
                    doc_type: t.doc_type,
                    name: `${t.label} approval`,
                    version: 0,
                    is_active: false,
                    scope_type: "COMPANY",
                    definition: {
                      rules: [{ sequence: 99, name: "Everyone", condition: true, steps: [] }],
                    },
                  } as unknown as ApprovalWorkflowRead)
                }
              >
                Create workflow
              </Button>
            )}
          </div>
        ))}

        <Simulator types={types.data ?? []} />

        {workflows.data?.length === 0 ? (
          <EmptyState title="No workflows yet" />
        ) : (
          workflows.data?.map((w) => (
            <WorkflowCard
              key={w.id}
              workflow={w}
              canManage={canManage}
              onNewVersion={setEditing}
              onDeactivate={setDeactivating}
            />
          ))
        )}
      </PageBody>

      {editing && <PublishDialog base={editing} onClose={() => setEditing(null)} />}
      {deactivating && (
        <ConfirmDialog
          open
          onOpenChange={(o) => !o && setDeactivating(null)}
          title={`Deactivate ${humanize(deactivating.doc_type)} workflow v${deactivating.version}?`}
          description="Documents of this type can no longer be submitted until a workflow is published again. Requests already in flight are not affected."
          confirmLabel="Deactivate workflow"
          destructive
          onConfirm={async () => {
            await api.post(`/approval-workflows/${deactivating.id}/deactivate`);
            await queryClient.invalidateQueries({ queryKey: ["approval-workflows"] });
            toast.success("Workflow deactivated.");
          }}
        />
      )}
    </>
  );
}

/** Publish a new version: the definition is edited as JSON, validated by the server. */
function PublishDialog({ base, onClose }: { base: ApprovalWorkflowRead; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(base.name);
  const [note, setNote] = useState("");
  const [text, setText] = useState(() => JSON.stringify(base.definition, null, 2));
  const [problems, setProblems] = useState<string[]>([]);

  const parsed = useMemo(() => {
    try {
      return { value: JSON.parse(text) as Record<string, unknown>, error: null };
    } catch (err) {
      return { value: null, error: err instanceof Error ? err.message : "Invalid JSON" };
    }
  }, [text]);

  const publish = useMutation({
    mutationFn: () =>
      api.post<ApprovalWorkflowRead>("/approval-workflows", {
        doc_type: base.doc_type,
        name,
        scope_type: base.scope_type,
        scope_id: base.scope_id ?? null,
        definition: parsed.value,
        change_note: note || null,
      }),
    onSuccess: async (w) => {
      await queryClient.invalidateQueries({ queryKey: ["approval-workflows"] });
      toast.success(`Published version ${w.version}. New submissions use it from now on.`);
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.fieldErrors.length) {
        setProblems(
          err.fieldErrors.map((e) => `${e.field.replace(/^definition\./, "")}: ${e.message}`),
        );
      } else {
        setProblems([describeError(err)]);
      }
    },
  });

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      variant="drawer"
      className="max-w-3xl"
      title={`Publish ${humanize(base.doc_type)} workflow`}
      description="The last rule must have condition true. Roles named in steps must be able to approve this document type; the server checks every reference before saving."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={publish.isPending}
            disabled={!parsed.value}
            onClick={() => {
              setProblems([]);
              publish.mutate();
            }}
          >
            Publish new version
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FormField label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </FormField>
        <FormField label="What changed" hint="Kept in the audit log">
          <Input value={note} onChange={(e) => setNote(e.target.value)} />
        </FormField>
        <FormField label="Definition (JSON)" error={parsed.error ?? undefined}>
          <Textarea
            rows={22}
            spellCheck={false}
            className="font-mono text-xs"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </FormField>
        {problems.length > 0 && (
          <ul
            role="alert"
            className="list-disc rounded-md bg-danger-bg px-6 py-2 text-sm text-danger"
          >
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  );
}

/** Try a document context against the live workflow before anyone depends on it. */
function Simulator({ types }: { types: ApprovalDocumentType[] }) {
  const [docType, setDocType] = useState("");
  const [amount, setAmount] = useState("");
  const [result, setResult] = useState<ApprovalSimulation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const selected = docType || types[0]?.doc_type || "";

  const run = useMutation({
    mutationFn: () =>
      api.post<ApprovalSimulation>("/approval-workflows/simulate", {
        doc_type: selected,
        context: { total_amount: amount },
      }),
    onSuccess: (r) => {
      setResult(r);
      setError(null);
    },
    onError: (err) => {
      setResult(null);
      setError(describeError(err));
    },
  });

  return (
    <Section title="Try a workflow">
      <div className="flex flex-wrap items-end gap-2 p-3">
        <FormField label="Document" className="w-52">
          <Select value={selected} onChange={(e) => setDocType(e.target.value)}>
            {types.map((t) => (
              <option key={t.doc_type} value={t.doc_type}>
                {t.label}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Total amount" className="w-44">
          <Input
            inputMode="decimal"
            className="tabular"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </FormField>
        <Button
          onClick={() => run.mutate()}
          loading={run.isPending}
          disabled={!selected || !amount.trim()}
        >
          <Calculator /> Show the chain
        </Button>
      </div>
      {(result || error) && (
        <div className="border-t border-border px-4 py-3 text-sm" aria-live="polite">
          {error ? (
            <p className="text-danger">{error}</p>
          ) : result ? (
            <>
              <p>
                Routed by <strong>{result.workflow_name}</strong> v{result.version}, rule{" "}
                <strong>{result.rule_name}</strong>:
              </p>
              <ol className="mt-1.5 flex flex-wrap items-center gap-1.5">
                {result.steps.length === 0 && (
                  <li className="text-warning">no steps — approved automatically</li>
                )}
                {result.steps.map((s, i) => (
                  <li key={s.step_no} className="flex items-center gap-1.5">
                    {i > 0 && (
                      <span className="text-fg-subtle" aria-hidden>
                        →
                      </span>
                    )}
                    <span className="rounded-sm border border-border bg-surface px-2 py-0.5">
                      {s.name} <span className="text-xs text-fg-muted">{s.sla_hours}h</span>
                    </span>
                  </li>
                ))}
              </ol>
            </>
          ) : null}
        </div>
      )}
    </Section>
  );
}
