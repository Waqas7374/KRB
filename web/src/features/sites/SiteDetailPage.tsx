import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Pencil } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { useCan } from "@/features/auth/use-can";
import { api, type Page } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { labelFor, useUserOptions } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDateTime, formatQuantity, humanize } from "@/lib/utils";
import type { Point, SiteRead, WarehouseRead } from "@/types/models";

interface GeofenceRead {
  site_id: string;
  centroid: Point | null;
  geofence_radius_m: string;
  boundary: Point[] | null;
  effective_mode: "RADIUS" | "POLYGON";
}

export function SiteDetailPage() {
  const { siteId = "" } = useParams();
  const canUpdate = useCan("sites.update");
  const canFence = useCan("sites.manage_geofence");
  const canWarehouses = useCan("warehouses.view");
  const users = useUserOptions();
  const [editingFence, setEditingFence] = useState(false);

  const site = useQuery({
    queryKey: ["sites", "detail", siteId],
    queryFn: () => api.get<SiteRead>(`/sites/${siteId}`),
  });
  const fence = useQuery({
    queryKey: ["sites", "geofence", siteId],
    queryFn: () => api.get<GeofenceRead>(`/sites/${siteId}/geofence`),
  });
  const warehouses = useQuery({
    queryKey: ["warehouses", "list", { site_id: siteId }],
    queryFn: () =>
      api.get<Page<WarehouseRead>>("/warehouses", { query: { site_id: siteId, limit: 200 } }),
    enabled: canWarehouses,
  });

  if (site.isLoading) return <PageSkeleton />;
  if (site.error || !site.data)
    return <ErrorState error={site.error} onRetry={() => void site.refetch()} />;
  const s = site.data;
  const f = fence.data;

  return (
    <>
      <PageHeader
        title={s.name}
        crumbs={[
          { label: "Sites", to: "/sites" },
          ...(s.project_id && s.project_code
            ? [{ label: s.project_code, to: `/projects/${s.project_id}` }]
            : []),
          { label: s.code },
        ]}
        meta={
          <>
            <Tag>{humanize(s.site_type)}</Tag>
            <span className="font-mono text-sm text-fg-muted">{s.code}</span>
          </>
        }
        actions={
          canUpdate && (
            <Button asChild>
              <Link to={`/sites/${s.id}/edit`}>
                <Pencil /> Edit
              </Link>
            </Button>
          )
        }
      />
      <PageBody>
        <Section title="Details">
          <FieldGrid
            items={[
              { label: "Project", value: s.project_code ?? "Company-level site" },
              { label: "City", value: s.city },
              { label: "Timezone", value: s.timezone },
              { label: "Manager", value: labelFor(users.options, s.manager_user_id) },
              { label: "Contact phone", value: s.contact_phone },
              { label: "Last updated", value: formatDateTime(s.updated_at) },
              { label: "Address", value: s.address, wide: true },
            ]}
          />
        </Section>

        <Section
          title="Geofence"
          actions={
            canFence && (
              <Button size="sm" variant="ghost" onClick={() => setEditingFence(true)}>
                <MapPin /> Change
              </Button>
            )
          }
        >
          {fence.isError ? (
            <ErrorState error={fence.error} onRetry={() => void fence.refetch()} />
          ) : (
            <FieldGrid
              items={[
                {
                  label: "Mode",
                  value: f
                    ? f.effective_mode === "POLYGON"
                      ? "Polygon boundary"
                      : "Radius around centre"
                    : "…",
                },
                {
                  label: "Centre",
                  value: f?.centroid
                    ? `${f.centroid.latitude.toFixed(6)}, ${f.centroid.longitude.toFixed(6)}`
                    : "Not set",
                  mono: true,
                },
                { label: "Radius", value: f ? formatQuantity(f.geofence_radius_m, "m") : "…" },
                {
                  label: "Boundary points",
                  value: f?.boundary ? String(f.boundary.length) : "None",
                },
              ]}
            />
          )}
          {f && !f.centroid && !f.boundary && (
            <p className="px-4 pb-3 text-sm text-warning">
              No centre point is set, so deliveries at this site cannot be checked against a
              location.
            </p>
          )}
        </Section>

        {canWarehouses && (
          <Section title="Warehouses">
            {warehouses.data?.items.length ? (
              <ul className="divide-y divide-border">
                {warehouses.data.items.map((w) => (
                  <li key={w.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                    <span className="font-mono">{w.code}</span>
                    <span className="font-medium">{w.name}</span>
                    <Tag>{humanize(w.warehouse_type)}</Tag>
                    {w.is_default_receiving && <Tag>Default receiving</Tag>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-4 py-4 text-sm text-fg-muted">
                {warehouses.isLoading ? "Loading…" : "No warehouses at this site."}
              </p>
            )}
          </Section>
        )}
      </PageBody>
      {editingFence && f && (
        <GeofenceDialog siteId={s.id} current={f} onClose={() => setEditingFence(false)} />
      )}
    </>
  );
}

const fenceSchema = z.object({
  latitude: z
    .string()
    .trim()
    .refine(
      (v) => Number.isFinite(Number(v)) && v !== "" && Math.abs(Number(v)) <= 90,
      "Between -90 and 90",
    ),
  longitude: z
    .string()
    .trim()
    .refine(
      (v) => Number.isFinite(Number(v)) && v !== "" && Math.abs(Number(v)) <= 180,
      "Between -180 and 180",
    ),
  geofence_radius_m: z
    .string()
    .trim()
    .refine((v) => Number(v) > 0 && Number(v) <= 50_000, "Between 1 and 50 000 metres"),
  clear_boundary: z.boolean(),
});
type FenceValues = z.infer<typeof fenceSchema>;

function GeofenceDialog({
  siteId,
  current,
  onClose,
}: {
  siteId: string;
  current: GeofenceRead;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<FenceValues>({
    resolver: zodResolver(fenceSchema),
    defaultValues: {
      latitude: current.centroid ? String(current.centroid.latitude) : "",
      longitude: current.centroid ? String(current.centroid.longitude) : "",
      geofence_radius_m: current.geofence_radius_m,
      clear_boundary: false,
    },
  });
  const save = useMutation({
    mutationFn: (v: FenceValues) =>
      api.put(`/sites/${siteId}/geofence`, {
        centroid: { latitude: Number(v.latitude), longitude: Number(v.longitude) },
        geofence_radius_m: v.geofence_radius_m,
        clear_boundary: v.clear_boundary,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["sites"] });
      toast.success("Geofence updated. New deliveries are checked against it from now on.");
      onClose();
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, ["geofence_radius_m"]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });
  const errors = form.formState.errors;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Change geofence"
      description="This changes which future deliveries are flagged as off-site. The change is audited."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => void form.handleSubmit((v) => save.mutate(v))()}
          >
            Save geofence
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {formError && <FormAlert>{formError}</FormAlert>}
        <FormGrid>
          <FormField label="Latitude" required error={errors.latitude?.message}>
            <Input inputMode="decimal" {...form.register("latitude")} />
          </FormField>
          <FormField label="Longitude" required error={errors.longitude?.message}>
            <Input inputMode="decimal" {...form.register("longitude")} />
          </FormField>
          <FormField label="Radius (metres)" required error={errors.geofence_radius_m?.message}>
            <Input inputMode="decimal" {...form.register("geofence_radius_m")} />
          </FormField>
        </FormGrid>
        {current.boundary && (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox {...form.register("clear_boundary")} /> Remove the polygon boundary and use
            the radius
          </label>
        )}
      </div>
    </Dialog>
  );
}
