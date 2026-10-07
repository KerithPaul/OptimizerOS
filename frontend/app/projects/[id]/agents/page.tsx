"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  AGENT_LABELS,
  AgentRunCard,
  interventionCount,
  sortAgentRuns,
} from "./AgentRunCard";
import { RUN_STATUS_STYLES } from "@/components/flow/badges";
import {
  ApiError,
  cancelJob,
  getAgentRunsForJob,
  getJob,
  listAgentRuns,
  startAgentRun,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import { BackToOverview } from "@/components/flow/ui";
import { JobProgress } from "@/components/flow/JobProgress";
import { useJobWatcher } from "@/components/flow/useJobWatcher";
import type { AgentRun } from "@/lib/types";

export default function AgentsPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { user, loading: userLoading } = useCurrentUser();

  const GOALS = [
    {
      key: "seo",
      label: "SEO",
      request:
        "Improve SEO for the site's pages: fix technical SEO findings (metadata, headings, internal links, sitemap) with high-confidence low-risk changes only",
    },
    {
      key: "aeo",
      label: "AEO",
      request:
        "Improve AEO (answer engine optimization): structure content with clear headings, FAQ/schema markup, and direct answers for AI answer engines",
    },
    {
      key: "geo",
      label: "GEO",
      request:
        "Improve GEO (generative engine optimization): strengthen entity clarity, citations, and content structure so the site is retrieved and cited by AI search",
    },
    {
      key: "all",
      label: "SEO · AEO · GEO",
      request:
        "Improve SEO/AEO/GEO across the site using the latest audit findings, high-confidence low-risk only",
    },
  ] as const;

  const [requestText, setRequestText] = useState<string>(GOALS[3].request);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [history, setHistory] = useState<AgentRun[]>([]);
  const [currentJobId, setCurrentJobId] = useState<number | null>(null);
  const [cancellingJobId, setCancellingJobId] = useState<number | null>(null);
  const { event, running, watch, stop, setEvent } = useJobWatcher();

  const loadHistory = useCallback(async () => {
    try {
      const rows = await listAgentRuns(projectId);
      setHistory(rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load agent run history.");
    }
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void loadHistory();
  }, [user, loadHistory]);

  useEffect(() => {
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The history list only reflects reality on a fresh `listAgentRuns` read,
  // so poll while any row is still `running` — including after the user hits
  // Stop, when cancel is cooperative and the worker flips the row at its next
  // checkpoint (which can take tens of seconds on an LLM step).
  const anyHistoryRunning = history.some((run) => run.status === "running");
  useEffect(() => {
    if (!anyHistoryRunning) return;
    const interval = setInterval(() => void loadHistory(), 4000);
    return () => clearInterval(interval);
  }, [anyHistoryRunning, loadHistory]);

  const runAgents = useCallback(async () => {
    if (!requestText.trim()) return;
    setError(null);
    setEvent(null);
    setRuns([]);
    try {
      const { job_id: jobId } = await startAgentRun(projectId, requestText.trim());
      setCurrentJobId(jobId);
      void loadHistory();
      watch(jobId, () => {
        setCurrentJobId(null);
        void loadHistory();
        getAgentRunsForJob(projectId, jobId)
          .then((rows) => setRuns(rows))
          .catch((err) =>
            setError(err instanceof ApiError ? err.message : "Failed to load agent runs.")
          );
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start agent run.");
      stop();
      setCurrentJobId(null);
    }
  }, [projectId, requestText, loadHistory, watch, stop, setEvent]);

  const cancelRun = useCallback(
    async (jobId: number) => {
      setError(null);
      setCancellingJobId(jobId);
      try {
        await cancelJob(jobId);
        if (jobId === currentJobId) {
          stop();
          setCurrentJobId(null);
        }
        // Cancel is cooperative on the backend: a running job only gets
        // `cancel_requested` set and the worker transitions it at its next
        // checkpoint. Poll until the row is terminal (or give up after ~6s)
        // so the history list doesn't reload while the row still reads
        // "running" and then sit there looking stuck.
        for (let i = 0; i < 20; i++) {
          const job = await getJob(jobId).catch(() => null);
          if (!job || job.status !== "running") break;
          await new Promise((r) => setTimeout(r, 300));
        }
        await loadHistory();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Failed to cancel job.");
      } finally {
        setCancellingJobId(null);
      }
    },
    [currentJobId, loadHistory, stop]
  );

  // A failed/cancelled run has nothing left to stop — clear the progress
  // panel so the user can start a fresh run without a dead error card in
  // the way.
  const dismissRun = useCallback(() => {
    setEvent(null);
    setCurrentJobId(null);
  }, [setEvent]);

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const orderedRuns = sortAgentRuns(runs);

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <BackToOverview projectId={projectId} />
      <ProjectTabs projectId={projectId} />

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">Improve SEO · AEO · GEO</h1>
        <p className="mt-1 max-w-2xl text-sm leading-relaxed text-zinc-400">
          Pick a goal — the optimization agents turn your latest audit findings into ranked,
          evidence-backed interventions. They do not recrawl the site, and indexing the repository
          does not refresh them. A finding that is already fixed is not proposed again. Crawl and
          audit after the site is deployed to check the live page. No file or CMS resource is
          mutated here.
        </p>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      {/* Run request */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void runAgents();
        }}
        className="card mb-8 p-5"
      >
        <label className="field-label" htmlFor="agent-request">
          Optimization goal
        </label>
        <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Goal presets">
          {GOALS.map((goal) => (
            <button
              key={goal.key}
              type="button"
              onClick={() => setRequestText(goal.request)}
              aria-pressed={requestText === goal.request}
              className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                requestText === goal.request
                  ? "bg-brand-500/15 font-medium text-brand-300 ring-1 ring-inset ring-brand-500/30"
                  : "bg-zinc-800/60 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
              }`}
            >
              {goal.label}
            </button>
          ))}
        </div>
        <textarea
          id="agent-request"
          value={requestText}
          onChange={(e) => setRequestText(e.target.value)}
          rows={3}
          className="field-input mb-4"
          placeholder="e.g. Improve SEO/AEO/GEO for product pages, high-confidence low-risk only"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            disabled={running || !requestText.trim()}
            className="btn-primary"
          >
            {running ? "Running agents…" : "Run agents"}
          </button>
          {running && currentJobId !== null && (
            <button
              type="button"
              onClick={() => void cancelRun(currentJobId)}
              disabled={cancellingJobId === currentJobId}
              className="btn-danger"
            >
              {cancellingJobId === currentJobId ? "Stopping…" : "Stop"}
            </button>
          )}
        </div>

        <div className="mt-4">
          <JobProgress title="Agent run" event={event} onDismiss={dismissRun} />
        </div>
      </form>

      {orderedRuns.length > 0 && (
        <div className="mb-10 space-y-4">
          <p className="text-xs text-zinc-500">
            These results are stored. After a reload, open any row in Run history to see them
            again and send findings to the change planner.
          </p>
          {orderedRuns.map((run) => (
            <AgentRunCard key={run.id} run={run} projectId={projectId} />
          ))}
        </div>
      )}

      {/* Run history */}
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-widest text-zinc-500">
          Run history
        </h2>
        <ul className="card divide-y divide-zinc-800/70 overflow-hidden">
          {history.map((run) => {
            const suggested = interventionCount(run);
            return (
              <li
                key={run.id}
                className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-zinc-800/40"
              >
                <Link
                  href={`/projects/${projectId}/agents/runs/${run.id}`}
                  className="min-w-0 flex-1"
                >
                  <span className="font-medium text-zinc-200">{AGENT_LABELS[run.agent_type]}</span>{" "}
                  <span className="block text-xs text-zinc-500 sm:inline">
                    {run.iterations_used} iter · {run.tool_calls_used} tool calls ·{" "}
                    {run.tokens_used} tokens
                    {run.stopped_reason ? ` · stopped: ${run.stopped_reason}` : ""}
                    {suggested > 0
                      ? ` · ${suggested} suggested change${suggested === 1 ? "" : "s"}`
                      : ""}
                  </span>
                </Link>
                <div className="flex shrink-0 items-center gap-2">
                  <span className={`badge ${RUN_STATUS_STYLES[run.status]}`}>{run.status}</span>
                  {run.status === "running" && run.job_id !== null ? (
                    <button
                      type="button"
                      onClick={() => void cancelRun(run.job_id as number)}
                      disabled={cancellingJobId === run.job_id}
                      className="btn-danger px-2 py-0.5 text-xs"
                    >
                      {cancellingJobId === run.job_id ? "Stopping…" : "Stop"}
                    </button>
                  ) : (
                    <Link
                      href={`/projects/${projectId}/agents/runs/${run.id}`}
                      className="text-xs text-zinc-500 transition-colors hover:text-brand-300"
                    >
                      Details →
                    </Link>
                  )}
                </div>
              </li>
            );
          })}
          {history.length === 0 && (
            <li className="px-4 py-10 text-center text-sm italic text-zinc-500">
              No agent runs yet.
            </li>
          )}
        </ul>
      </section>
    </div>
  );
}
