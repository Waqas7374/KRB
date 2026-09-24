import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Pencil, Plus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Input, Select } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/menu";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { useCan } from "@/features/auth/use-can";
import { api, type Page } from "@/lib/api";
import { phaseStatusOptions } from "@/lib/enums";
import { applyServerErrors } from "@/lib/errors";
import { dirtyValues, emptyToNull, str } from "@/lib/forms";
import { labelFor, useUserOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import {
  formatDate,
  formatDateRange,
  formatDateTime,
  formatMoney,
  formatQuantity,
  humanize,
} from "@/lib/utils";
import type { ProjectDetail, ProjectPhaseRead, ProjectStatus, SiteListItem } from "@/types/models";

/** Mirrors ALLOWED_PROJECT_TRANSITIONS in backend project_service.py. */
const TRANSITIONS: Record<ProjectStatus, ProjectStatus[]> = {
  DRAFT: ["ACTIVE", "CANCELLED"],
  ACTIVE: ["ON_HOLD", "COMPLETED", "CANCELLED"],
  ON_HOLD: ["ACTIVE", "CANCELLED"],
  COMPLETED: ["CLOSED", "ACTIVE"],
  CLOSED: [],
  CANCELLED: [],
};
const TRANSITION_LABEL: Record<ProjectStatus, string> = {
  DRAFT: "Back to draft",
  ACTIVE: "Activate",
  ON_HOLD: "Put on hold",
  COMPLETED: "Mark completed",
  CLOSED: "Close",
  CANCELLED: "Cancel project",
};
const NEEDS_REASON: ProjectStatus[] = ["CLOSED", "CANCELLED"];

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const canUpdate = useCan("projects.update");
  const canClose = useCan("projects.close");
  const canCreateSite = useCan("sites.create");
  const users = useUserOptions();
  const [target, setTarget] = useState<ProjectStatus | null>(null);
  const [editingPhase, setEditingPhase] = useState<ProjectPhaseRead | null>(null);

  const project = useQuery({
    queryKey: ["projects", "detail", projectId],
    queryFn: () => api.get<ProjectDetail>(`/projects/${projectId}`),
  });
  const sites = useQuery({
    queryKey: ["sites", "list", { project_id: projectId }],
    queryFn: () =>
      api.get<Page<SiteListItem>>("/sites", {
        query: { project_id: projectId, limit: 200, sort: "code" },
      }),
  });

  if (project.isLoading) return <PageSkeleton />;
  if (project.error || !project.data) {
    return <ErrorState error={project.error} onRetry={() => void project.refetch()} />;
  }
  const p = project.data;
  const status = p.status as ProjectStatus;
  const transitions = canClose ? (TRANSITIONS[status] ?? []) : [];

  return (
    <>
      <PageHeader
        title={p.name}
        crumbs={[{ label: "Projects", to: "/projects" }, { label: p.code }]}
        meta={
          <>
            <StatusBadge status={p.status} />
            <Tag>{humanize(p.project_type)}</Tag>
            <span className="font-mono text-sm text-fg-muted">{p.code}</span>
          </>
        }
        actions={
          <>
            {canUpdate && !["CLOSED", "CANCELLED"].includes(status) && (
              <Button asChild>
                <Link to={`/projects/${p.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {transitions.length > 0 && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button>
                    Status <ChevronDown />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  {transitions.map((t) => (
                    <DropdownMenuItem
                      key={t}
                      onSelect={() => setTarget(t)}
                      destructive={t === "CANCELLED"}
                    >
                      {TRANSITION_LABEL[t]}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </>
        }
      />
      <PageBody>
        {p.close_reason && (
          <FormAlert>
            {humanize(p.status)}: {p.close_reason}
          </FormAlert>
        )}
        <Section title="Details">
          <FieldGrid
            columns={4}
            items={[
              { label: "Location", value: p.location_name },
              {
                label: "Area",
                value: p.total_area_value
                  ? formatQuantity(p.total_area_value, p.total_area_unit ?? undefined)
                  : null,
              },
              { label: "Manager", value: labelFor(users.options, p.manager_user_id) },
              {
                label: "Budget",
                value: p.total_budget
                  ? formatMoney(p.total_budget, { currency: p.currency_code, decimals: 0 })
                  : null,
              },
              { label: "Start", value: formatDate(p.start_date) },
              { label: "Planned end", value: formatDate(p.end_date) },
              {
                label: "Completed",
                value: p.actual_completion_date ? formatDate(p.actual_completion_date) : null,
              },
              { label: "Deliveries need a PO", value: p.require_po_for_delivery ? "Yes" : "No" },
              {
                label: "Centre point",
                value: p.centroid
                  ? `${p.centroid.latitude.toFixed(6)}, ${p.centroid.longitude.toFixed(6)}`
                  : null,
                mono: true,
              },
              { label: "Last updated", value: formatDateTime(p.updated_at) },
              { label: "Description", value: p.description, wide: true },
            ]}
          />
        </Section>

        <Section title="Phases">
          {p.phases?.length ? (
            <table className="w-full text-sm">
              <caption className="sr-only">Project phases</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    #
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Phase
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Planned
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Actual
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Progress
                  </th>
                  <th scope="col" className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {[...p.phases]
                  .sort((a, b) => a.sequence - b.sequence)
                  .map((phase) => (
                    <tr key={phase.id} className="border-t border-border">
                      <td className="px-4 py-2 text-fg-muted">{phase.sequence}</td>
                      <td className="px-4 py-2 font-medium">{phase.name}</td>
                      <td className="px-4 py-2">
                        <StatusBadge status={phase.status} />
                      </td>
                      <td className="px-4 py-2">
                        {formatDateRange(phase.planned_start, phase.planned_end)}
                      </td>
                      <td className="px-4 py-2">
                        {formatDateRange(phase.actual_start, phase.actual_end)}
                      </td>
                      <td data-numeric className="px-4 py-2">
                        {formatQuantity(phase.progress_pct)}%
                      </td>
                      <td className="px-4 py-2 text-right">
                        {canUpdate && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setEditingPhase(phase)}
                            aria-label={`Edit phase ${phase.name}`}
                          >
                            <Pencil />
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          ) : (
            <p className="px-4 py-4 text-sm text-fg-muted">No phases defined.</p>
          )}
        </Section>

        <Section
          title={`Sites (${sites.data?.page.total ?? "…"})`}
          actions={
            canCreateSite && (
              <Button size="sm" variant="ghost" asChild>
                <Link to={`/sites/new?project_id=${p.id}`}>
                  <Plus /> Add site
                </Link>
              </Button>
            )
          }
        >
          {sites.data?.items.length ? (
            <ul className="divide-y divide-border">
              {sites.data.items.map((s) => (
                <li key={s.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <Link to={`/sites/${s.id}`} className="font-mono text-primary hover:underline">
                    {s.code}
                  </Link>
                  <span className="font-medium">{s.name}</span>
                  <Tag>{humanize(s.site_type)}</Tag>
                  <span className="ml-auto text-fg-muted">{s.city ?? ""}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-4 py-4 text-sm text-fg-muted">
              {sites.isLoading ? "Loading…" : "No sites in this project yet."}
            </p>
          )}
        </Section>
      </PageBody>

      {target && (
        <ConfirmDialog
          open
          onOpenChange={(o) => !o && setTarget(null)}
          title={`${TRANSITION_LABEL[target]}: ${p.name}?`}
          description={
            target === "CLOSED"
              ? "Closing is permanent. All sites must be deactivated first."
              : target === "CANCELLED"
                ? "Cancelling is permanent."
                : `The project moves from ${humanize(status)} to ${humanize(target)}.`
          }
          confirmLabel={`${TRANSITION_LABEL[target]} ${p.code}`}
          destructive={NEEDS_REASON.includes(target)}
          reason={{ label: "Reason", required: NEEDS_REASON.includes(target) }}
          onConfirm={async (reason) => {
            await api.post(`/projects/${p.id}/status`, { status: target, reason: reason || null });
            await queryClient.invalidateQueries({ queryKey: ["projects"] });
            toast.success(`${p.code} is now ${humanize(target).toLowerCase()}.`);
          }}
        />
      )}
      {editingPhase && (
        <PhaseDialog projectId={p.id} phase={editingPhase} onClose={() => setEditingPhase(null)} />
      )}
    </>
  );
}

const phaseSchema = z.object({
  name: z.string().trim().min(2),
  status: z.string(),
  planned_start: z.string(),
  planned_end: z.string(),
  actual_start: z.string(),
  actual_end: z.string(),
  progress_pct: z
    .string()
    .trim()
    .refine((v) => v === "" || (Number(v) >= 0 && Number(v) <= 100), "Between 0 and 100"),
});
type PhaseValues = z.infer<typeof phaseSchema>;

function PhaseDialog({
  projectId,
  phase,
  onClose,
}: {
  projectId: string;
  phase: ProjectPhaseRead;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<PhaseValues>({
    resolver: zodResolver(phaseSchema),
    defaultValues: {
      name: phase.name,
      status: phase.status,
      planned_start: str(phase.planned_start),
      planned_end: str(phase.planned_end),
      actual_start: str(phase.actual_start),
      actual_end: str(phase.actual_end),
      progress_pct: str(phase.progress_pct),
    },
  });
  const save = useMutation({
    mutationFn: (values: PhaseValues) =>
      api.patch(
        `/projects/${projectId}/phases/${phase.id}`,
        emptyToNull(dirtyValues(form.formState.dirtyFields, values)),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects", "detail", projectId] });
      toast.success(`Phase "${phase.name}" updated.`);
      onClose();
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, Object.keys(phaseSchema.shape));
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Edit phase: ${phase.name}`}
      variant="drawer"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => void form.handleSubmit((v) => save.mutate(v))()}
          >
            Save phase
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormGrid>
          <FormField label="Name" error={errors.name?.message}>
            <Input {...form.register("name")} />
          </FormField>
          <FormField label="Status">
            <Select {...form.register("status")}>
              {phaseStatusOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Planned start">
            <Input type="date" {...form.register("planned_start")} />
          </FormField>
          <FormField label="Planned end">
            <Input type="date" {...form.register("planned_end")} />
          </FormField>
          <FormField label="Actual start">
            <Input type="date" {...form.register("actual_start")} />
          </FormField>
          <FormField label="Actual end">
            <Input type="date" {...form.register("actual_end")} />
          </FormField>
          <FormField label="Progress (%)" error={errors.progress_pct?.message}>
            <Input inputMode="decimal" {...form.register("progress_pct")} />
          </FormField>
        </FormGrid>
      </div>
    </Dialog>
  );
}
