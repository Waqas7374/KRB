import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import {
  Ban,
  Check,
  CornerUpLeft,
  FilePlus2,
  PackageCheck,
  LocateFixed,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router-dom";
import { z } from "zod";

import { DataTable } from "@/components/data-table/data-table";
import { FilterBar } from "@/components/data-table/filter-bar";
import { useListParams } from "@/components/data-table/use-list-params";
import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Input, Select, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { PermissionGate } from "@/features/auth/permission-gate";
import { AttachmentsSection } from "@/features/documents/AttachmentsSection";
import { useCan } from "@/features/auth/use-can";
import { api, type Page } from "@/lib/api";
import { applyServerErrors, describeError } from "@/lib/errors";
import { DECIMAL_RE, emptyToNull } from "@/lib/forms";
import {
  useMaterialOptions,
  usePagedList,
  useSiteOptions,
  useTruckTypeOptions,
  useUnitOptions,
  useVendorOptions,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { formatDate, formatDateTime, formatMoney, formatQuantity, humanize } from "@/lib/utils";
import type {
  DeliveryCreate,
  DeliveryFlagRead,
  DeliveryListItem,
  DeliveryRead,
  PurchaseOrderListItem,
} from "@/types/models";

import { ageLabel, geofenceSummary, plainQty, skewLabel } from "./delivery-format";

const STATUS_OPTIONS = [
  "SUBMITTED",
  "UNDER_REVIEW",
  "CORRECTION_REQUESTED",
  "APPROVED",
  "REJECTED",
  "RECEIVED",
  "CANCELLED",
].map((value) => ({ value, label: humanize(value) }));

const toLocalInput = (iso?: string) => {
  const d = iso ? new Date(iso) : new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

// --- Lists -----------------------------------------------------------------------

const col = createColumnHelper<DeliveryListItem>();

function useDeliveryColumns(showAge: boolean) {
  return useMemo(
    () => [
      col.accessor("delivery_number", {
        header: "Delivery",
        meta: { sortKey: "delivery_number", alwaysVisible: true },
        cell: (c) => (
          <Link
            to={`/deliveries/${c.row.original.id}`}
            className="font-mono text-primary hover:underline"
          >
            {c.getValue()}
          </Link>
        ),
      }),
      col.accessor("status", {
        header: "Status",
        meta: { sortKey: "status" },
        cell: (c) => <StatusBadge status={c.getValue()} />,
      }),
      col.display({
        id: "flags",
        header: "Flags",
        meta: { sortKey: "flag_count" },
        cell: (c) => {
          const row = c.row.original;
          if (!row.flag_count) return <span className="text-fg-subtle">None</span>;
          return (
            <span className="inline-flex items-center gap-2">
              {row.worst_severity ? (
                <StatusBadge status={row.worst_severity} />
              ) : (
                <span className="text-xs text-fg-muted">Resolved</span>
              )}
              <span className="text-xs text-fg-muted">
                {row.flag_count} flag{row.flag_count === 1 ? "" : "s"}
              </span>
            </span>
          );
        },
      }),
      col.accessor("captured_at", {
        header: "Captured",
        meta: { sortKey: "captured_at" },
        cell: (c) => formatDateTime(c.getValue()),
      }),
      ...(showAge
        ? [
            col.display({
              id: "waiting",
              header: "Waiting",
              cell: (c) => ageLabel(c.row.original.captured_at),
            }),
          ]
        : []),
      col.accessor("site_code", { header: "Site", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("vendor_name", { header: "Vendor", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("material_summary", {
        header: "Material",
        cell: (c) => <span className="line-clamp-1 max-w-56">{c.getValue() ?? "—"}</span>,
      }),
      col.accessor("truck_number", { header: "Truck", cell: (c) => c.getValue() ?? "—" }),
      col.accessor("amount", {
        header: "Amount",
        meta: { numeric: true },
        cell: (c) => (c.getValue() ? formatMoney(c.getValue(), { decimals: 0 }) : "—"),
      }),
    ],
    [showAge],
  );
}

export function DeliveriesListPage() {
  const list = useListParams({ sort: "-captured_at" });
  const query = usePagedList<DeliveryListItem>("deliveries", "/deliveries", list.query);
  const sites = useSiteOptions();
  const vendors = useVendorOptions();
  const columns = useDeliveryColumns(false);
  const canReview = useCan("deliveries.review");

  return (
    <>
      <PageHeader
        title="Deliveries"
        subtitle="Every truck that arrived, priced and checked on the server. Anything odd is flagged, never refused."
        actions={
          <>
            {canReview && (
              <Button asChild>
                <Link to="/deliveries/review">Review queue</Link>
              </Button>
            )}
            <PermissionGate permission="deliveries.create">
              <Button variant="primary" asChild>
                <Link to="/deliveries/new">
                  <Plus /> Record delivery
                </Link>
              </Button>
            </PermissionGate>
          </>
        }
      />
      <PageBody>
        <DataTable
          tableId="deliveries"
          caption="Deliveries"
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
          getRowHref={(r) => `/deliveries/${r.id}`}
          toolbar={
            <FilterBar
              list={list}
              searchPlaceholder="Number, truck or challan…  ( / )"
              savedViewsId="deliveries"
              filters={[
                { key: "status", label: "Status", options: STATUS_OPTIONS },
                { key: "site_id", label: "Site", options: sites.options },
                { key: "vendor_id", label: "Vendor", options: vendors.options },
                {
                  key: "has_open_flags",
                  label: "Flags",
                  options: [{ value: "true", label: "Open flags only" }],
                },
              ]}
            />
          }
        />
      </PageBody>
    </>
  );
}

export function ReviewQueuePage() {
  const list = useListParams({ limit: 50 });
  const query = usePagedList<DeliveryListItem>(
    "deliveries",
    "/deliveries/review-queue",
    list.query,
  );
  const columns = useDeliveryColumns(true);

  return (
    <>
      <PageHeader
        title="Delivery review"
        subtitle="Deliveries waiting for a decision — most severe first, and the one waiting longest first within a severity."
        crumbs={[{ label: "Deliveries", to: "/deliveries" }, { label: "Review" }]}
      />
      <PageBody>
        <DataTable
          tableId="delivery-review"
          caption="Deliveries waiting for review"
          columns={columns}
          rows={query.data?.items}
          total={query.data?.page.total}
          isLoading={query.isLoading}
          isFetching={query.isFetching}
          error={query.error}
          onRetry={() => void query.refetch()}
          offset={list.params.offset}
          limit={list.params.limit}
          onOffsetChange={list.setOffset}
          onLimitChange={list.setLimit}
          getRowId={(r) => r.id}
          getRowHref={(r) => `/deliveries/${r.id}`}
          empty={
            <EmptyState
              title="Nothing waiting"
              description="Every delivery has been decided. New flagged deliveries appear here as they arrive."
            />
          }
        />
      </PageBody>
    </>
  );
}

// --- Form ------------------------------------------------------------------------

/** Coordinates and accuracy carry more decimals than money or quantities do. */
const coordinate = (message: string) =>
  z
    .string()
    .trim()
    .refine((v) => v === "" || /^-?\d+(\.\d+)?$/.test(v), message);

const lineSchema = z.object({
  material_id: z.string().min(1, "Choose a material"),
  unit_id: z.string().min(1, "Choose a unit"),
  quantity: z
    .string()
    .trim()
    .refine((v) => DECIMAL_RE.test(v) && Number(v) > 0, "A positive number"),
  remarks: z.string(),
});

const schema = z.object({
  site_id: z.string().min(1, "Choose the site"),
  vendor_id: z.string().min(1, "Choose the vendor"),
  purchase_order_id: z.string(),
  truck_number: z.string().max(30),
  truck_type_id: z.string(),
  driver_name: z.string().max(120),
  driver_phone: z.string().max(32),
  challan_number: z.string().max(60),
  challan_date: z.string(),
  captured_at: z.string().min(1, "Required"),
  latitude: coordinate("A number"),
  longitude: coordinate("A number"),
  gps_accuracy_m: coordinate("A number"),
  remarks: z.string().max(1000),
  items: z.array(lineSchema).min(1, "Add at least one line"),
});
type Values = z.infer<typeof schema>;

const emptyLine = { material_id: "", unit_id: "", quantity: "", remarks: "" };

export function DeliveryFormPage() {
  const { deliveryId } = useParams();
  const existing = useQuery({
    queryKey: ["deliveries", "detail", deliveryId],
    queryFn: () => api.get<DeliveryRead>(`/deliveries/${deliveryId}`),
    enabled: Boolean(deliveryId),
    staleTime: 0,
  });
  if (deliveryId && existing.isLoading) return <PageSkeleton />;
  if (deliveryId && existing.error) {
    return <ErrorState error={existing.error} onRetry={() => void existing.refetch()} />;
  }
  return <DeliveryForm delivery={existing.data} />;
}

function DeliveryForm({ delivery }: { delivery?: DeliveryRead }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sites = useSiteOptions();
  const vendors = useVendorOptions();
  const materials = useMaterialOptions();
  const units = useUnitOptions();
  const trucks = useTruckTypeOptions();
  const canSeeOrders = useCan("procurement.po.view");
  const [formError, setFormError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: delivery
      ? {
          site_id: delivery.site_id,
          vendor_id: delivery.vendor_id,
          purchase_order_id: delivery.purchase_order_id ?? "",
          truck_number: delivery.truck_number ?? "",
          truck_type_id: delivery.truck_type_id ?? "",
          driver_name: delivery.driver_name ?? "",
          driver_phone: delivery.driver_phone ?? "",
          challan_number: delivery.challan_number ?? "",
          challan_date: delivery.challan_date ?? "",
          captured_at: toLocalInput(delivery.captured_at),
          latitude: delivery.captured_lat ?? "",
          longitude: delivery.captured_lng ?? "",
          gps_accuracy_m: delivery.gps_accuracy_m ?? "",
          remarks: delivery.remarks ?? "",
          items: delivery.items.map((i) => ({
            material_id: i.material_id,
            unit_id: i.unit_id,
            quantity: plainQty(i.quantity),
            remarks: i.remarks ?? "",
          })),
        }
      : {
          site_id: "",
          vendor_id: "",
          purchase_order_id: "",
          truck_number: "",
          truck_type_id: "",
          driver_name: "",
          driver_phone: "",
          challan_number: "",
          challan_date: "",
          captured_at: toLocalInput(),
          latitude: "",
          longitude: "",
          gps_accuracy_m: "",
          remarks: "",
          items: [{ ...emptyLine }],
        },
  });
  const lines = useFieldArray({ control: form.control, name: "items" });
  const vendorId = form.watch("vendor_id");
  const errors = form.formState.errors;

  const orders = useQuery({
    queryKey: ["purchase-orders", "receivable", vendorId],
    queryFn: () =>
      api.get<Page<PurchaseOrderListItem>>("/purchase-orders", {
        query: {
          vendor_id: vendorId,
          status: ["APPROVED", "SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"],
          limit: 50,
        },
      }),
    enabled: canSeeOrders && Boolean(vendorId),
  });

  const locate = () => {
    if (!("geolocation" in navigator)) {
      toast.error("This browser cannot provide a location.");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        form.setValue("latitude", pos.coords.latitude.toFixed(7));
        form.setValue("longitude", pos.coords.longitude.toFixed(7));
        form.setValue("gps_accuracy_m", pos.coords.accuracy.toFixed(1));
        setLocating(false);
      },
      (err) => {
        setLocating(false);
        toast.error(`Could not get a location: ${err.message}`);
      },
      { enableHighAccuracy: true, timeout: 15_000 },
    );
  };

  const save = useMutation({
    mutationFn: (values: Values) => {
      const hasPoint = values.latitude !== "" && values.longitude !== "";
      const body = {
        ...emptyToNull({
          site_id: values.site_id,
          vendor_id: values.vendor_id,
          purchase_order_id: values.purchase_order_id,
          truck_number: values.truck_number,
          truck_type_id: values.truck_type_id,
          driver_name: values.driver_name,
          driver_phone: values.driver_phone,
          challan_number: values.challan_number,
          challan_date: values.challan_date,
          remarks: values.remarks,
        }),
        captured_at: new Date(values.captured_at).toISOString(),
        latitude: hasPoint ? values.latitude : null,
        longitude: hasPoint ? values.longitude : null,
        gps_accuracy_m: hasPoint && values.gps_accuracy_m ? values.gps_accuracy_m : null,
        location_source: hasPoint ? "GPS" : "MANUAL",
        items: values.items.map((i) => emptyToNull(i)),
      } as unknown as DeliveryCreate;
      return delivery
        ? api.put<DeliveryRead>(`/deliveries/${delivery.id}`, body)
        : api.post<DeliveryRead>("/deliveries", body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      const open = saved.flags.filter((f) => f.status === "OPEN").length;
      toast.success(
        open
          ? `Recorded ${saved.delivery_number}. ${open} flag${open === 1 ? "" : "s"} raised — sent for review.`
          : `Recorded ${saved.delivery_number}.`,
      );
      navigate(`/deliveries/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, [
        "site_id",
        "vendor_id",
        "purchase_order_id",
        "truck_type_id",
        "captured_at",
      ]);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  const hasPoint = form.watch("latitude") !== "" && form.watch("longitude") !== "";

  return (
    <>
      <PageHeader
        title={delivery ? `Correct ${delivery.delivery_number}` : "Record a delivery"}
        subtitle={
          delivery
            ? "It will be checked again exactly as a new delivery is."
            : "It is always saved. Anything unusual is flagged for head office, not refused."
        }
        crumbs={[
          { label: "Deliveries", to: "/deliveries" },
          ...(delivery
            ? [{ label: delivery.delivery_number, to: `/deliveries/${delivery.id}` }]
            : []),
          { label: delivery ? "Correct" : "Record" },
        ]}
      />
      <PageBody className="max-w-4xl">
        <form
          onSubmit={(e) => {
            setFormError(null);
            void form.handleSubmit((v) => save.mutate(v))(e);
          }}
          className="flex flex-col gap-5"
          noValidate
        >
          {formError && <FormAlert>{formError}</FormAlert>}
          {delivery?.reviews[0]?.action === "REQUEST_CORRECTION" && (
            <FormAlert>
              {delivery.reviews[0].reviewer_name ?? "The reviewer"} asked:{" "}
              {delivery.reviews[0].comments}
            </FormAlert>
          )}

          <FormSection title="Where it came from">
            <FormGrid>
              <FormField label="Site" required error={errors.site_id?.message}>
                <Select {...form.register("site_id")} disabled={Boolean(delivery)}>
                  <option value="">Choose…</option>
                  {sites.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Vendor" required error={errors.vendor_id?.message}>
                <Select
                  {...form.register("vendor_id", {
                    onChange: () => form.setValue("purchase_order_id", ""),
                  })}
                >
                  <option value="">Choose…</option>
                  {vendors.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              {canSeeOrders && (
                <FormField
                  label="Purchase order"
                  hint="Optional. Without one the delivery is reviewed at head office."
                  error={errors.purchase_order_id?.message}
                >
                  <Select {...form.register("purchase_order_id")} disabled={!vendorId}>
                    <option value="">No purchase order</option>
                    {(orders.data?.items ?? []).map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.po_number}
                        {o.expected_delivery_date &&
                          ` — due ${formatDate(o.expected_delivery_date)}`}
                      </option>
                    ))}
                  </Select>
                </FormField>
              )}
              <FormField label="Captured at" required error={errors.captured_at?.message}>
                <Input type="datetime-local" {...form.register("captured_at")} />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Material">
            {errors.items?.message && (
              <p className="mb-2 text-xs text-danger">{errors.items.message}</p>
            )}
            <div className="flex flex-col gap-3">
              {lines.fields.map((field, i) => {
                const row = errors.items?.[i];
                return (
                  <div key={field.id} className="rounded-md border border-border p-3">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_1fr_auto]">
                      <FormField label={`Line ${i + 1} material`} error={row?.material_id?.message}>
                        <Select
                          {...form.register(`items.${i}.material_id`, {
                            onChange: (e: { target: { value: string } }) => {
                              const picked = materials.rows.find((m) => m.id === e.target.value);
                              if (picked) form.setValue(`items.${i}.unit_id`, picked.base_unit_id);
                            },
                          })}
                        >
                          <option value="">Choose…</option>
                          {materials.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <FormField label="Quantity" error={row?.quantity?.message}>
                        <Input
                          inputMode="decimal"
                          className="tabular"
                          {...form.register(`items.${i}.quantity`)}
                        />
                      </FormField>
                      <FormField label="Unit" error={row?.unit_id?.message}>
                        <Select {...form.register(`items.${i}.unit_id`)}>
                          <option value="">Choose…</option>
                          {units.options.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      </FormField>
                      <div className="flex items-end pb-0.5">
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Remove line ${i + 1}`}
                          onClick={() => lines.remove(i)}
                          disabled={lines.fields.length === 1}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
              <div>
                <Button size="sm" onClick={() => lines.append({ ...emptyLine })}>
                  <Plus /> Add line
                </Button>
              </div>
            </div>
            <p className="mt-2 text-xs text-fg-subtle">
              Rates and conversions are worked out by the server from the capture time. You never
              enter a price here.
            </p>
          </FormSection>

          <FormSection title="Truck and challan">
            <FormGrid>
              <FormField label="Truck number" error={errors.truck_number?.message}>
                <Input {...form.register("truck_number")} placeholder="e.g. LEB-1234" />
              </FormField>
              <FormField label="Truck type" error={errors.truck_type_id?.message}>
                <Select {...form.register("truck_type_id")}>
                  <option value="">Not stated</option>
                  {trucks.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Driver">
                <Input {...form.register("driver_name")} />
              </FormField>
              <FormField label="Driver phone">
                <Input {...form.register("driver_phone")} />
              </FormField>
              <FormField label="Challan number">
                <Input {...form.register("challan_number")} />
              </FormField>
              <FormField label="Challan date">
                <Input type="date" {...form.register("challan_date")} />
              </FormField>
              <FormField label="Remarks" className="sm:col-span-2">
                <Textarea rows={2} {...form.register("remarks")} />
              </FormField>
            </FormGrid>
          </FormSection>

          {!delivery && (
            <FormSection title="Location">
              <div className="flex flex-wrap items-center gap-3">
                <Button onClick={locate} loading={locating}>
                  <LocateFixed /> Use my current location
                </Button>
                {hasPoint ? (
                  <span className="text-sm text-fg-muted">
                    {form.watch("latitude")}, {form.watch("longitude")} · ±
                    {form.watch("gps_accuracy_m") || "?"} m
                  </span>
                ) : (
                  <span className="text-sm text-fg-muted">
                    No position captured. The delivery is still saved, and flagged for review.
                  </span>
                )}
                {(errors.latitude || errors.longitude || errors.gps_accuracy_m) && (
                  <span role="alert" className="text-xs text-danger">
                    The position is not a valid number. Capture it again.
                  </span>
                )}
              </div>
            </FormSection>
          )}

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {delivery ? "Submit correction" : "Record delivery"}
            </Button>
            <Button variant="ghost" onClick={() => navigate(-1)} disabled={save.isPending}>
              Cancel
            </Button>
          </div>
        </form>
      </PageBody>
    </>
  );
}

// --- Detail ----------------------------------------------------------------------

type Dialogs = null | "approve" | "reject" | "correction" | "reopen" | "order";

export function DeliveryDetailPage() {
  const { deliveryId = "" } = useParams();
  const canAttach = useCan("deliveries.create");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const [waiving, setWaiving] = useState<DeliveryFlagRead | null>(null);

  const query = useQuery({
    queryKey: ["deliveries", "detail", deliveryId],
    queryFn: () => api.get<DeliveryRead>(`/deliveries/${deliveryId}`),
  });
  const refresh = () => queryClient.invalidateQueries();
  const receive = useMutation({
    mutationFn: () =>
      api.post<{ id: string; grn_number: string }>(`/deliveries/${deliveryId}/convert-to-grn`, {}),
    onSuccess: async (grn) => {
      await refresh();
      toast.success(`Drafted ${grn.grn_number}. Inspect it, then post it to stock.`);
      navigate(`/grns/${grn.id}`);
    },
    onError: (err) => toast.error(describeError(err)),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const d = query.data;
  const openCritical = d.flags.find((f) => f.status === "OPEN" && f.severity === "CRITICAL");
  const sentBack = d.status === "CORRECTION_REQUESTED" ? d.reviews[0] : undefined;

  return (
    <>
      <PageHeader
        title={d.delivery_number}
        crumbs={[{ label: "Deliveries", to: "/deliveries" }, { label: d.delivery_number }]}
        meta={
          <>
            <StatusBadge status={d.status} />
            <span className="text-sm text-fg-muted">
              {d.site_code} · {d.vendor_name}
            </span>
            <span className="text-sm text-fg-muted">
              Captured {formatDateTime(d.captured_at)}
              {d.was_offline && " (offline)"}
            </span>
          </>
        }
        actions={
          <>
            {d.status === "APPROVED" && !d.grn_id && (
              <PermissionGate permission="grn.create">
                <Button
                  variant="primary"
                  onClick={() => receive.mutate()}
                  loading={receive.isPending}
                >
                  <PackageCheck /> Receive into stock
                </Button>
              </PermissionGate>
            )}
            {d.can_correct && (
              <Button variant="primary" asChild>
                <Link to={`/deliveries/${d.id}/edit`}>
                  <Pencil /> Correct
                </Link>
              </Button>
            )}
            {d.can_approve && (
              <Button variant="primary" onClick={() => setDialog("approve")}>
                <Check /> Approve
              </Button>
            )}
            {d.can_request_correction && (
              <Button onClick={() => setDialog("correction")}>
                <CornerUpLeft /> Send back
              </Button>
            )}
            {d.can_attach_order && (
              <Button onClick={() => setDialog("order")}>
                <FilePlus2 /> Attach order
              </Button>
            )}
            {d.can_reject && (
              <Button variant="danger" onClick={() => setDialog("reject")}>
                <X /> Reject
              </Button>
            )}
            {d.can_reopen && (
              <Button onClick={() => setDialog("reopen")}>
                <RotateCcw /> Reopen
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {sentBack && (
          <FormAlert>
            Sent back{sentBack.reviewer_name ? ` by ${sentBack.reviewer_name}` : ""}:{" "}
            {sentBack.comments}
          </FormAlert>
        )}
        {d.status === "REJECTED" && d.rejection_reason && (
          <FormAlert>Rejected: {d.rejection_reason}</FormAlert>
        )}

        <Section title={`Flags (${d.flags.length})`}>
          {d.flags.length === 0 ? (
            <p className="px-4 py-4 text-sm text-fg-muted">
              Nothing unusual: the delivery passed every check.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {d.flags.map((f) => (
                <li
                  key={f.id}
                  className="flex flex-wrap items-start justify-between gap-3 px-4 py-3"
                >
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={f.severity} />
                      <span className="font-medium">{humanize(f.flag_type)}</span>
                      <span className="text-xs text-fg-muted">
                        <StatusBadge status={f.status} />
                      </span>
                    </p>
                    <p className="mt-0.5 text-sm">{f.message}</p>
                    {f.resolution_note && (
                      <p className="mt-0.5 text-xs text-fg-muted">
                        {humanize(f.status)}: {f.resolution_note}
                      </p>
                    )}
                    {Object.keys(f.rule_snapshot).length > 0 && (
                      <p className="mt-0.5 text-xs text-fg-subtle">
                        Rule in force at the time:{" "}
                        {String((f.rule_snapshot as { name?: string }).name ?? "unnamed")}
                      </p>
                    )}
                  </div>
                  {d.can_waive && f.status === "OPEN" && (
                    <Button size="sm" onClick={() => setWaiving(f)}>
                      <Ban /> Waive
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Load">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Material delivered</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Material
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Quantity
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Priced quantity
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Rate
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-fg-muted">{i.material_sku}</span>{" "}
                      <span className="font-medium">{i.material_name}</span>
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(i.quantity, i.unit_code ?? undefined)}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {i.converted_quantity ? (
                        <span title={`× ${plainQty(i.conversion_factor ?? "1")}`}>
                          {formatQuantity(i.converted_quantity, i.converted_unit_code ?? undefined)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {d.prices_hidden ? (
                        <span className="text-fg-subtle">Hidden</span>
                      ) : i.rate ? (
                        formatMoney(i.rate)
                      ) : (
                        <span className="text-warning">Not priced</span>
                      )}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {d.prices_hidden ? "" : i.amount ? formatMoney(i.amount) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
              {!d.prices_hidden && d.total_amount && (
                <tfoot>
                  <tr className="border-t border-border-strong bg-surface">
                    <td colSpan={4} className="px-4 py-2 text-right font-semibold">
                      Total
                    </td>
                    <td data-numeric className="px-4 py-2 font-semibold">
                      {formatMoney(d.total_amount)}
                    </td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </Section>

        <Section title="Details">
          <FieldGrid
            items={[
              { label: "Site", value: `${d.site_code} — ${d.site_name}` },
              { label: "Vendor", value: `${d.vendor_code} — ${d.vendor_name}` },
              {
                label: "Purchase order",
                value: d.purchase_order_id ? (
                  <Link
                    to={`/purchase-orders/${d.purchase_order_id}`}
                    className="font-mono text-primary hover:underline"
                  >
                    {d.purchase_order_number}
                  </Link>
                ) : (
                  "None"
                ),
              },
              {
                label: "Truck",
                value: [d.truck_number, d.truck_type_name].filter(Boolean).join(" · ") || null,
              },
              {
                label: "Driver",
                value: [d.driver_name, d.driver_phone].filter(Boolean).join(" · ") || null,
              },
              {
                label: "Challan",
                value:
                  [d.challan_number, d.challan_date ? formatDate(d.challan_date) : null]
                    .filter(Boolean)
                    .join(" · ") || null,
              },
              {
                label: "Goods received note",
                value: d.grn_id ? (
                  <Link to={`/grns/${d.grn_id}`} className="text-primary hover:underline">
                    Open the GRN
                  </Link>
                ) : (
                  "None yet"
                ),
              },
              { label: "Location", value: geofenceSummary(d) },
              { label: "Reached the server", value: formatDateTime(d.received_at) },
              { label: "Device clock", value: skewLabel(d.clock_skew_seconds) },
              { label: "Captured by", value: d.submitted_by_name },
              { label: "Remarks", value: d.remarks, wide: true },
            ]}
          />
        </Section>

        <Section title="Review history">
          {d.reviews.length === 0 ? (
            <p className="px-4 py-4 text-sm text-fg-muted">No decisions yet.</p>
          ) : (
            <ol className="divide-y divide-border">
              {d.reviews.map((r) => (
                <li key={r.id} className="px-4 py-2.5 text-sm">
                  <p>
                    <span className="font-medium">{humanize(r.action)}</span>{" "}
                    <span className="text-fg-muted">
                      by {r.reviewer_name ?? "—"} · {formatDateTime(r.reviewed_at)} ·{" "}
                      {humanize(r.previous_status)} → {humanize(r.new_status)}
                    </span>
                  </p>
                  {r.comments && <p className="text-xs text-fg-muted">“{r.comments}”</p>}
                </li>
              ))}
            </ol>
          )}
        </Section>

        <AttachmentsSection
          entityType="delivery"
          entityId={d.id}
          canUpload={canAttach && !["REJECTED", "CANCELLED"].includes(d.status)}
          documentTypes={["PHOTO", "CHALLAN", "OTHER"]}
          title="Photos and challan"
        />
      </PageBody>

      <ConfirmDialog
        open={dialog === "approve"}
        onOpenChange={(o) => setDialog(o ? "approve" : null)}
        title={`Approve ${d.delivery_number}?`}
        description={
          d.has_open_flags
            ? "Approving accepts its open flags. They stay on the record with your note."
            : "Your approval is recorded against your name."
        }
        confirmLabel={`Approve ${d.delivery_number}`}
        reason={{
          label: openCritical ? "Why is this acceptable?" : "Comment (optional)",
          required: Boolean(openCritical),
          minLength: openCritical ? 5 : undefined,
        }}
        onConfirm={async (comments) => {
          await api.post(`/deliveries/${d.id}/approve`, { comments: comments || null });
          await refresh();
          toast.success(`${d.delivery_number} approved.`);
        }}
      >
        {openCritical && (
          <p className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
            Critical flag: {openCritical.message}
          </p>
        )}
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === "reject"}
        onOpenChange={(o) => setDialog(o ? "reject" : null)}
        title={`Reject ${d.delivery_number}?`}
        description="The delivery stays on the record, marked rejected. It no longer counts against its order."
        confirmLabel="Reject delivery"
        destructive
        reason={{ label: "Reason", required: true, minLength: 5 }}
        onConfirm={async (comments) => {
          await api.post(`/deliveries/${d.id}/reject`, { comments });
          await refresh();
          toast.success(`${d.delivery_number} rejected.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "correction"}
        onOpenChange={(o) => setDialog(o ? "correction" : null)}
        title={`Send ${d.delivery_number} back?`}
        description="It returns to the person who captured it, with your note, to be corrected and checked again."
        confirmLabel="Send back"
        reason={{ label: "What needs correcting", required: true, minLength: 5 }}
        onConfirm={async (comments) => {
          await api.post(`/deliveries/${d.id}/request-correction`, { comments });
          await refresh();
          toast.success("Sent back for correction.");
        }}
      />
      <ConfirmDialog
        open={dialog === "reopen"}
        onOpenChange={(o) => setDialog(o ? "reopen" : null)}
        title={`Reopen ${d.delivery_number}?`}
        description="It goes back to the review queue."
        confirmLabel="Reopen"
        reason={{ label: "Why", required: true, minLength: 5 }}
        onConfirm={async (comments) => {
          await api.post(`/deliveries/${d.id}/reopen`, { comments });
          await refresh();
          toast.success(`${d.delivery_number} reopened.`);
        }}
      />
      <ConfirmDialog
        open={Boolean(waiving)}
        onOpenChange={(o) => !o && setWaiving(null)}
        title="Waive this flag?"
        description={waiving?.message}
        confirmLabel="Waive flag"
        reason={{ label: "Why is it not a problem", required: true, minLength: 5 }}
        onConfirm={async (note) => {
          await api.post(`/delivery-flags/${waiving?.id}/waive`, { note });
          await refresh();
          toast.success("Flag waived.");
        }}
      />
      {dialog === "order" && <AttachOrderDialog delivery={d} onClose={() => setDialog(null)} />}
    </>
  );
}

function AttachOrderDialog({ delivery, onClose }: { delivery: DeliveryRead; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [orderId, setOrderId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const orders = useQuery({
    queryKey: ["purchase-orders", "receivable", delivery.vendor_id],
    queryFn: () =>
      api.get<Page<PurchaseOrderListItem>>("/purchase-orders", {
        query: {
          vendor_id: delivery.vendor_id,
          status: ["APPROVED", "SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"],
          limit: 50,
        },
      }),
  });
  const attach = useMutation({
    mutationFn: () =>
      api.post(`/deliveries/${delivery.id}/attach-purchase-order`, { purchase_order_id: orderId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries();
      toast.success("Order attached and its balance re-checked.");
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Attach a purchase order"
      description={`Orders placed with ${delivery.vendor_name} that can still be received against. The order's balance is checked as if it had been named at capture.`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!orderId}
            loading={attach.isPending}
            onClick={() => {
              setError(null);
              attach.mutate();
            }}
          >
            Attach order
          </Button>
        </>
      }
    >
      {orders.isLoading ? (
        <p className="text-sm text-fg-muted">Loading…</p>
      ) : (orders.data?.items.length ?? 0) === 0 ? (
        <p className="text-sm text-fg-muted">
          This vendor has no order that can be received against.
        </p>
      ) : (
        <Select
          value={orderId}
          onChange={(e) => setOrderId(e.target.value)}
          aria-label="Purchase order"
        >
          <option value="">Choose an order…</option>
          {orders.data?.items.map((o) => (
            <option key={o.id} value={o.id}>
              {o.po_number} — {humanize(o.status)}
            </option>
          ))}
        </Select>
      )}
      {error && (
        <p role="alert" className="mt-3 rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
    </Dialog>
  );
}
