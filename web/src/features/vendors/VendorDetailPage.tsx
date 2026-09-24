import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeCheck,
  Ban,
  CheckCircle2,
  Pencil,
  PauseCircle,
  Plus,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { FieldGrid, PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField, FormGrid } from "@/components/ui/form-field";
import { Checkbox, Input } from "@/components/ui/input";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status-badge";
import { FormAlert } from "@/features/auth/auth-layout";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { applyServerErrors } from "@/lib/errors";
import { emptyToNull } from "@/lib/forms";
import { toast } from "@/lib/toast";
import { formatDateTime, formatMoney, humanize } from "@/lib/utils";
import type { VendorBankAccountRead, VendorDetail, VendorStatus } from "@/types/models";

// Mirrors ALLOWED_TRANSITIONS in backend vendor_service.py; the server is
// still the authority, this only decides which buttons to show.
const CAN_APPROVE: VendorStatus[] = ["DRAFT", "PENDING_APPROVAL", "SUSPENDED", "INACTIVE"];
const CAN_SUSPEND: VendorStatus[] = ["ACTIVE"];
const CAN_BLACKLIST: VendorStatus[] = ["ACTIVE", "SUSPENDED"];

export function VendorDetailPage() {
  const { vendorId = "" } = useParams();
  const queryClient = useQueryClient();
  const canUpdate = useCan("vendors.update");
  const canApprove = useCan("vendors.approve");
  const canSuspend = useCan("vendors.suspend");
  const [dialog, setDialog] = useState<null | "approve" | "suspend" | "blacklist">(null);

  const vendor = useQuery({
    queryKey: ["vendors", "detail", vendorId],
    queryFn: () => api.get<VendorDetail>(`/vendors/${vendorId}`),
  });

  if (vendor.isLoading) return <PageSkeleton />;
  if (vendor.error || !vendor.data) {
    return <ErrorState error={vendor.error} onRetry={() => void vendor.refetch()} />;
  }
  const v = vendor.data;
  const status = v.status as VendorStatus;
  const address = (v.address ?? {}) as Record<string, string | null>;
  const addressText = [
    address.line1,
    address.line2,
    address.city,
    address.province,
    address.postal_code,
  ]
    .filter(Boolean)
    .join(", ");

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["vendors"] });

  return (
    <>
      <PageHeader
        title={v.display_name}
        crumbs={[{ label: "Vendors", to: "/vendors" }, { label: v.code }]}
        meta={
          <>
            <StatusBadge status={v.status} />
            <Tag>{humanize(v.vendor_type)}</Tag>
            <span className="font-mono text-sm text-fg-muted">{v.code}</span>
            {v.is_tradeable ? (
              <span className="text-sm text-success">Can be used on purchase orders</span>
            ) : (
              <span className="text-sm text-fg-muted">Not yet approved for trading</span>
            )}
          </>
        }
        actions={
          <>
            {canUpdate && status !== "BLACKLISTED" && (
              <Button asChild>
                <Link to={`/vendors/${v.id}/edit`}>
                  <Pencil /> Edit
                </Link>
              </Button>
            )}
            {canApprove && CAN_APPROVE.includes(status) && (
              <Button variant="primary" onClick={() => setDialog("approve")}>
                <CheckCircle2 /> {status === "SUSPENDED" ? "Reinstate" : "Approve"}
              </Button>
            )}
            {canSuspend && CAN_SUSPEND.includes(status) && (
              <Button onClick={() => setDialog("suspend")}>
                <PauseCircle /> Suspend
              </Button>
            )}
            {canSuspend && CAN_BLACKLIST.includes(status) && (
              <Button variant="danger" onClick={() => setDialog("blacklist")}>
                <Ban /> Blacklist
              </Button>
            )}
          </>
        }
      />
      <PageBody>
        {v.suspension_reason && (
          <FormAlert>
            {status === "BLACKLISTED" ? "Blacklisted" : "Suspended"}: {v.suspension_reason}
          </FormAlert>
        )}
        <Section title="Details">
          <FieldGrid
            items={[
              { label: "Legal name", value: v.legal_name },
              { label: "Trade name", value: v.trade_name },
              { label: "NTN", value: v.ntn, mono: true },
              { label: "CNIC", value: v.cnic, mono: true },
              { label: "STRN", value: v.strn, mono: true },
              {
                label: "Filer status",
                value: v.is_filer === null ? "Not recorded" : v.is_filer ? "Filer" : "Non-filer",
              },
              { label: "Withholding exempt", value: v.withholding_exempt ? "Yes" : "No" },
              { label: "Payment terms", value: `${v.payment_terms_days} days` },
              { label: "Credit limit", value: v.credit_limit ? formatMoney(v.credit_limit) : null },
              { label: "Phone", value: v.phone },
              { label: "Email", value: v.email },
              { label: "Website", value: v.website },
              { label: "Address", value: addressText, wide: true },
              { label: "Notes", value: v.notes, wide: true },
              { label: "Approved", value: v.approved_at ? formatDateTime(v.approved_at) : null },
              { label: "Created", value: formatDateTime(v.created_at) },
              { label: "Last updated", value: formatDateTime(v.updated_at) },
            ]}
          />
        </Section>

        <Section title="Contacts">
          {v.contacts?.length ? (
            <table className="w-full text-sm">
              <caption className="sr-only">Contacts</caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Name
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Designation
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Phone
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Email
                  </th>
                </tr>
              </thead>
              <tbody>
                {v.contacts.map((c) => (
                  <tr key={c.id} className="border-t border-border">
                    <td className="px-4 py-2 font-medium">
                      {c.name} {c.is_primary && <Tag className="ml-1">Primary</Tag>}
                    </td>
                    <td className="px-4 py-2">{c.designation ?? "—"}</td>
                    <td className="px-4 py-2">{c.phone ?? "—"}</td>
                    <td className="px-4 py-2">{c.email ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-4 py-4 text-sm text-fg-muted">No contact people recorded.</p>
          )}
        </Section>

        <BankAccounts vendorId={v.id} />
      </PageBody>

      <ConfirmDialog
        open={dialog === "approve"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`${status === "SUSPENDED" ? "Reinstate" : "Approve"} ${v.display_name}?`}
        description="The vendor becomes available on RFQs and purchase orders."
        confirmLabel={`${status === "SUSPENDED" ? "Reinstate" : "Approve"} ${v.code}`}
        reason={{ label: "Note (optional)" }}
        onConfirm={async (note) => {
          await api.post(`/vendors/${v.id}/approve`, { note: note || null });
          await refresh();
          toast.success(`${v.code} is approved for trading.`);
        }}
      />
      <ConfirmDialog
        open={dialog === "suspend" || dialog === "blacklist"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={`${dialog === "blacklist" ? "Blacklist" : "Suspend"} ${v.display_name}?`}
        description={
          dialog === "blacklist"
            ? "Blacklisting is permanent: the vendor can never be reinstated or edited."
            : "The vendor cannot be used on new documents until reinstated."
        }
        confirmLabel={`${dialog === "blacklist" ? "Blacklist" : "Suspend"} ${v.code}`}
        destructive
        reason={{
          label: "Reason",
          required: true,
          placeholder: "At least 5 characters; kept in the audit log",
        }}
        onConfirm={async (reason) => {
          await api.post(`/vendors/${v.id}/suspend`, { reason, blacklist: dialog === "blacklist" });
          await refresh();
          toast.success(`${v.code} ${dialog === "blacklist" ? "blacklisted" : "suspended"}.`);
        }}
      />
    </>
  );
}

const bankSchema = z
  .object({
    account_title: z.string().trim().min(2, "At least 2 characters"),
    bank_name: z.string().trim().min(2, "At least 2 characters"),
    account_no: z.string().trim().max(40),
    iban: z
      .string()
      .trim()
      .transform((v) => v.replace(/\s+/g, "").toUpperCase())
      .refine((v) => v === "" || (v.length >= 15 && v.length <= 34), "15 to 34 characters"),
    branch_name: z.string(),
    branch_code: z.string(),
    is_primary: z.boolean(),
  })
  .refine((v) => v.account_no || v.iban, {
    path: ["account_no"],
    message: "Provide an account number or an IBAN",
  });
type BankValues = z.input<typeof bankSchema>;

function BankAccounts({ vendorId }: { vendorId: string }) {
  const allowed = useCan("vendors.manage_bank_details");
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const accounts = useQuery({
    queryKey: ["vendors", "bank-accounts", vendorId],
    queryFn: () => api.get<VendorBankAccountRead[]>(`/vendors/${vendorId}/bank-accounts`),
    enabled: allowed,
  });

  const form = useForm<BankValues>({
    resolver: zodResolver(bankSchema),
    defaultValues: {
      account_title: "",
      bank_name: "",
      account_no: "",
      iban: "",
      branch_name: "",
      branch_code: "",
      is_primary: false,
    },
  });

  const add = useMutation({
    mutationFn: (values: BankValues) =>
      api.post(`/vendors/${vendorId}/bank-accounts`, emptyToNull(values)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["vendors", "bank-accounts", vendorId] });
      toast.success("Bank account added. It must be verified before payments use it.");
      setAdding(false);
      form.reset();
    },
    onError: (err) => {
      const rest = applyServerErrors(err, form.setError, Object.keys(bankSchema.innerType().shape));
      setFormError(rest.length ? rest.join(" ") : null);
    },
  });

  const verify = useMutation({
    mutationFn: (id: string) => api.post(`/vendors/bank-accounts/${id}/verify`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["vendors", "bank-accounts", vendorId] });
      toast.success("Bank account verified.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Verification failed"),
  });

  // Bank details are restricted; users without the permission do not see
  // the section at all rather than an empty or masked one.
  if (!allowed) return null;
  const errors = form.formState.errors;

  return (
    <Section
      title="Bank accounts"
      actions={
        <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
          <Plus /> Add
        </Button>
      }
    >
      {accounts.isError ? (
        <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
      ) : accounts.data?.length === 0 ? (
        <EmptyState
          title="No bank accounts"
          description="Payments cannot be made until one is added and verified."
        />
      ) : (
        <table className="w-full text-sm">
          <caption className="sr-only">Bank accounts</caption>
          <thead className="bg-surface text-left text-xs text-fg-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-semibold">
                Title
              </th>
              <th scope="col" className="px-4 py-2 font-semibold">
                Bank
              </th>
              <th scope="col" className="px-4 py-2 font-semibold">
                Account
              </th>
              <th scope="col" className="px-4 py-2 font-semibold">
                Status
              </th>
              <th scope="col" className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {accounts.data?.map((a) => (
              <tr key={a.id} className="border-t border-border">
                <td className="px-4 py-2 font-medium">
                  {a.account_title} {a.is_primary && <Tag className="ml-1">Primary</Tag>}
                </td>
                <td className="px-4 py-2">
                  {a.bank_name}
                  {a.branch_name && <span className="text-fg-muted"> · {a.branch_name}</span>}
                </td>
                <td className="px-4 py-2 font-mono">{a.masked_account}</td>
                <td className="px-4 py-2">
                  {a.is_verified ? (
                    <span className="inline-flex items-center gap-1 text-success">
                      <BadgeCheck className="size-3.5" /> Verified
                    </span>
                  ) : (
                    <StatusBadge status="PENDING" tone="warning" />
                  )}
                </td>
                <td className="px-4 py-2 text-right">
                  {!a.is_verified && (
                    <Button
                      size="sm"
                      onClick={() => verify.mutate(a.id)}
                      loading={verify.isPending && verify.variables === a.id}
                    >
                      <ShieldCheck /> Mark verified
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Dialog
        open={adding}
        onOpenChange={setAdding}
        title="Add bank account"
        variant="drawer"
        footer={
          <>
            <Button onClick={() => setAdding(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={add.isPending}
              onClick={() => void form.handleSubmit((v) => add.mutate(v))()}
            >
              Add account
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          {formError && <FormAlert>{formError}</FormAlert>}
          <FormGrid>
            <FormField label="Account title" required error={errors.account_title?.message}>
              <Input {...form.register("account_title")} />
            </FormField>
            <FormField label="Bank" required error={errors.bank_name?.message}>
              <Input {...form.register("bank_name")} />
            </FormField>
            <FormField label="Account number" error={errors.account_no?.message}>
              <Input {...form.register("account_no")} className="font-mono" />
            </FormField>
            <FormField label="IBAN" error={errors.iban?.message}>
              <Input
                {...form.register("iban")}
                className="font-mono uppercase"
                placeholder="PK36SCBL0000001123456702"
              />
            </FormField>
            <FormField label="Branch">
              <Input {...form.register("branch_name")} />
            </FormField>
            <FormField label="Branch code">
              <Input {...form.register("branch_code")} />
            </FormField>
          </FormGrid>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox {...form.register("is_primary")} /> Primary account for payments
          </label>
        </div>
      </Dialog>
    </Section>
  );
}
