import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { PageBody, PageHeader } from "@/components/layout/page";
import { FormField } from "@/components/ui/form-field";
import { Input, Select } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { api } from "@/lib/api";
import { useAccountOptions } from "@/lib/queries";
import { formatDate, formatMoney } from "@/lib/utils";
import type { GeneralLedgerRead, TrialBalanceRead } from "@/types/models";

// --- Trial balance -----------------------------------------------------------------

export function TrialBalancePage() {
  const [asOf, setAsOf] = useState("");
  const query = useQuery({
    queryKey: ["trial-balance", asOf],
    queryFn: () => api.get<TrialBalanceRead>("/finance/trial-balance", { query: { as_of: asOf } }),
  });

  return (
    <>
      <PageHeader
        title="Trial balance"
        subtitle="Every account with a posted movement, net onto whichever side it actually falls."
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
                Trial balance as of {formatDate(query.data.as_of)}
              </caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Code
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Account
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Debit
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Credit
                  </th>
                </tr>
              </thead>
              <tbody>
                {query.data.rows.map((r) => (
                  <tr key={r.account_id} className="border-t border-border">
                    <td className="px-4 py-2 font-mono text-xs text-fg-muted">{r.code}</td>
                    <td className="px-4 py-2">{r.name}</td>
                    <td data-numeric className="px-4 py-2">
                      {Number(r.debit) > 0 ? formatMoney(r.debit) : ""}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(r.credit) > 0 ? formatMoney(r.credit) : ""}
                    </td>
                  </tr>
                ))}
                {query.data.rows.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-6 text-center text-fg-muted">
                      Nothing posted yet.
                    </td>
                  </tr>
                )}
              </tbody>
              <tfoot>
                <tr className="border-t border-border font-semibold">
                  <td className="px-4 py-2" colSpan={2}>
                    Total
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(query.data.total_debit)}
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(query.data.total_credit)}
                  </td>
                </tr>
              </tfoot>
            </table>
            <p
              className={`border-t border-border px-4 py-2 text-xs ${
                query.data.total_debit === query.data.total_credit ? "text-fg-muted" : "text-danger"
              }`}
            >
              {query.data.total_debit === query.data.total_credit
                ? "Balances."
                : "Does not balance — this should never happen; tell finance."}
            </p>
          </div>
        )}
      </PageBody>
    </>
  );
}

// --- General ledger ------------------------------------------------------------------

export function GeneralLedgerPage() {
  const [params, setParams] = useSearchParams();
  const accounts = useAccountOptions();
  const postable = accounts.rows.filter((a) => a.is_postable);
  const accountId = params.get("account_id") ?? "";
  const fromDate = params.get("from_date") ?? "";
  const toDate = params.get("to_date") ?? "";

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const query = useQuery({
    queryKey: ["general-ledger", accountId, fromDate, toDate],
    queryFn: () =>
      api.get<GeneralLedgerRead>("/finance/general-ledger", {
        query: { account_id: accountId, from_date: fromDate, to_date: toDate },
      }),
    enabled: Boolean(accountId),
  });

  return (
    <>
      <PageHeader
        title="General ledger"
        subtitle="One account's posted movements, in date order, with a running balance."
      />
      <PageBody>
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3 sm:max-w-2xl">
          <FormField label="Account" required>
            <Select value={accountId} onChange={(e) => set("account_id", e.target.value)}>
              <option value="">Choose…</option>
              {postable.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.code} — {a.name}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="From">
            <Input
              type="date"
              value={fromDate}
              onChange={(e) => set("from_date", e.target.value)}
            />
          </FormField>
          <FormField label="To">
            <Input type="date" value={toDate} onChange={(e) => set("to_date", e.target.value)} />
          </FormField>
        </div>

        {!accountId ? (
          <p className="text-sm text-fg-muted">Choose an account to see its ledger.</p>
        ) : query.isLoading ? (
          <PageSkeleton />
        ) : query.error || !query.data ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">
                General ledger for {query.data.account_code} — {query.data.account_name}
              </caption>
              <thead className="bg-surface text-left text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Date
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Entry
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Description
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Debit
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Credit
                  </th>
                  <th scope="col" data-numeric className="px-4 py-2 font-semibold">
                    Balance
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-border bg-surface">
                  <td className="px-4 py-2 text-fg-muted" colSpan={5}>
                    Opening balance
                  </td>
                  <td data-numeric className="px-4 py-2 font-medium">
                    {formatMoney(query.data.opening_balance)}
                  </td>
                </tr>
                {query.data.rows.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="px-4 py-2">{formatDate(r.entry_date)}</td>
                    <td className="px-4 py-2 font-mono text-xs">{r.je_number}</td>
                    <td className="px-4 py-2">{r.line_description || r.description}</td>
                    <td data-numeric className="px-4 py-2">
                      {Number(r.debit) > 0 ? formatMoney(r.debit) : ""}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {Number(r.credit) > 0 ? formatMoney(r.credit) : ""}
                    </td>
                    <td data-numeric className="px-4 py-2 tabular">
                      {formatMoney(r.running_balance)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border font-semibold">
                  <td className="px-4 py-2" colSpan={5}>
                    Closing balance
                  </td>
                  <td data-numeric className="px-4 py-2">
                    {formatMoney(query.data.closing_balance)}
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
