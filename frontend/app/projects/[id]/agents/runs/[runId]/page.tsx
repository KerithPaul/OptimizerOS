"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AgentRunCard, RequestSummary, sortAgentRuns } from "../../AgentRunCard";
import { ApiError, getAgentRun, getAgentRunsForJob } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import type { AgentRun } from "@/lib/types";

export default function AgentRunDetailPage() {
  const params = useParams<{ id: string; runId: string }>();
  const projectId = Number(params.id);
  const runId = Number(params.runId);
  const { user, loading: userLoading } = useCurrentUser();

  const [run, setRun] = useState<AgentRun | null>(null);
  const [siblings, setSiblings] = useState<AgentRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const selected = await getAgentRun(projectId, runId);
      setRun(selected);
      if (selected.job_id != null) {
        const rows = await getAgentRunsForJob(projectId, selected.job_id);
        setSiblings(rows);
      } else {
        setSiblings([selected]);
      }
    } catch (err) {
      setRun(null);
      setSiblings([]);
      setError(err instanceof ApiError ? err.message : "Failed to load agent run.");
    } finally {
      setLoading(false);
    }
  }, [projectId, runId]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void load();
  }, [user, load]);

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  const ordered = sortAgentRuns(siblings.length > 0 ? siblings : run ? [run] : []);
  const headerRun = run ?? ordered[0];

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <Link
        href={`/projects/${projectId}/agents`}
        className="mb-5 inline-flex items-center gap-1.5 text-sm text-zinc-500 transition-colors hover:text-brand-300"
      >
        <span aria-hidden="true">←</span> Optimization agents
      </Link>

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">Agent run</h1>
        <p className="mt-1 max-w-2xl text-sm leading-relaxed text-zinc-400">
          Persisted output from this run, including suggested interventions. Send a finding to
          the change planner to produce a Change Plan, preview diff, and review.
        </p>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}
      {loading && <p className="text-sm text-zinc-500">Loading…</p>}

      {!loading && headerRun && <RequestSummary run={headerRun} />}

      {!loading && ordered.length > 0 && (
        <div className="space-y-4">
          {ordered.map((row) => (
            <AgentRunCard
              key={row.id}
              run={row}
              projectId={projectId}
              highlighted={row.id === runId}
            />
          ))}
        </div>
      )}
    </div>
  );
}
