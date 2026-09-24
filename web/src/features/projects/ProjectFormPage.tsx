import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams } from "react-router-dom";
import { z } from "zod";

import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { FormAlert } from "@/features/auth/auth-layout";
import { api } from "@/lib/api";
import { projectTypeOptions } from "@/lib/enums";
import { applyServerErrors } from "@/lib/errors";
import { codeField, DECIMAL_RE, dirtyValues, emptyToNull, str } from "@/lib/forms";
import { useUserOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { ProjectDetail, ProjectRead } from "@/types/models";

const decimal = z
  .string()
  .trim()
  .refine((v) => v === "" || DECIMAL_RE.test(v), "A plain number, e.g. 125.5");
const coord = (min: number, max: number) =>
  z
    .string()
    .trim()
    .refine(
      (v) => v === "" || (Number.isFinite(Number(v)) && Number(v) >= min && Number(v) <= max),
      `Between ${min} and ${max}`,
    );

const schema = z
  .object({
    code: codeField,
    name: z.string().trim().min(2, "At least 2 characters").max(200),
    description: z.string(),
    project_type: z.string(),
    location_name: z.string(),
    latitude: coord(-90, 90),
    longitude: coord(-180, 180),
    total_area_value: decimal,
    total_area_unit: z.string(),
    start_date: z.string(),
    end_date: z.string(),
    manager_user_id: z.string(),
    total_budget: decimal,
    require_po_for_delivery: z.boolean(),
    create_default_phases: z.boolean(),
  })
  .refine((v) => !v.start_date || !v.end_date || v.end_date >= v.start_date, {
    path: ["end_date"],
    message: "The end date is before the start date",
  })
  .refine((v) => (v.latitude === "") === (v.longitude === ""), {
    path: ["longitude"],
    message: "Enter both latitude and longitude, or neither",
  });
type Values = z.infer<typeof schema>;

const FIELDS = [
  "code",
  "name",
  "description",
  "project_type",
  "location_name",
  "total_area_value",
  "total_area_unit",
  "start_date",
  "end_date",
  "manager_user_id",
  "total_budget",
] as const;

function toValues(p?: ProjectDetail): Values {
  return {
    code: p?.code ?? "",
    name: p?.name ?? "",
    description: str(p?.description),
    project_type: p?.project_type ?? "HOUSING_SCHEME",
    location_name: str(p?.location_name),
    latitude: str(p?.centroid?.latitude),
    longitude: str(p?.centroid?.longitude),
    total_area_value: str(p?.total_area_value),
    total_area_unit: p?.total_area_unit ?? "KANAL",
    start_date: str(p?.start_date),
    end_date: str(p?.end_date),
    manager_user_id: str(p?.manager_user_id),
    total_budget: str(p?.total_budget),
    require_po_for_delivery: p?.require_po_for_delivery ?? false,
    create_default_phases: true,
  };
}

function toPayload(values: Partial<Values>, full: Values) {
  const { latitude, longitude, create_default_phases, ...rest } = values;
  const payload: Record<string, unknown> = emptyToNull(rest);
  if (latitude !== undefined || longitude !== undefined) {
    payload.centroid =
      full.latitude && full.longitude
        ? { latitude: Number(full.latitude), longitude: Number(full.longitude) }
        : null;
  }
  if (create_default_phases !== undefined) payload.create_default_phases = create_default_phases;
  return payload;
}

export function ProjectFormPage() {
  const { projectId } = useParams();
  const project = useQuery({
    queryKey: ["projects", "detail", projectId],
    queryFn: () => api.get<ProjectDetail>(`/projects/${projectId}`),
    enabled: Boolean(projectId),
    staleTime: 0,
  });
  if (projectId && project.isLoading) return <PageSkeleton />;
  if (projectId && project.error)
    return <ErrorState error={project.error} onRetry={() => void project.refetch()} />;
  return <ProjectForm project={project.data} />;
}

function ProjectForm({ project }: { project?: ProjectDetail }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const users = useUserOptions();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: toValues(project) });
  const errors = form.formState.errors;

  const save = useMutation({
    mutationFn: (values: Values) => {
      if (!project) return api.post<ProjectRead>("/projects", toPayload(values, values));
      const changed = dirtyValues(form.formState.dirtyFields, values);
      delete changed.code;
      delete changed.create_default_phases;
      return api.patch<ProjectRead>(`/projects/${project.id}`, toPayload(changed, values), {
        ifMatch: String(project.version),
      });
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success(project ? `Saved ${saved.code}.` : `Created project ${saved.code}.`);
      navigate(`/projects/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, FIELDS);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title={project ? `Edit ${project.name}` : "New project"}
        crumbs={[
          { label: "Projects", to: "/projects" },
          ...(project ? [{ label: project.code, to: `/projects/${project.id}` }] : []),
          { label: project ? "Edit" : "New" },
        ]}
      />
      <PageBody className="max-w-4xl">
        <form
          onSubmit={(e) =>
            void form.handleSubmit((v) => {
              setFormError(null);
              save.mutate(v);
            })(e)
          }
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}
          <FormSection title="Project">
            <FormGrid>
              <FormField
                label="Code"
                required
                hint={project ? "Codes cannot be changed." : "Short, e.g. GVH"}
                error={errors.code?.message}
              >
                <Input
                  {...form.register("code")}
                  readOnly={Boolean(project)}
                  className="font-mono uppercase read-only:opacity-60"
                  autoFocus={!project}
                />
              </FormField>
              <FormField label="Name" required error={errors.name?.message}>
                <Input {...form.register("name")} />
              </FormField>
              <FormField label="Type">
                <Select {...form.register("project_type")}>
                  {projectTypeOptions.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              {users.allowed && (
                <FormField label="Project manager" error={errors.manager_user_id?.message}>
                  <Select {...form.register("manager_user_id")}>
                    <option value="">Not assigned</option>
                    {users.options.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
                </FormField>
              )}
              <FormField label="Description" className="sm:col-span-2">
                <Textarea {...form.register("description")} />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Location and size">
            <FormGrid>
              <FormField label="Location" error={errors.location_name?.message}>
                <Input
                  {...form.register("location_name")}
                  placeholder="e.g. Raiwind Road, Lahore"
                />
              </FormField>
              <div className="grid grid-cols-2 gap-2">
                <FormField label="Area" error={errors.total_area_value?.message}>
                  <Input
                    inputMode="decimal"
                    {...form.register("total_area_value")}
                    className="tabular"
                  />
                </FormField>
                <FormField label="Area unit">
                  <Select {...form.register("total_area_unit")}>
                    {["KANAL", "MARLA", "ACRE", "SQFT", "SQM", "HECTARE"].map((u) => (
                      <option key={u}>{u}</option>
                    ))}
                  </Select>
                </FormField>
              </div>
              <FormField
                label="Latitude"
                hint="Centre point, decimal degrees"
                error={errors.latitude?.message}
              >
                <Input inputMode="decimal" {...form.register("latitude")} />
              </FormField>
              <FormField label="Longitude" error={errors.longitude?.message}>
                <Input inputMode="decimal" {...form.register("longitude")} />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Schedule and budget">
            <FormGrid>
              <FormField label="Start date" error={errors.start_date?.message}>
                <Input type="date" {...form.register("start_date")} />
              </FormField>
              <FormField label="End date" error={errors.end_date?.message}>
                <Input type="date" {...form.register("end_date")} />
              </FormField>
              <FormField label="Total budget (PKR)" error={errors.total_budget?.message}>
                <Input inputMode="decimal" {...form.register("total_budget")} className="tabular" />
              </FormField>
            </FormGrid>
            <div className="mt-3 flex flex-col gap-2">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox {...form.register("require_po_for_delivery")} />
                Deliveries to this project must reference a purchase order
              </label>
              {!project && (
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox {...form.register("create_default_phases")} />
                  Create the standard development phases
                </label>
              )}
            </div>
          </FormSection>

          <div className="flex gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {project ? "Save changes" : "Create project"}
            </Button>
            <Button onClick={() => navigate(-1)} disabled={save.isPending}>
              Cancel
            </Button>
          </div>
        </form>
      </PageBody>
    </>
  );
}
