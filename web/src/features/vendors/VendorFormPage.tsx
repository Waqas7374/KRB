import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { useNavigate, useParams } from "react-router-dom";
import { z } from "zod";

import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { FormField, FormGrid, FormSection } from "@/components/ui/form-field";
import { Checkbox, Input, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { FormAlert } from "@/features/auth/auth-layout";
import { api } from "@/lib/api";
import { vendorTypeOptions } from "@/lib/enums";
import { applyServerErrors } from "@/lib/errors";
import { codeField, DECIMAL_RE, dirtyValues, emptyToNull, str } from "@/lib/forms";
import { toast } from "@/lib/toast";
import type { VendorCreate, VendorDetail, VendorRead } from "@/types/models";

// Patterns copied from backend/app/modules/vendors/schemas.py.
const NTN_RE = /^\d{7}-\d$/;
const STRN_RE = /^\d{13}$/;
const CNIC_RE = /^\d{5}-\d{7}-\d$/;

const optional = (re: RegExp, message: string) =>
  z
    .string()
    .trim()
    .refine((v) => v === "" || re.test(v), message);

const contactSchema = z
  .object({
    name: z.string().trim().min(2, "At least 2 characters"),
    designation: z.string(),
    phone: z.string(),
    email: z.string(),
    is_primary: z.boolean(),
  })
  .refine((c) => c.phone.trim() || c.email.trim(), {
    path: ["phone"],
    message: "A phone number or an email is required",
  });

const schema = z
  .object({
    // Optional on create (auto-numbered when blank).
    code: z.union([z.literal(""), codeField]),
    legal_name: z.string().trim().min(2, "At least 2 characters").max(200),
    trade_name: z.string().max(200),
    vendor_type: z.string(),
    ntn: optional(NTN_RE, "Format 1234567-8"),
    strn: optional(STRN_RE, "13 digits"),
    cnic: optional(CNIC_RE, "Format 35202-1234567-8"),
    is_filer: z.enum(["", "yes", "no"]),
    withholding_exempt: z.boolean(),
    payment_terms_days: z.coerce.number().int().min(0).max(365),
    credit_limit: optional(DECIMAL_RE, "A plain amount, e.g. 250000 or 250000.50"),
    phone: z.string().max(32),
    email: optional(/^[^\s@]+@[^\s@]+\.[^\s@]+$/, "Not a valid email address"),
    website: z.string().max(200),
    address: z.object({
      line1: z.string(),
      line2: z.string(),
      city: z.string(),
      province: z.string(),
      postal_code: z.string(),
    }),
    notes: z.string().max(2000),
    contacts: z.array(contactSchema),
  })
  // Same rule the API enforces: withholding tax needs a tax identity.
  .refine((v) => v.ntn.trim() || v.cnic.trim(), {
    path: ["ntn"],
    message: "Provide an NTN (registered business) or a CNIC (sole proprietor)",
  })
  .refine((v) => v.contacts.filter((c) => c.is_primary).length <= 1, {
    path: ["contacts"],
    message: "Only one contact can be primary",
  });

type Values = z.infer<typeof schema>;

const FIELDS = [
  "code",
  "legal_name",
  "trade_name",
  "vendor_type",
  "ntn",
  "strn",
  "cnic",
  "payment_terms_days",
  "credit_limit",
  "phone",
  "email",
  "website",
  "notes",
] as const;

function toValues(v?: VendorDetail): Values {
  const address = (v?.address ?? {}) as Record<string, string | null | undefined>;
  return {
    code: v?.code ?? "",
    legal_name: v?.legal_name ?? "",
    trade_name: str(v?.trade_name),
    vendor_type: v?.vendor_type ?? "MATERIAL_SUPPLIER",
    ntn: str(v?.ntn),
    strn: str(v?.strn),
    cnic: str(v?.cnic),
    is_filer: v?.is_filer === true ? "yes" : v?.is_filer === false ? "no" : "",
    withholding_exempt: v?.withholding_exempt ?? false,
    payment_terms_days: v?.payment_terms_days ?? 30,
    credit_limit: str(v?.credit_limit),
    phone: str(v?.phone),
    email: str(v?.email),
    website: str(v?.website),
    address: {
      line1: str(address.line1),
      line2: str(address.line2),
      city: str(address.city),
      province: str(address.province),
      postal_code: str(address.postal_code),
    },
    notes: str(v?.notes),
    contacts: [],
  };
}

function toPayload(values: Partial<Values>) {
  const { is_filer, contacts, address, ...rest } = values;
  const payload: Record<string, unknown> = emptyToNull(rest);
  if (is_filer !== undefined) payload.is_filer = is_filer === "" ? null : is_filer === "yes";
  if (address !== undefined) {
    const cleaned = emptyToNull(address);
    payload.address = Object.values(cleaned).some((x) => x !== null) ? cleaned : null;
  }
  if (contacts !== undefined) payload.contacts = contacts.map((c) => emptyToNull(c));
  return payload;
}

export function VendorFormPage() {
  const { vendorId } = useParams();
  const editing = Boolean(vendorId);
  const vendor = useQuery({
    queryKey: ["vendors", "detail", vendorId],
    queryFn: () => api.get<VendorDetail>(`/vendors/${vendorId}`),
    enabled: editing,
    staleTime: 0,
  });

  if (editing && vendor.isLoading) return <PageSkeleton />;
  if (editing && vendor.error)
    return <ErrorState error={vendor.error} onRetry={() => void vendor.refetch()} />;
  return <VendorForm vendor={vendor.data} />;
}

function VendorForm({ vendor }: { vendor?: VendorDetail }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: toValues(vendor) });
  const contacts = useFieldArray({ control: form.control, name: "contacts" });
  const errors = form.formState.errors;

  const save = useMutation({
    mutationFn: async (values: Values) => {
      if (!vendor) {
        const body = toPayload(values) as VendorCreate;
        return api.post<VendorRead>("/vendors", body);
      }
      const changed = dirtyValues(form.formState.dirtyFields, values);
      delete changed.code;
      delete changed.contacts;
      return api.patch<VendorRead>(`/vendors/${vendor.id}`, toPayload(changed), {
        ifMatch: String(vendor.version),
      });
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["vendors"] });
      toast.success(vendor ? `Saved ${saved.code}.` : `Created ${saved.code} as a draft.`);
      navigate(`/vendors/${saved.id}`);
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, FIELDS);
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  const onSubmit = form.handleSubmit((values) => {
    setFormError(null);
    save.mutate(values);
  });

  const title = vendor ? `Edit ${vendor.display_name}` : "New vendor";
  return (
    <>
      <PageHeader
        title={title}
        crumbs={[
          { label: "Vendors", to: "/vendors" },
          ...(vendor ? [{ label: vendor.code, to: `/vendors/${vendor.id}` }] : []),
          { label: vendor ? "Edit" : "New" },
        ]}
      />
      <PageBody className="max-w-4xl">
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-5" noValidate>
          {formError && <FormAlert>{formError}</FormAlert>}

          <FormSection title="Identity">
            <FormGrid>
              <FormField label="Legal name" required error={errors.legal_name?.message}>
                <Input {...form.register("legal_name")} autoFocus />
              </FormField>
              <FormField label="Trade name" error={errors.trade_name?.message}>
                <Input {...form.register("trade_name")} />
              </FormField>
              <FormField label="Type" error={errors.vendor_type?.message}>
                <Select {...form.register("vendor_type")}>
                  {vendorTypeOptions.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField
                label="Code"
                hint={vendor ? "Codes cannot be changed." : "Leave blank to number automatically."}
                error={errors.code?.message}
              >
                <Input
                  {...form.register("code")}
                  readOnly={Boolean(vendor)}
                  className="font-mono uppercase read-only:opacity-60"
                />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Tax">
            <FormGrid>
              <FormField label="NTN" hint="Registered businesses" error={errors.ntn?.message}>
                <Input {...form.register("ntn")} placeholder="1234567-8" className="font-mono" />
              </FormField>
              <FormField label="CNIC" hint="Sole proprietors" error={errors.cnic?.message}>
                <Input
                  {...form.register("cnic")}
                  placeholder="35202-1234567-8"
                  className="font-mono"
                />
              </FormField>
              <FormField label="STRN" error={errors.strn?.message}>
                <Input {...form.register("strn")} className="font-mono" />
              </FormField>
              <FormField label="Filer status (ATL)" error={errors.is_filer?.message}>
                <Select {...form.register("is_filer")}>
                  <option value="">Not recorded</option>
                  <option value="yes">Filer</option>
                  <option value="no">Non-filer</option>
                </Select>
              </FormField>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox {...form.register("withholding_exempt")} />
                Exempt from withholding tax
              </label>
            </FormGrid>
          </FormSection>

          <FormSection title="Commercial">
            <FormGrid>
              <FormField label="Payment terms (days)" error={errors.payment_terms_days?.message}>
                <Input type="number" min={0} max={365} {...form.register("payment_terms_days")} />
              </FormField>
              <FormField label="Credit limit (PKR)" error={errors.credit_limit?.message}>
                <Input inputMode="decimal" {...form.register("credit_limit")} className="tabular" />
              </FormField>
            </FormGrid>
          </FormSection>

          <FormSection title="Contact">
            <FormGrid>
              <FormField label="Phone" error={errors.phone?.message}>
                <Input type="tel" {...form.register("phone")} />
              </FormField>
              <FormField label="Email" error={errors.email?.message}>
                <Input type="email" {...form.register("email")} />
              </FormField>
              <FormField label="Website" error={errors.website?.message}>
                <Input {...form.register("website")} />
              </FormField>
              <FormField label="Address line 1">
                <Input {...form.register("address.line1")} />
              </FormField>
              <FormField label="Address line 2">
                <Input {...form.register("address.line2")} />
              </FormField>
              <FormField label="City">
                <Input {...form.register("address.city")} />
              </FormField>
              <FormField label="Province">
                <Input {...form.register("address.province")} />
              </FormField>
              <FormField label="Postal code">
                <Input {...form.register("address.postal_code")} />
              </FormField>
            </FormGrid>
          </FormSection>

          {!vendor && (
            <FormSection title="Contact people">
              {errors.contacts?.message && (
                <p className="mb-2 text-xs text-danger">{errors.contacts.message}</p>
              )}
              <div className="flex flex-col gap-3">
                {contacts.fields.map((field, i) => (
                  <div key={field.id} className="rounded-md border border-border p-3">
                    <FormGrid>
                      <FormField label="Name" required error={errors.contacts?.[i]?.name?.message}>
                        <Input {...form.register(`contacts.${i}.name`)} />
                      </FormField>
                      <FormField label="Designation">
                        <Input {...form.register(`contacts.${i}.designation`)} />
                      </FormField>
                      <FormField label="Phone" error={errors.contacts?.[i]?.phone?.message}>
                        <Input type="tel" {...form.register(`contacts.${i}.phone`)} />
                      </FormField>
                      <FormField label="Email">
                        <Input type="email" {...form.register(`contacts.${i}.email`)} />
                      </FormField>
                    </FormGrid>
                    <div className="mt-2 flex items-center justify-between">
                      <label className="flex items-center gap-2 text-sm">
                        <Checkbox {...form.register(`contacts.${i}.is_primary`)} /> Primary contact
                      </label>
                      <Button size="sm" variant="ghost" onClick={() => contacts.remove(i)}>
                        <Trash2 /> Remove
                      </Button>
                    </div>
                  </div>
                ))}
                <Button
                  size="sm"
                  className="self-start"
                  onClick={() =>
                    contacts.append({
                      name: "",
                      designation: "",
                      phone: "",
                      email: "",
                      is_primary: contacts.fields.length === 0,
                    })
                  }
                >
                  <Plus /> Add contact
                </Button>
              </div>
            </FormSection>
          )}

          <FormSection title="Notes">
            <FormField label="Internal notes" error={errors.notes?.message}>
              <Textarea {...form.register("notes")} />
            </FormField>
          </FormSection>

          <div className="flex gap-2 border-t border-border pt-4">
            <Button type="submit" variant="primary" loading={save.isPending}>
              {vendor ? "Save changes" : "Create vendor"}
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
