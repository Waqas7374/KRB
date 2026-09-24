import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { FormAlert } from "@/features/auth/auth-layout";
import { api } from "@/lib/api";
import { siteTypeOptions } from "@/lib/enums";
import { applyServerErrors } from "@/lib/errors";
import { codeField, dirtyValues, emptyToNull, str } from "@/lib/forms";
import { useProjectOptions, useUserOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { SiteRead } from "@/types/models";

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
    project_id: z.string(),
    site_type: z.string(),
    address: z.string(),
    city: z.string(),
    timezone: z.string().min(1),
    manager_user_id: z.string(),
    contact_phone: z.string().max(32),
    latitude: coord(-90, 90),
    longitude: coord(-180, 180),
    geofence_radius_m: z
      .string()
      .trim()
      .refine(
        (v) => v === "" || (Number(v) > 0 && Number(v) <= 50_000),
        "Between 1 and 50 000 metres",
      ),
  })
  .refine((v) => (v.latitude === "") === (v.longitude === ""), {
    path: ["longitude"],
    message: "Enter both latitude and longitude, or neither",
  });
type Values = z.infer<typeof schema>;

const FIELDS = [
  "code",
  "name",
  "project_id",
  "site_type",
  "address",
  "city",
  "timezone",
  "manager_user_id",
  "contact_phone",
  "geofence_radius_m",
] as const;

export function SiteFormPage() {
  const { siteId } = useParams();
  const site = useQuery({
    queryKey: ["sites", "detail", siteId],
    queryFn: () => api.get<SiteRead>(`/sites/${siteId}`),
    enabled: Boolean(siteId),
    staleTime: 0,
  });
  if (siteId && site.isLoading) return <PageSkeleton />;
  if (siteId && site.error)
    return <ErrorState error={site.error} onRetry={() => void site.refetch()} />;
  return <SiteForm site={site.data} />;
}

function SiteForm({ site }: { site?: SiteRead }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const queryClient = useQueryClient();
  const projects = useProjectOptions();
  const users = useUserOptions();
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      code: site?.code ?? "",
      name: site?.name ?? "",
      project_id: site?.project_id ?? params.get("project_id") ?? "",
      site_type: site?.site_type ?? "DEVELOPMENT",
      address: str(site?.address),
      city: str(site?.city),
      timezone: site?.timezone ?? "Asia/Karachi",
      manager_user_id: str(site?.manager_user_id),
      contact_phone: str(site?.contact_phone),
      latitude: str(site?.centroid?.latitude),
      longitude: str(site?.centroid?.longitude),
      geofence_radius_m: site?.geofence_radius_m ?? "300",
    },
  });
  const errors = form.formState.errors;

  const save = useMutation({
    mutationFn: (values: Values) => {
      const { latitude, longitude, geofence_radius_m, ...rest } = values;
      if (!site) {
        return api.post<SiteRead>("/sites", {
          ...emptyToNull(rest),
          centroid:
            latitude && longitude
              ? { latitude: Number(latitude), longitude: Number(longitude) }
              : null,
          geofence_radius_m: geofence_radius_m || undefined,
        });
      }
      // The geofence is changed through its own audited endpoint on the
      // detail page, so the edit form sends only descriptive fields.
      const changed = dirtyValues(form.formState.dirtyFields, rest);
      delete changed.code;
      return api.patch<SiteRead>(`/sites/${site.id}`, emptyToNull(changed), {
        ifMatch: String(site.version),
      });
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["sites"] });
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success(site ? `Saved ${saved.code}.` : `Created site ${saved.code}.`);
      navigate(`/sites/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, FIELDS);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  return (
    <>
      <PageHeader
        title={site ? `Edit ${site.name}` : "New site"}
        crumbs={[
          { label: "Sites", to: "/sites" },
          ...(site ? [{ label: site.code, to: `/sites/${site.id}` }] : []),
          { label: site ? "Edit" : "New" },
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
          <FormSection title="Site">
            <FormGrid>
              <FormField
                label="Code"
                required
                error={errors.code?.message}
                hint={site ? "Codes cannot be changed." : "e.g. GVH-S3"}
              >
                <Input
                  {...form.register("code")}
                  readOnly={Boolean(site)}
                  className="font-mono uppercase read-only:opacity-60"
                  autoFocus={!site}
                />
              </FormField>
              <FormField label="Name" required error={errors.name?.message}>
                <Input {...form.register("name")} />
              </FormField>
              <FormField
                label="Project"
                hint="Leave empty for a company-level site such as a central store."
                error={errors.project_id?.message}
              >
                <Select {...form.register("project_id")}>
                  <option value="">None (company-level)</option>
                  {projects.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Type">
                <Select {...form.register("site_type")}>
                  {siteTypeOptions.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              {users.allowed && (
                <FormField label="Site manager">
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
              <FormField label="Contact phone" error={errors.contact_phone?.message}>
                <Input type="tel" {...form.register("contact_phone")} />
              </FormField>
              <FormField label="Address" className="sm:col-span-2">
                <Textarea rows={2} {...form.register("address")} />
              </FormField>
              <FormField label="City">
                <Input {...form.register("city")} />
              </FormField>
              <FormField label="Timezone" error={errors.timezone?.message}>
                <Input {...form.register("timezone")} />
              </FormField>
            </FormGrid>
          </FormSection>
          {!site && (
            <FormSection title="Geofence">
              <p className="mb-2 text-sm text-fg-muted">
                Deliveries captured outside this circle are flagged for review. A polygon can be set
                later from the site page.
              </p>
              <FormGrid>
                <FormField label="Latitude" error={errors.latitude?.message}>
                  <Input inputMode="decimal" {...form.register("latitude")} />
                </FormField>
                <FormField label="Longitude" error={errors.longitude?.message}>
                  <Input inputMode="decimal" {...form.register("longitude")} />
                </FormField>
                <FormField label="Radius (metres)" error={errors.geofence_radius_m?.message}>
                  <Input inputMode="decimal" {...form.register("geofence_radius_m")} />
                </FormField>
              </FormGrid>
            </FormSection>
          )}
          <div className="flex gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {site ? "Save changes" : "Create site"}
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
