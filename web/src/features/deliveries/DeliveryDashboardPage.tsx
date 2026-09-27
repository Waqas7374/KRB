import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PageBody, PageHeader, Section } from "@/components/layout/page";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { useCan } from "@/features/auth/use-can";
import { api } from "@/lib/api";
import { cn, formatDate, formatDateTime, formatMoney, formatQuantity, humanize } from "@/lib/utils";
import type { DeliverySummary } from "@/types/models";

type Preset = "today" | "7" | "30" | "custom";

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const daysAgo = (n: number) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return iso(d);
};

/** A number that is also a door: every tile opens the list of rows behind it. */
function Tile({
  label,
  value,
  hint,
  to,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  to: string;
  tone?: "warning" | "danger";
}) {
  return (
    <Link
      to={to}
      className="flex flex-col gap-1 rounded-md border border-border bg-surface-raised px-4 py-3 hover:border-border-strong focus-visible:outline-2"
    >
      <span className="text-xs font-medium text-fg-muted">{label}</span>
      <span
        className={cn(
          "tabular text-2xl font-semibold",
          tone === "warning" && "text-warning",
          tone === "danger" && "text-danger",
        )}
      >
        {value}
      </span>
      {hint && <span className="text-xs text-fg-muted">{hint}</span>}
    </Link>
  );
}

const th = "px-4 py-2 text-left text-xs font-semibold text-fg-muted";

export function DeliveryDashboardPage() {
  const [preset, setPreset] = useState<Preset>("today");
  const canReview = useCan("deliveries.review");
  const [custom, setCustom] = useState({ from: daysAgo(6), to: iso(new Date()) });

  // "Today" is the company's day, which the server knows; a range is sent as dates.
  const range =
    preset === "today"
      ? {}
      : preset === "custom"
        ? { from_date: custom.from, to_date: custom.to }
        : { from_date: daysAgo(Number(preset) - 1), to_date: iso(new Date()) };

  const query = useQuery({
    queryKey: ["deliveries", "summary", range],
    queryFn: () => api.get<DeliverySummary>("/deliveries/summary", { query: range }),
    placeholderData: (previous) => previous,
  });

  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const s = query.data;

  const period = { from_date: s.from_date, to_date: s.to_date };
  const list = (extra: Record<string, string> = {}) =>
    `/deliveries?${new URLSearchParams({ ...period, ...extra }).toString()}`;
  const count = (status: string) => s.by_status[status] ?? 0;
  const others = s.quantities.filter((q) => q.unit_code !== "TON");
  const days = s.by_day.map((d) => ({
    label: formatDate(d.day),
    loads: d.deliveries,
    tonnage: Number(d.tonnage),
    value: d.value == null ? null : Number(d.value),
  }));

  return (
    <>
      <PageHeader
        title="Delivery dashboard"
        subtitle={
          s.from_date === s.to_date
            ? `Loads captured on ${formatDate(s.from_date)}.`
            : `Loads captured ${formatDate(s.from_date)} to ${formatDate(s.to_date)}.`
        }
        actions={
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Period">
            {(
              [
                ["today", "Today"],
                ["7", "7 days"],
                ["30", "30 days"],
                ["custom", "Custom"],
              ] as const
            ).map(([key, label]) => (
              <Button
                key={key}
                size="sm"
                variant={preset === key ? "primary" : "secondary"}
                aria-pressed={preset === key}
                onClick={() => setPreset(key)}
              >
                {label}
              </Button>
            ))}
          </div>
        }
      />
      <PageBody>
        {preset === "custom" && (
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-fg-muted">
              From
              <Input
                type="date"
                className="mt-1"
                value={custom.from}
                max={custom.to}
                onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))}
              />
            </label>
            <label className="text-xs text-fg-muted">
              To
              <Input
                type="date"
                className="mt-1"
                value={custom.to}
                min={custom.from}
                onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))}
              />
            </label>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
          <Tile label="Deliveries" value={s.deliveries} to={list()} />
          <Tile
            label="Tonnage"
            value={formatQuantity(s.tonnage, "TON")}
            to={list()}
            hint={
              others.length > 0
                ? others.map((q) => formatQuantity(q.quantity, q.unit_code)).join(" · ")
                : undefined
            }
          />
          <Tile
            label="Approved"
            value={count("APPROVED") + count("RECEIVED") + count("PARTIALLY_RECEIVED")}
            to={list({ status: "APPROVED" })}
          />
          <Tile
            label="Awaiting review"
            value={count("UNDER_REVIEW") + count("CORRECTION_REQUESTED")}
            to={list({ status: "UNDER_REVIEW" })}
            tone={count("UNDER_REVIEW") > 0 ? "warning" : undefined}
          />
          <Tile
            label="Rejected"
            value={count("REJECTED")}
            to={list({ status: "REJECTED" })}
            tone={count("REJECTED") > 0 ? "danger" : undefined}
          />
          <Tile
            label="Open flags"
            value={s.open_flags}
            to={list({ has_open_flags: "true" })}
            tone={s.open_flags > 0 ? "warning" : undefined}
          />
        </div>
        {!s.values_hidden && s.value != null && (
          <p className="text-sm text-fg-muted">
            Value of loads that arrived:{" "}
            <span className="tabular font-medium text-fg">{formatMoney(s.value)}</span>
          </p>
        )}

        {days.length === 0 ? (
          <Section title="By day">
            <p className="px-4 py-4 text-sm text-fg-muted">No loads in this period.</p>
          </Section>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Section title="Tonnage by day">
              <div
                className="h-56 px-2 pt-3"
                role="img"
                aria-label={`Tonnage by day: ${days.map((d) => `${d.label} ${d.tonnage}`).join(", ")}`}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={days} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                    <CartesianGrid vertical={false} strokeOpacity={0.4} />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} width={44} />
                    <Tooltip />
                    <Bar dataKey="tonnage" name="Tonnes" fill="var(--color-primary)" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Section>
            {!s.values_hidden && (
              <Section title="Value by day">
                <div
                  className="h-56 px-2 pt-3"
                  role="img"
                  aria-label={`Value by day: ${days.map((d) => `${d.label} ${d.value ?? 0}`).join(", ")}`}
                >
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={days} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                      <CartesianGrid vertical={false} strokeOpacity={0.4} />
                      <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} width={56} />
                      <Tooltip />
                      <Line
                        dataKey="value"
                        name="Value"
                        stroke="var(--color-primary)"
                        strokeWidth={2}
                        dot
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </Section>
            )}
          </div>
        )}

        {days.length > 0 && (
          <Section title="By day, as a table">
            <table className="w-full text-sm">
              <caption className="sr-only">Loads, tonnage and value for each day</caption>
              <thead className="bg-surface">
                <tr>
                  <th scope="col" className={th}>
                    Day
                  </th>
                  <th scope="col" data-numeric className={th}>
                    Loads
                  </th>
                  <th scope="col" data-numeric className={th}>
                    Tonnes
                  </th>
                  {!s.values_hidden && (
                    <th scope="col" data-numeric className={th}>
                      Value
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {s.by_day.map((d) => (
                  <tr key={d.day} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link
                        to={`/deliveries?${new URLSearchParams({ from_date: d.day, to_date: d.day }).toString()}`}
                        className="text-primary hover:underline"
                      >
                        {formatDate(d.day)}
                      </Link>
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {d.deliveries}
                    </td>
                    <td data-numeric className="px-4 py-2">
                      {formatQuantity(d.tonnage)}
                    </td>
                    {!s.values_hidden && (
                      <td data-numeric className="px-4 py-2">
                        {d.value == null ? "—" : formatMoney(d.value, { decimals: 0 })}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        )}

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <Section title="Top materials">
            <RankTable rows={s.top_materials} showValue={!s.values_hidden} label="Material" />
          </Section>
          <Section title="Top vendors">
            <RankTable rows={s.top_vendors} showValue={!s.values_hidden} label="Vendor" />
          </Section>
          <Section title="By site">
            <RankTable rows={s.by_site} showValue={false} label="Site" />
          </Section>
        </div>

        <Section
          title={`Waiting for review (${s.waiting_total})`}
          actions={
            s.waiting_total > 0 && canReview ? (
              <Link to="/deliveries/review" className="text-sm text-primary hover:underline">
                Open the review queue
              </Link>
            ) : undefined
          }
        >
          {s.waiting.length === 0 ? (
            <p className="px-4 py-4 text-sm text-fg-muted">Nothing is waiting.</p>
          ) : (
            <table className="w-full text-sm">
              <caption className="sr-only">Deliveries waiting for review, oldest first</caption>
              <thead className="bg-surface">
                <tr>
                  <th scope="col" className={th}>
                    Delivery
                  </th>
                  <th scope="col" className={th}>
                    Site
                  </th>
                  <th scope="col" className={th}>
                    Vendor
                  </th>
                  <th scope="col" className={th}>
                    Captured
                  </th>
                  <th scope="col" data-numeric className={th}>
                    Flags
                  </th>
                  <th scope="col" className={th}>
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {s.waiting.map((w) => (
                  <tr key={w.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link
                        to={`/deliveries/${w.id}`}
                        className="font-mono text-primary hover:underline"
                      >
                        {w.delivery_number}
                      </Link>
                    </td>
                    <td className="px-4 py-2">{w.site_code ?? "—"}</td>
                    <td className="px-4 py-2">{w.vendor_name ?? "—"}</td>
                    <td className="px-4 py-2">{formatDateTime(w.captured_at)}</td>
                    <td data-numeric className="px-4 py-2">
                      {w.flag_count}
                    </td>
                    <td className="px-4 py-2">{humanize(w.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </PageBody>
    </>
  );
}

function RankTable({
  rows,
  showValue,
  label,
}: {
  rows: DeliverySummary["top_vendors"];
  showValue: boolean;
  label: string;
}) {
  if (rows.length === 0) return <p className="px-4 py-4 text-sm text-fg-muted">Nothing yet.</p>;
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">{label} ranking</caption>
      <thead className="bg-surface">
        <tr>
          <th scope="col" className={th}>
            {label}
          </th>
          <th scope="col" data-numeric className={th}>
            Loads
          </th>
          <th scope="col" data-numeric className={th}>
            Quantity
          </th>
          {showValue && (
            <th scope="col" data-numeric className={th}>
              Value
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={`${r.id}-${r.quantities[0]?.unit_code ?? ""}`}
            className="border-t border-border"
          >
            <td className="px-4 py-2">
              <span className="font-medium">{r.label}</span>
              {r.sublabel && (
                <span className="ml-1 font-mono text-xs text-fg-muted">{r.sublabel}</span>
              )}
            </td>
            <td data-numeric className="px-4 py-2">
              {r.deliveries}
            </td>
            <td data-numeric className="px-4 py-2">
              {r.quantities.length
                ? r.quantities.map((q) => formatQuantity(q.quantity, q.unit_code)).join(" · ")
                : "—"}
            </td>
            {showValue && (
              <td data-numeric className="px-4 py-2">
                {r.value == null ? "—" : formatMoney(r.value, { decimals: 0 })}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
