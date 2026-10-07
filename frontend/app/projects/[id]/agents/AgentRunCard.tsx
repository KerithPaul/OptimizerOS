"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { ApiError, applyChange } from "@/lib/api";
import { GuidedFixPanel } from "@/components/flow/GuidedFixPanel";
import { RUN_STATUS_STYLES } from "@/components/flow/badges";
import type {
  AgentRun,
  AgentType,
  ChangePlan,
  IntentObjective,
  Intervention,
  OmittedFinding,
} from "@/lib/types";

export const AGENT_LABELS: Record<AgentType, string> = {
  research: "Research Agent",
  seo: "SEO Agent",
  aeo: "AEO Agent",
  geo: "GEO Agent",
  code: "Code Agent",
  reviewer: "Reviewer Agent",
  cms: "CMS Agent",
};

export const AGENT_ORDER: AgentType[] = ["research", "seo", "aeo", "geo", "code", "reviewer"];

export function isInterventionList(value: unknown): value is Intervention[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        item !== null && typeof item === "object" && "finding_id" in item && "intervention" in item
    )
  );
}

export function interventionsFrom(result: AgentRun["result_json"]): Intervention[] {
  if (isInterventionList(result)) return result;
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const wrapped = (result as { interventions?: unknown }).interventions;
    if (isInterventionList(wrapped)) return wrapped;
  }
  return [];
}

export function omittedFindingsFrom(result: AgentRun["result_json"]): {
  count: number;
  findings: OmittedFinding[];
} {
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    return { count: 0, findings: [] };
  }
  const row = result as { omitted_findings?: unknown; omitted_count?: unknown };
  const findings = Array.isArray(row.omitted_findings)
    ? row.omitted_findings.filter(
        (item): item is OmittedFinding =>
          item !== null &&
          typeof item === "object" &&
          typeof (item as OmittedFinding).finding_id === "string",
      )
    : [];
  const count = typeof row.omitted_count === "number" ? row.omitted_count : findings.length;
  return { count, findings };
}

export function interventionCount(run: AgentRun): number {
  return interventionsFrom(run.result_json).length;
}

export function sortAgentRuns(runs: AgentRun[]): AgentRun[] {
  return [...runs].sort(
    (a, b) => AGENT_ORDER.indexOf(a.agent_type) - AGENT_ORDER.indexOf(b.agent_type)
  );
}

export function AgentRunCard({
  run,
  projectId,
  highlighted = false,
}: {
  run: AgentRun;
  projectId: number;
  highlighted?: boolean;
}) {
  const interventions = interventionsFrom(run.result_json);
  const omitted = omittedFindingsFrom(run.result_json);
  const router = useRouter();
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);

  const apply = useCallback(
    async (findingId: string) => {
      setApplyError(null);
      setApplyingId(findingId);
      try {
        await applyChange(projectId, findingId, run.id);
        router.push(`/projects/${projectId}/changes?finding=${encodeURIComponent(findingId)}`);
      } catch (err) {
        setApplyError(err instanceof ApiError ? err.message : "Failed to start the code change.");
      } finally {
        setApplyingId(null);
      }
    },
    [projectId, run.id, router]
  );

  return (
    <div
      id={`run-${run.id}`}
      className={`card p-5 ${highlighted ? "border-brand-500/50 shadow-[0_0_0_1px_rgb(99_102_241/0.3),0_16px_40px_-24px_rgb(79_70_229/0.5)]" : ""}`}
    >
      <div className="mb-4 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-zinc-100">{AGENT_LABELS[run.agent_type]}</h3>
        <span className={`badge ${RUN_STATUS_STYLES[run.status]}`}>{run.status}</span>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-2 text-center text-xs sm:grid-cols-4">
        {[
          { label: "iterations", value: run.iterations_used },
          { label: "tool calls", value: run.tool_calls_used },
          { label: "tokens", value: run.tokens_used },
          { label: "files modified", value: run.files_modified },
        ].map((stat) => (
          <div key={stat.label} className="rounded-lg border border-zinc-800 bg-zinc-900/60 py-1.5">
            <p className="text-zinc-500">{stat.label}</p>
            <p className="font-medium tabular-nums text-zinc-200">{stat.value}</p>
          </div>
        ))}
      </div>

      {run.stopped_reason && (
        <p className="note-warning mb-3 text-xs">
          Stopped on budget limit: {run.stopped_reason}. Reporting partial progress.
        </p>
      )}
      {run.error && (
        <p role="alert" className="note-error mb-3 text-xs">
          {run.error}
        </p>
      )}

      {applyError && (
        <p role="alert" className="note-error mb-3 text-xs">
          {applyError}
        </p>
      )}

      {run.agent_type === "research" ? (
        <ResearchOutput run={run} />
      ) : run.agent_type === "code" || run.agent_type === "reviewer" ? (
        <CodeOrReviewerOutput run={run} projectId={projectId} />
      ) : (
        <>
          {omitted.count > 0 && (
            <p className="note-info mb-3 text-xs">
              {omitted.count} finding{omitted.count === 1 ? "" : "s"} left out because{" "}
              {omitted.count === 1 ? "it is" : "they are"} already fixed, validated, or in
              progress
              {omitted.findings.length > 0
                ? `: ${omitted.findings
                    .map((item) => `${item.finding_id} (${item.status})`)
                    .join(", ")}`
                : ""}
              {omitted.count > omitted.findings.length ? "…" : ""}. Crawl and audit again
              after the site is deployed to check the live page. Indexing the repository
              does not refresh these findings.
            </p>
          )}
          {interventions.length > 0 ? (
        <ul className="space-y-2.5">
          {interventions.map((item, index) => (
            <li
              key={`${item.finding_id}-${index}`}
              className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3 text-xs"
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="font-mono font-medium text-zinc-200">{item.finding_id}</p>
                <button
                  onClick={() => void apply(item.finding_id)}
                  disabled={applyingId === item.finding_id}
                  className="btn-secondary shrink-0 px-2.5 py-1 text-xs"
                >
                  {applyingId === item.finding_id ? "Starting…" : "Send to change planner →"}
                </button>
              </div>
              <p className="leading-relaxed text-zinc-400">
                <span className="text-zinc-500">Hypothesis:</span> {item.hypothesis}
              </p>
              <p className="leading-relaxed text-zinc-400">
                <span className="text-zinc-500">Intervention:</span> {item.intervention}
              </p>
              <p className="leading-relaxed text-zinc-400">
                <span className="text-zinc-500">Why it should help:</span>{" "}
                {item.expected_mechanism}
              </p>
              <p className="leading-relaxed text-zinc-400">
                <span className="text-zinc-500">Risk:</span> {item.risk}
              </p>
              {/* Guided fix workflow: inspect the real code before/instead of
                  dispatching to the change planner, then apply with an exact
                  before/after comparison. Additive; the planner flow above
                  is untouched. */}
              <GuidedFixPanel
                projectId={projectId}
                findingId={item.finding_id}
                sourceAgentRunId={run.id}
              />
            </li>
          ))}
        </ul>
          ) : omitted.count === 0 ? (
            <p className="text-xs italic text-zinc-500">No interventions from this run.</p>
          ) : null}
        </>
      )}
    </div>
  );
}

function locatorOrTitle(item: unknown): string {
  if (!item || typeof item !== "object") return String(item);
  const row = item as Record<string, unknown>;
  if (typeof row.locator === "string" && row.locator) return row.locator;
  if (typeof row.title === "string" && row.title) return row.title;
  if (typeof row.path === "string" && row.path) return row.path;
  if (typeof row.url === "string" && row.url) return row.url;
  if (typeof row.rule_id === "string" && row.rule_id) return row.rule_id;
  return JSON.stringify(row);
}

function ResearchOutput({ run }: { run: AgentRun }) {
  const output = (run.result_json ?? {}) as {
    knowledge?: unknown[];
    code?: unknown[];
    pages?: unknown[];
    sources?: string[];
    gaps?: string[];
  };
  const sections: { label: string; items: unknown[] }[] = [
    { label: "Knowledge", items: output.knowledge ?? [] },
    { label: "Code", items: output.code ?? [] },
    { label: "Pages", items: output.pages ?? [] },
  ];
  return (
    <div className="space-y-2.5 text-xs text-zinc-400">
      <p>
        {output.knowledge?.length ?? 0} knowledge item
        {(output.knowledge?.length ?? 0) === 1 ? "" : "s"} · {output.code?.length ?? 0} code item
        {(output.code?.length ?? 0) === 1 ? "" : "s"} · {output.pages?.length ?? 0} page item
        {(output.pages?.length ?? 0) === 1 ? "" : "s"}
      </p>
      {sections
        .filter((section) => section.items.length > 0)
        .map((section) => (
          <div key={section.label}>
            <p className="eyebrow mb-1">{section.label}</p>
            <ul className="list-inside list-disc space-y-0.5">
              {section.items.slice(0, 8).map((item, index) => (
                <li key={`${section.label}-${index}`}>{locatorOrTitle(item)}</li>
              ))}
              {section.items.length > 8 && <li>… {section.items.length - 8} more</li>}
            </ul>
          </div>
        ))}
      {output.sources && output.sources.length > 0 && (
        <p>
          <span className="text-zinc-500">Sources:</span> {output.sources.slice(0, 8).join("; ")}
          {output.sources.length > 8 ? ` … +${output.sources.length - 8}` : ""}
        </p>
      )}
      {output.gaps && output.gaps.length > 0 && (
        <p className="text-amber-400">Gaps: {output.gaps.join("; ")}</p>
      )}
    </div>
  );
}

function CodeOrReviewerOutput({ run, projectId }: { run: AgentRun; projectId: number }) {
  const result =
    run.result_json && !Array.isArray(run.result_json)
      ? (run.result_json as Record<string, unknown>)
      : {};
  const objective =
    run.objective_json && !Array.isArray(run.objective_json)
      ? (run.objective_json as Record<string, unknown>)
      : {};
  const plan = (result.change_plan ?? null) as ChangePlan | null;
  const findingId =
    (typeof objective.finding_id === "string" && objective.finding_id) || plan?.finding_id || null;
  const verdict = result.reviewer_verdict as
    | { approved?: boolean; reasons?: string[] }
    | undefined;
  const approved = result.approved ?? verdict?.approved;

  return (
    <div className="space-y-2 text-xs text-zinc-400">
      {findingId && (
        <p>
          <span className="text-zinc-500">Finding:</span>{" "}
          <span className="font-mono">{findingId}</span>
        </p>
      )}
      {plan && (
        <>
          <p>
            <span className="text-zinc-500">Files:</span>{" "}
            {plan.target_files.length > 0 ? plan.target_files.join(", ") : "(none)"}
          </p>
          <p>
            <span className="text-zinc-500">Expected diff:</span> {plan.expected_diff_summary}
          </p>
        </>
      )}
      {typeof result.dry_run === "boolean" && result.dry_run && (
        <p className="text-zinc-500">Preview only — not written to disk.</p>
      )}
      {approved === true && <p className="text-emerald-400">Reviewer approved.</p>}
      {approved === false && <p className="text-red-400">Reviewer rejected.</p>}
      {verdict?.reasons && verdict.reasons.length > 0 && (
        <p>
          <span className="text-zinc-500">Reasons:</span> {verdict.reasons.join("; ")}
        </p>
      )}
      {findingId && (
        <p>
          <Link
            href={`/projects/${projectId}/changes?finding=${encodeURIComponent(findingId)}`}
            className="text-brand-400 underline-offset-2 transition-colors hover:text-brand-300 hover:underline"
          >
            Open in change planner →
          </Link>
        </p>
      )}
      {!plan && !findingId && Object.keys(result).length === 0 && (
        <p className="italic text-zinc-500">No stored result for this run.</p>
      )}
    </div>
  );
}

export function RequestSummary({ run }: { run: AgentRun }) {
  const objective = run.objective_json as IntentObjective | Record<string, unknown> | null;
  const intent =
    objective && "objective" in objective && typeof objective.objective === "string"
      ? (objective as IntentObjective)
      : null;
  return (
    <div className="card mb-4 p-4 text-sm">
      {run.request_text && (
        <p className="mb-2 leading-relaxed text-zinc-200">
          <span className="eyebrow mb-1 block">Request</span>
          {run.request_text}
        </p>
      )}
      {intent && (
        <p className="text-xs leading-relaxed text-zinc-500">
          <span className="text-zinc-400">Objective:</span> {intent.objective}
          {intent.scope ? ` · scope: ${intent.scope}` : ""}
          {intent.mode ? ` · mode: ${intent.mode}` : ""}
        </p>
      )}
    </div>
  );
}
