import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { PageBody, PageHeader } from "@/components/layout/page";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { api } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/utils";
import type { PayablesAgingRead } from "@/types/models";

export function PayablesAgingPage() {
  const [asOf, setAsOf] = useState("");
  const query = useQuery({
    queryKey: ["payables-aging", asOf],
    queryFn: () =>
      api.get<PayablesAgingRead>("/finance/payables/aging", { query: { as_of: asOf } }),
  });

  return (
    <>
      <PageHeader
        title="Payables ageing"
        subtitle="What is owed to each vendor, approved invoices only, bucketed by how many days past due."
      />
      <PageBody>
        <div className="mb-4 max-w-xs">
          <FormField label="As of" hint="Left blank, as of today.">
            <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          </FormField>
        </div>

        {query.isLoading ? (
          <PageSkeleton />
        ) : query.error || !query.data ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Payables ageing as of {formatDate(query.data.as_of)}
              </caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Vendor
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Current
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    1–30 days
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    31–60 days
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    61–90 days
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    90+ days
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Total
                  </th>
                </tr>
              </thead>
              <tbody>
                {query.data.rows.map((r) => (
                  <tr key={r.vendor_id} className="border-t border-border">
                    <td className="px-4 py-2">{r.vendor_name}</td>
                    <td data-numeric className="px-4 py-2">
                      {Number(r.current) > 0 ? formatMoney(r.current) : ""}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(r.days_1_30) > 0 ? formatMoney(r.days_1_30) : ""}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(r.days_31_60) > 0 ? formatMoney(r.days_31_60) : ""}
                    </td>
                    <td
                      data-numeric
                      className={`px-4 py-2 ${Number(r.days_61_90) > 0 ? "text-warning" : ""}`}
                    >
                      {Number(r.days_61_90) > 0 ? formatMoney(r.days_61_90) : ""}
                    </td>
                    <td
                      data-numeric
                      className={`px-4 py-2 ${Number(r.days_over_90) > 0 ? "text-danger" : ""}`}
                    >
                      {Number(r.days_over_90) > 0 ? formatMoney(r.days_over_90) : ""}
                    </td>
                    <td data-numeric className="px-4 py-2 font-medium tabular">
                      {formatMoney(r.total)}
                    </td>
                  </tr>
                ))}
                {query.data.rows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-4 py-6 text-center text-fg-muted">
                      Nothing outstanding.
                    </td>
                  </tr>
                )}
              </tbody>
              <tfoot>
                <tr className="border-t border-border font-semibold">
                  <td className="px-4 py-2" colSpan={6}>
                    Total
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(query.data.total)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </PageBody>
    </>
  );
}
