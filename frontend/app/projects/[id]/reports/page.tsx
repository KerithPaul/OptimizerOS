"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  ApiError,
  getSiteReport,
  listSiteReports,
  startSiteReport,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import { BackToOverview } from "@/components/flow/ui";
import { JobProgress } from "@/components/flow/JobProgress";
import { useJobWatcher } from "@/components/flow/useJobWatcher";
import type {
  SiteReport,
  SiteReportDailyPoint,
  SiteReportDocument,
  SiteReportMetricCell,
  SiteReportPeriodComparison,
  SiteReportQueryRow,
  SiteReportSummary,
} from "@/lib/types";

function formatMetric(
  cell?: SiteReportMetricCell | null,
  kind: "count" | "ctr" | "position" | "ratio" = "count",
): string {
  if (!cell) return "—";
  if (cell.status !== "measured" || cell.value == null) return cell.detail || cell.status || "unavailable";
  if (kind === "ctr" && typeof cell.value === "number") return `${(cell.value * 100).toFixed(2)}%`;
  if (kind === "ratio" && typeof cell.value === "number") return `${(cell.value * 100).toFixed(1)}%`;
  if (kind === "position" && typeof cell.value === "number") return cell.value.toFixed(1);
  if (typeof cell.value === "number") return cell.value.toLocaleString();
  return "—";
}

function formatCount(value?: number | null): string {
  if (value == null) return "—";
  return value.toLocaleString();
}

function formatCtr(value?: number | null): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function formatPos(value?: number | null): string {
  if (value == null) return "—";
  return value.toFixed(1);
}

function signed(value?: number | null, kind: "count" | "ctr" | "position" = "count"): string {
  if (value == null) return "—";
  if (kind === "ctr") return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}pp`;
  if (kind === "position") return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toLocaleString()}`;
}

function toneFor(metric: string, delta?: number | null): "up" | "down" | "flat" {
  if (delta == null || delta === 0) return "flat";
  if (metric === "position") return delta < 0 ? "up" : "down";
  return delta > 0 ? "up" : "down";
}

function toneClass(tone: "up" | "down" | "flat"): string {
  if (tone === "up") return "text-emerald-400";
  if (tone === "down") return "text-red-400";
  return "text-zinc-500";
}

function kpiRing(tone: "up" | "down" | "flat"): string {
  if (tone === "up") return "border-t-emerald-500";
  if (tone === "down") return "border-t-red-500";
  return "border-t-zinc-600";
}

function Sparkline({
  points,
  metric,
  color,
}: {
  points: SiteReportDailyPoint[];
  metric: "impressions" | "clicks";
  color: string;
}) {
  if (points.length < 2) {
    return <p className="text-sm text-zinc-500">Need at least two daily Search Console rows for this chart.</p>;
  }
  const width = 640;
  const height = 140;
  const pad = 16;
  const values = points.map((point) => point[metric] || 0);
  const vmax = Math.max(...values);
  const vmin = Math.min(...values);
  const span = vmax - vmin || vmax || 1;
  const coords = values.map((value, index) => {
    const x = pad + ((width - 2 * pad) * index) / (values.length - 1);
    const y = height - pad - ((height - 2 * pad) * (value - vmin)) / span;
    return [x, y] as const;
  });
  const line = coords.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const fill = `${line} L ${coords[coords.length - 1][0].toFixed(1)},${height - pad} L ${coords[0][0].toFixed(1)},${height - pad} Z`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-36 w-full" role="img" aria-label={`${metric} daily trend`}>
      <path d={fill} fill={color} fillOpacity="0.16" />
      <path d={line} fill="none" stroke={color} strokeWidth="2.4" />
      <circle cx={coords[coords.length - 1][0]} cy={coords[coords.length - 1][1]} r="3.5" fill={color} />
      <text x={pad} y={height - 2} fontSize="11" fill="#71717a">
        {points[0].date}
      </text>
      <text x={width - pad} y={height - 2} fontSize="11" fill="#71717a" textAnchor="end">
        {points[points.length - 1].date}
      </text>
    </svg>
  );
}

function BarList({
  rows,
  labelKey,
}: {
  rows: SiteReportQueryRow[];
  labelKey: "query" | "page" | "label";
}) {
  const usable = rows.filter((row) => row[labelKey]).slice(0, 8);
  const maxValue = Math.max(1, ...usable.map((row) => row.impressions || 0));
  if (usable.length === 0) {
    return <p className="text-sm text-zinc-500">None stored for this run.</p>;
  }
  return (
    <ul className="grid gap-2">
      {usable.map((row) => {
        const label = String(row[labelKey] || "");
        const width = `${Math.max(4, ((row.impressions || 0) / maxValue) * 100)}%`;
        return (
          <li key={label}>
            <div className="mb-1 flex justify-between gap-3 text-xs text-zinc-400">
              <span className="truncate">{label}</span>
              <span>{formatCount(row.impressions)}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
              <div className="h-2 rounded-full bg-brand-500" style={{ width }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function ComparisonTable({ windows }: { windows: SiteReportPeriodComparison[] }) {
  if (windows.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        Daily Search Console rows are missing, so day/week/month windows cannot be built yet.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="py-2 pr-3">Window</th>
            <th className="py-2 pr-3">Impressions</th>
            <th className="py-2 pr-3">Clicks</th>
            <th className="py-2 pr-3">CTR</th>
            <th className="py-2">Position</th>
          </tr>
        </thead>
        <tbody>
          {windows.map((window) => {
            if (window.status !== "observed") {
              return (
                <tr key={window.key} className="border-t border-zinc-800">
                  <td className="py-2 pr-3 text-zinc-200">{window.label}</td>
                  <td colSpan={4} className="py-2 text-zinc-500">
                    {window.detail || "unavailable"}
                  </td>
                </tr>
              );
            }
            const delta = window.delta || {};
            return (
              <tr key={window.key} className="border-t border-zinc-800">
                <td className="py-2 pr-3">
                  <div className="text-zinc-100">{window.label}</div>
                  <div className="text-xs text-zinc-500">
                    {window.current_start} → {window.current_end}
                  </div>
                </td>
                <td className="py-2 pr-3">
                  {formatCount(window.current?.impressions)}
                  <div className={`text-xs ${toneClass(toneFor("impressions", delta.impressions))}`}>
                    {signed(delta.impressions)}
                  </div>
                </td>
                <td className="py-2 pr-3">
                  {formatCount(window.current?.clicks)}
                  <div className={`text-xs ${toneClass(toneFor("clicks", delta.clicks))}`}>
                    {signed(delta.clicks)}
                  </div>
                </td>
                <td className="py-2 pr-3">
                  {formatCtr(window.current?.ctr)}
                  <div className={`text-xs ${toneClass(toneFor("ctr", delta.ctr))}`}>{signed(delta.ctr, "ctr")}</div>
                </td>
                <td className="py-2">
                  {formatPos(window.current?.position)}
                  <div className={`text-xs ${toneClass(toneFor("position", delta.position))}`}>
                    {signed(delta.position, "position")}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type ObservedDeltaMap = Record<string, { status?: string; delta?: number }>;

function KeywordTable({ rows, kind }: { rows: SiteReportQueryRow[]; kind: "query" | "page" }) {
  if (rows.length === 0) {
    return <p className="text-sm text-zinc-500">None stored for this run.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="py-2 pr-3">{kind === "page" ? "Page" : "Keyword"}</th>
            <th className="py-2 pr-3">Impressions</th>
            <th className="py-2 pr-3">Δ</th>
            <th className="py-2 pr-3">Clicks</th>
            <th className="py-2 pr-3">CTR</th>
            <th className="py-2">Position</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const label = kind === "page" ? row.page : row.query;
            return (
              <tr key={label} className="border-t border-zinc-800">
                <td className="max-w-xs truncate py-2 pr-3 text-zinc-200" title={label || ""}>
                  {label || "—"}
                </td>
                <td className="py-2 pr-3">{formatCount(row.impressions)}</td>
                <td className={`py-2 pr-3 ${toneClass(toneFor("impressions", row.delta_impressions))}`}>
                  {row.movement === "new" ? "new" : signed(row.delta_impressions)}
                </td>
                <td className="py-2 pr-3">{formatCount(row.clicks)}</td>
                <td className="py-2 pr-3">{formatCtr(row.ctr)}</td>
                <td className="py-2">{formatPos(row.position)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function SiteReportsPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [rows, setRows] = useState<SiteReportSummary[]>([]);
  const [selected, setSelected] = useState<SiteReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { event, running, watch, stop, setEvent } = useJobWatcher();

  const load = useCallback(async (preferId?: number) => {
    const list = await listSiteReports(projectId);
    setRows(list);
    if (list.length === 0) {
      setSelected(null);
      return;
    }
    const nextId = preferId && list.some((row) => row.id === preferId) ? preferId : list[0].id;
    setSelected(await getSiteReport(projectId, nextId));
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    void load().catch((err) => {
      setError(err instanceof ApiError ? err.message : "Failed to load site reports.");
    });
    return () => {
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, projectId]);

  async function onGenerate() {
    setError(null);
    setEvent(null);
    try {
      const job = await startSiteReport(projectId);
      watch(job.id, () => {
        void load().catch((err) => {
          setError(err instanceof ApiError ? err.message : "Failed to load site reports.");
        });
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start the site report.");
      stop();
    }
  }

  async function onSelect(id: number) {
    setError(null);
    try {
      setSelected(await getSiteReport(projectId, id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to open that report.");
    }
  }

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const doc = (selected?.document_json || {}) as SiteReportDocument;
  const totals = selected?.totals || doc.totals;
  const daily = doc.daily_series || [];
  const observed = (doc.deltas as { observed_delta?: ObservedDeltaMap } | null)?.observed_delta;
  const impressionTone = toneFor("impressions", observed?.impressions?.status === "observed" ? observed.impressions.delta : null);
  const clickTone = toneFor("clicks", observed?.clicks?.status === "observed" ? observed.clicks.delta : null);
  const ctrTone = toneFor("ctr", observed?.ctr?.status === "observed" ? observed.ctr.delta : null);
  const posTone = toneFor("position", observed?.position?.status === "observed" ? observed.position.delta : null);

  return (
    <div className="mx-auto w-full max-w-6xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <BackToOverview projectId={projectId} />
      <ProjectTabs projectId={projectId} />

      <div className="mb-8 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Site health reports</h1>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-zinc-400">
            Recrawl, audit, and pull Search Console including daily rows for day, week, and month
            comparisons. Colored movement is correlation, not proof that a change caused it.
            Indexability is crawl evidence, not Google Index Coverage.
          </p>
        </div>
        <button type="button" onClick={() => void onGenerate()} disabled={running} className="btn-primary">
          {running ? "Generating…" : "Generate report"}
        </button>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      <JobProgress title="Site report" event={event} />

      {rows.length === 0 && !running ? (
        <p className="text-sm text-zinc-500">No reports yet. Generate one after Search Console is connected.</p>
      ) : (
        <ul className="mb-8 grid gap-2">
          {rows.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => void onSelect(row.id)}
                className={`w-full rounded-lg px-3 py-2 text-left text-sm ring-1 ring-inset ${
                  selected?.id === row.id
                    ? "bg-brand-500/15 text-brand-200 ring-brand-500/30"
                    : "text-zinc-300 ring-zinc-800 hover:bg-zinc-800/60"
                }`}
              >
                <span className="font-medium">Report #{row.id}</span>
                <span className="ml-2 text-zinc-500">{row.status}</span>
                {row.period?.start_date && row.period?.end_date ? (
                  <span className="ml-2 text-zinc-500">
                    {row.period.start_date} – {row.period.end_date}
                  </span>
                ) : null}
                <span className="ml-2 text-zinc-500">email {row.email_status}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <section className="grid gap-6">
          <div className="card card-section p-5">
            <h2 className="text-sm font-semibold text-zinc-200">Report #{selected.id}</h2>
            <p className="mt-1 text-sm text-zinc-400">
              {selected.website_url ?? "No website URL"} · {selected.search_console ?? "Search Console: unknown"}
            </p>
            <p className="mt-1 text-xs text-zinc-500">{selected.email_detail}</p>
            <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className={`rounded-xl border border-zinc-800 border-t-4 bg-zinc-950/40 p-3 ${kpiRing(impressionTone)}`}>
                <dt className="text-xs text-zinc-500">Impressions</dt>
                <dd className="text-xl font-semibold text-zinc-100">{formatMetric(totals?.impressions)}</dd>
              </div>
              <div className={`rounded-xl border border-zinc-800 border-t-4 bg-zinc-950/40 p-3 ${kpiRing(clickTone)}`}>
                <dt className="text-xs text-zinc-500">Clicks</dt>
                <dd className="text-xl font-semibold text-zinc-100">{formatMetric(totals?.clicks)}</dd>
              </div>
              <div className={`rounded-xl border border-zinc-800 border-t-4 bg-zinc-950/40 p-3 ${kpiRing(ctrTone)}`}>
                <dt className="text-xs text-zinc-500">CTR</dt>
                <dd className="text-xl font-semibold text-zinc-100">{formatMetric(totals?.ctr, "ctr")}</dd>
              </div>
              <div className={`rounded-xl border border-zinc-800 border-t-4 bg-zinc-950/40 p-3 ${kpiRing(posTone)}`}>
                <dt className="text-xs text-zinc-500">Avg position</dt>
                <dd className="text-xl font-semibold text-zinc-100">{formatMetric(totals?.position, "position")}</dd>
              </div>
            </dl>
            <p className="mt-3 text-sm text-zinc-400">
              Findings: {selected.finding_count ?? doc.finding_count ?? 0} (
              {selected.open_finding_count ?? doc.open_finding_count ?? 0} open)
            </p>
          </div>

          <div className="card card-section p-5">
            <h3 className="text-sm font-semibold text-zinc-200">Day, week, and month</h3>
            <p className="mt-1 text-xs text-zinc-500">
              Built from daily Search Console rows. Green is a helpful direction (higher traffic, lower position).
            </p>
            <div className="mt-4">
              <ComparisonTable windows={doc.period_comparisons || []} />
            </div>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <div className="card card-section p-5">
              <h3 className="text-sm font-semibold text-zinc-200">Daily impressions</h3>
              <div className="mt-3">
                <Sparkline points={daily} metric="impressions" color="#818cf8" />
              </div>
            </div>
            <div className="card card-section p-5">
              <h3 className="text-sm font-semibold text-zinc-200">Daily clicks</h3>
              <div className="mt-3">
                <Sparkline points={daily} metric="clicks" color="#34d399" />
              </div>
            </div>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <div className="card card-section p-5">
              <h3 className="text-sm font-semibold text-zinc-200">Top keywords</h3>
              <div className="mt-3">
                <BarList rows={doc.top_queries || []} labelKey="query" />
              </div>
            </div>
            <div className="card card-section p-5">
              <h3 className="text-sm font-semibold text-zinc-200">Devices</h3>
              <div className="mt-3">
                <BarList rows={doc.devices || []} labelKey="label" />
              </div>
            </div>
          </div>

          <div className="card card-section p-5">
            <h3 className="text-sm font-semibold text-zinc-200">Keywords with movement vs previous audit</h3>
            <div className="mt-3">
              <KeywordTable rows={doc.top_queries || []} kind="query" />
            </div>
          </div>

          <div className="card card-section p-5">
            <h3 className="text-sm font-semibold text-zinc-200">Query opportunities (position ≥ 8 or CTR below 2%)</h3>
            <div className="mt-3">
              <KeywordTable rows={doc.keywords || []} kind="query" />
            </div>
          </div>

          <div className="card card-section p-5">
            <h3 className="text-sm font-semibold text-zinc-200">Top pages</h3>
            <div className="mt-3">
              <KeywordTable rows={doc.top_pages || []} kind="page" />
            </div>
          </div>

          {selected.html ? (
            <div className="card card-section p-5">
              <h3 className="text-sm font-semibold text-zinc-200">Printable report</h3>
              <iframe
                title={`Site health report ${selected.id}`}
                className="mt-4 h-[720px] w-full rounded-md bg-white"
                srcDoc={selected.html}
              />
            </div>
          ) : (
            <p className="text-sm text-zinc-500">This run has no HTML yet.</p>
          )}
        </section>
      )}
    </div>
  );
}
