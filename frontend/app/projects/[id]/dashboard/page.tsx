"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, getLatestAnalysisRun, startAudit } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import { BackToOverview } from "@/components/flow/ui";
import { JobProgress } from "@/components/flow/JobProgress";
import { useJobWatcher } from "@/components/flow/useJobWatcher";
import {
  SCORE_TONE_TEXT,
  ScoreMeter,
  ScoreRing,
  percentage,
  toneFor,
} from "@/components/flow/metrics";
import { getFindingCopy, getSeverityLabel } from "@/lib/findingMappings";
import type { AnalysisRun, Score } from "@/lib/types";

const RUN_STATUS_STYLES: Record<string, string> = {
  succeeded: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  partial: "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30",
  failed: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  running: "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/30",
  pending: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
};

export default function OptimizationDashboardPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [missingRun, setMissingRun] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedScore, setExpandedScore] = useState<string | null>(null);
  const [expandedSignal, setExpandedSignal] = useState<string | null>(null);
  const { event, running, watch, stop, setEvent } = useJobWatcher();

  const loadRun = useCallback(async () => {
    try {
      // Null just means "no audit yet" — the endpoint returns 200 + null.
      const latest = (await getLatestAnalysisRun(projectId)) ?? null;
      setRun(latest);
      setMissingRun(latest === null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load analysis run.");
    }
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    void (async () => {
      try {
        const latest = (await getLatestAnalysisRun(projectId)) ?? null;
        if (cancelled) return;
        setRun(latest);
        setMissingRun(latest === null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load analysis run.");
      }
    })();
    return () => {
      cancelled = true;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, projectId]);

  const runAudit = useCallback(async () => {
    setError(null);
    setEvent(null);
    try {
      const job = await startAudit(projectId);
      watch(job.id, loadRun);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start audit.");
      stop();
    }
  }, [projectId, loadRun, watch, stop, setEvent]);

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const scores: Score[] = run?.scores_json ? Object.values(run.scores_json) : [];
  const overall = (() => {
    if (scores.length === 0) return null;
    const total = scores.reduce((sum, s) => sum + s.value, 0);
    const max = scores.reduce((sum, s) => sum + s.max_value, 0);
    return percentage(total, max);
  })();

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <BackToOverview projectId={projectId} />
      <ProjectTabs projectId={projectId} />

      <div className="mb-8 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Optimization dashboard</h1>
          <p className="mt-1 max-w-2xl text-sm text-zinc-400">
            Score breakdown and the signals behind every deduction. Drill into any score to see
            the findings that caused it.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/projects/${projectId}/findings`}
            className="text-sm text-zinc-500 transition-colors hover:text-brand-300"
          >
            Findings →
          </Link>
          <button onClick={runAudit} disabled={running} className="btn-primary">
            {running ? "Running audit…" : "Run audit"}
          </button>
        </div>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      <JobProgress title="Audit in progress" event={event} />

      {missingRun && !run && !running && (
        <div className="card p-10 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-full bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30">
            <svg viewBox="0 0 24 24" fill="none" className="size-6" aria-hidden="true">
              <path
                d="M12 3v3m0 12v3m9-9h-3M6 12H3m13.5-6.5-2 2m-7 7-2 2m11 0-2-2m-7-7-2-2"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </div>
          <p className="text-sm font-semibold text-zinc-100">No audit has run yet</p>
          <p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-zinc-400">
            Run an audit to see scores, signals, and findings for this project.
          </p>
          <button onClick={runAudit} className="btn-primary mt-4">
            Run audit
          </button>
        </div>
      )}

      {run && (
        <>
          <div className="mb-6 flex flex-wrap items-center gap-3 text-sm">
            <span className={`badge ${RUN_STATUS_STYLES[run.status] ?? RUN_STATUS_STYLES.pending}`}>
              {run.status}
            </span>
            <span className="text-zinc-500">
              Run #{run.id} ·{" "}
              {run.finished_at ? new Date(run.finished_at).toLocaleString() : "in progress"}
            </span>
          </div>

          {overall != null && (
            <div className="card mb-6 flex flex-col items-center gap-6 p-6 sm:flex-row sm:items-start">
              <div className="flex flex-col items-center gap-1">
                <ScoreRing percentage={overall} tone={overall >= 80 ? "good" : overall >= 50 ? "warn" : "bad"} size="lg" />
                <p className="text-xs text-zinc-500">Overall</p>
              </div>
              <div className="grid flex-1 gap-3 sm:grid-cols-3">
                {scores.map((score) => {
                  const p = percentage(score.value, score.max_value);
                  const tone = toneFor(score.value, score.max_value);
                  return (
                    <button
                      key={score.key}
                      type="button"
                      onClick={() => {
                        setExpandedScore(expandedScore === score.key ? null : score.key);
                        setExpandedSignal(null);
                      }}
                      aria-expanded={expandedScore === score.key}
                      className={`card card-hover p-4 text-left ${
                        expandedScore === score.key ? "border-brand-500/50" : ""
                      }`}
                    >
                      <p className="text-xs font-medium text-zinc-400">{score.label}</p>
                      <p className={`mt-1 text-3xl font-semibold tabular-nums ${SCORE_TONE_TEXT[tone]}`}>
                        {score.value}
                        <span className="text-base font-normal text-zinc-500">
                          /{score.max_value}
                        </span>
                      </p>
                      <div className="mt-2">
                        <ScoreMeter percentage={p} tone={tone} />
                      </div>
                      <p className="mt-2 text-xs text-zinc-500">
                        {score.signals.length} signal{score.signals.length === 1 ? "" : "s"} —{" "}
                        {expandedScore === score.key ? "hide" : "drill down"}
                      </p>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {expandedScore && (
            <ScoreDrilldown
              score={scores.find((s) => s.key === expandedScore)!}
              expandedSignal={expandedSignal}
              onSelectSignal={setExpandedSignal}
              projectId={projectId}
            />
          )}

          {scores.length === 0 && (
            <p className="text-sm italic text-zinc-500">No score data on this run.</p>
          )}

          <div className="mt-8 grid gap-2 sm:grid-cols-3">
            {[
              {
                label: "Search Console",
                value:
                  typeof run.inputs_json?.search_console_display === "string"
                    ? run.inputs_json.search_console_display
                    : null,
              },
              {
                label: "Pages audited",
                value:
                  typeof run.inputs_json?.page_count === "number"
                    ? String(run.inputs_json.page_count)
                    : null,
              },
              {
                label: "Findings",
                value:
                  typeof run.inputs_json?.finding_count === "number"
                    ? String(run.inputs_json.finding_count)
                    : null,
              },
            ].map((field) => (
              <div
                key={field.label}
                className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 px-4 py-3"
              >
                <p className="text-[11px] uppercase tracking-wider text-zinc-500">{field.label}</p>
                <p
                  className={`mt-0.5 truncate text-sm font-medium ${
                    field.value ? "text-zinc-200" : "italic text-zinc-500"
                  }`}
                >
                  {field.value ?? "Not available"}
                </p>
              </div>
            ))}
          </div>

          {typeof run.inputs_json?.sitemap_url_count === "number" && (
            <p className="mt-2 text-xs text-zinc-500">
              Sitemap: {run.inputs_json.sitemap_url_count} URLs
            </p>
          )}

          {run.gaps_json && run.gaps_json.length > 0 && (
            <p className="note-warning mt-4">
              Partial run — missing: {run.gaps_json.map((g) => g.capability).join(", ")}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function ScoreDrilldown({
  score,
  expandedSignal,
  onSelectSignal,
  projectId,
}: {
  score: Score;
  expandedSignal: string | null;
  onSelectSignal: (rule: string | null) => void;
  projectId: number;
}) {
  if (score.signals.length === 0) {
    return (
      <div className="card mb-8 p-4 text-sm italic text-zinc-500">
        No findings contributed to {score.label}.
      </div>
    );
  }

  return (
    <div className="card mb-8 p-5">
      <h2 className="mb-3 text-sm font-semibold text-zinc-100">{score.label} — signals</h2>
      <ul className="divide-y divide-zinc-800/70 text-sm">
        {score.signals.map((signal) => (
          <li key={signal.rule} className="py-2.5">
            <button
              type="button"
              onClick={() => onSelectSignal(expandedSignal === signal.rule ? null : signal.rule)}
              aria-expanded={expandedSignal === signal.rule}
              className="flex w-full items-center justify-between gap-3 text-left"
            >
              <span className="min-w-0">
                <span className="font-medium text-zinc-200">
                  {getFindingCopy(signal.rule).title}
                </span>{" "}
                <span className="text-xs text-zinc-500">
                  ({getSeverityLabel(signal.severity)}, {signal.finding_count} finding
                  {signal.finding_count === 1 ? "" : "s"})
                </span>
              </span>
              <span className="shrink-0 text-xs text-zinc-500">
                {expandedSignal === signal.rule ? "hide evidence" : "show evidence"}
              </span>
            </button>
            {expandedSignal === signal.rule && (
              <ul className="mt-2 space-y-2 border-l-2 border-brand-500/30 pl-3">
                {signal.evidence.map((ref, index) => (
                  <li key={`${ref.finding_id}:${index}`} className="text-xs">
                    <p className="truncate text-zinc-300">{ref.affected_resource}</p>
                    <p className="text-zinc-500">{ref.excerpt}</p>
                  </li>
                ))}
                <li>
                  <Link
                    href={`/projects/${projectId}/findings?rule=${encodeURIComponent(signal.rule)}`}
                    className="text-xs text-brand-400 transition-colors hover:text-brand-300"
                  >
                    View {signal.finding_count} finding{signal.finding_count === 1 ? "" : "s"} →
                  </Link>
                </li>
              </ul>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
