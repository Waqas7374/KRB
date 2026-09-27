import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, RotateCcw, Unlock } from "lucide-react";
import { useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dialog } from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { PermissionGate } from "@/features/auth/permission-gate";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { describeError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { formatDate } from "@/lib/utils";
import type { PeriodRead } from "@/types/models";

type Dialogs = null | "close" | "reopen" | "lock";

export function PeriodsPage() {
  const canManage = useCan("finance.period.close");
  const queryClient = useQueryClient();
  const [generating, setGenerating] = useState(false);
  const [dialog, setDialog] = useState<{ period: PeriodRead; action: Dialogs } | null>(null);
  const query = useQuery({
    queryKey: ["finance-periods"],
    queryFn: () => api.get<PeriodRead[]>("/finance/periods"),
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const byYear = new Map<number, PeriodRead[]>();
  for (const p of query.data) {
    byYear.set(p.fiscal_year, [...(byYear.get(p.fiscal_year) ?? []), p]);
  }
  const years = [...byYear.keys()].sort((a, b) => b - a);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["finance-periods"] });

  return (
    <>
      <PageHeader
        title="Accounting periods"
        subtitle="Twelve months a fiscal year. Nothing posts into a closed period, and a locked one never
          opens again."
        actions={
          <PermissionGate permission="finance.period.close">
            <Button variant="primary" onClick={() => setGenerating(true)}>
              <Plus /> Generate a fiscal year
            </Button>
          </PermissionGate>
        }
      />
      <PageBody>
        {years.length === 0 && (
          <p className="text-sm text-fg-muted">No periods yet. Generate the current fiscal year.</p>
        )}
        {years.map((year) => (
          <div key={year} className="mb-6 overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <caption className="border-b border-border bg-surface px-4 py-2 text-left font-semibold">
                FY {year}
              </caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Period
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Dates
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Closed
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {byYear
                  .get(year)!
                  .sort((a, b) => a.period_no - b.period_no)
                  .map((p) => (
                    <tr key={p.id} className="border-t border-border">
                      <td className="px-4 py-2 font-medium">{p.period_no}</td>
                      <td className="px-4 py-2 text-fg-muted">
                        {formatDate(p.start_date)} – {formatDate(p.end_date)}
                      </td>
                      <td className="px-4 py-2">
                        <StatusBadge status={p.status} />
                      </td>
                      <td className="px-4 py-2 text-fg-muted">
                        {p.closed_at ? formatDate(p.closed_at) : "—"}
                      </td>
                      <td className="px-4 py-2">
                        {canManage && (
                          <div className="flex justify-end gap-1">
                            {p.status === "OPEN" && (
                              <Button
                                size="sm"
                                onClick={() => setDialog({ period: p, action: "close" })}
                              >
                                Close
                              </Button>
                            )}
                            {p.status === "CLOSED" && (
                              <>
                                <Button
                                  size="sm"
                                  onClick={() => setDialog({ period: p, action: "reopen" })}
                                >
                                  <RotateCcw /> Reopen
                                </Button>
                                <Button
                                  size="sm"
                                  variant="danger"
                                  onClick={() => setDialog({ period: p, action: "lock" })}
                                >
                                  <Lock /> Lock
                                </Button>
                              </>
                            )}
                            {p.status === "LOCKED" && (
                              <span className="flex items-center gap-1 text-xs text-fg-subtle">
                                <Unlock className="size-3.5" /> Never reopens
                              </span>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ))}
      </PageBody>

      {generating && (
        <GenerateDialog onClose={() => setGenerating(false)} onDone={() => void refresh()} />
      )}

      <ConfirmDialog
        open={dialog?.action === "close"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={
          dialog ? `Close period ${dialog.period.period_no} of FY${dialog.period.fiscal_year}?` : ""
        }
        description="Nothing can post into it until it is reopened."
        confirmLabel="Close period"
        onConfirm={async () => {
          await api.post(`/finance/periods/${dialog!.period.id}/close`);
          await refresh();
          toast.success("Period closed.");
        }}
      />
      <ConfirmDialog
        open={dialog?.action === "reopen"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={
          dialog
            ? `Reopen period ${dialog.period.period_no} of FY${dialog.period.fiscal_year}?`
            : ""
        }
        description="Entries can post into it again."
        confirmLabel="Reopen period"
        onConfirm={async () => {
          await api.post(`/finance/periods/${dialog!.period.id}/reopen`);
          await refresh();
          toast.success("Period reopened.");
        }}
      />
      <ConfirmDialog
        open={dialog?.action === "lock"}
        onOpenChange={(o) => !o && setDialog(null)}
        title={
          dialog ? `Lock period ${dialog.period.period_no} of FY${dialog.period.fiscal_year}?` : ""
        }
        description="This cannot be undone — a locked period never reopens."
        confirmLabel="Lock permanently"
        destructive
        onConfirm={async () => {
          await api.post(`/finance/periods/${dialog!.period.id}/lock`);
          await refresh();
          toast.success("Period locked.");
        }}
      />
    </>
  );
}

function GenerateDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [error, setError] = useState<string | null>(null);

  const generate = useMutation({
    mutationFn: () => api.post("/finance/periods/generate", { fiscal_year: Number(year) }),
    onSuccess: () => {
      onDone();
      toast.success(`FY${year} periods are ready.`);
      onClose();
    },
    onError: (err) => setError(describeError(err)),
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Generate a fiscal year"
      description="Twelve monthly periods, from the company's fiscal year start month. Safe to run again for a year that already has some."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={generate.isPending}
            onClick={() => {
              setError(null);
              generate.mutate();
            }}
          >
            Generate
          </Button>
        </>
      }
    >
      <FormField label="Fiscal year" required>
        <Input
          inputMode="numeric"
          className="tabular"
          value={year}
          onChange={(e) => setYear(e.target.value)}
        />
      </FormField>
      {error && (
        <p role="alert" className="mt-3 rounded-md bg-danger-bg px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
    </Dialog>
  );
}
