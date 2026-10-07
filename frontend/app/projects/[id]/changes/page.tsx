"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, getChangeDetail, listChanges } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { ProjectTabs } from "@/components/flow/ProjectTabs";
import { BackToOverview } from "@/components/flow/ui";
import { RUN_STATUS_STYLES } from "@/components/flow/badges";
import { AuditFindingCard } from "@/components/flow/AuditFindingCard";
import { DiffView, UnifiedDiffView } from "@/components/flow/DiffView";
import { getFindingCopy } from "@/lib/findingMappings";
import type {
  ChangeDetail,
  ChangeSummary,
  EvidenceRow,
  ValidationCheckStatus,
  ValidationCheckType,
  ValidationResult,
  ValidationRunStatus,
} from "@/lib/types";

const CHECK_STATUS_STYLES: Record<ValidationCheckStatus, string> = {
  passed: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  failed: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  skipped: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
  not_applicable: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
};

export default function ChangesPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const searchParams = useSearchParams();
  const { user, loading: userLoading } = useCurrentUser();

  const [changes, setChanges] = useState<ChangeSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(searchParams.get("finding"));
  const [detail, setDetail] = useState<ChangeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const loadList = useCallback(async () => {
    try {
      const rows = await listChanges(projectId);
      setChanges(rows);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load changes.");
    }
  }, [projectId]);

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void loadList();
  }, [user, loadList]);

  const loadDetail = useCallback(
    async (findingId: string) => {
      setLoadingDetail(true);
      setError(null);
      try {
        const row = await getChangeDetail(projectId, findingId);
        setDetail(row);
      } catch (err) {
        setDetail(null);
        setError(err instanceof ApiError ? err.message : "Failed to load change detail.");
      } finally {
        setLoadingDetail(false);
      }
    },
    [projectId]
  );

  useEffect(() => {
    if (!user || !selected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch detail when selection changes
    void loadDetail(selected);
  }, [user, selected, loadDetail]);

  // A change still `running` has no terminal result yet -- poll until it does.
  useEffect(() => {
    if (!selected || detail?.status !== "running") return;
    const interval = setInterval(() => {
      void loadDetail(selected);
      void loadList();
    }, 3000);
    return () => clearInterval(interval);
  }, [selected, detail?.status, loadDetail, loadList]);

  if (userLoading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 animate-fade-up px-4 py-8 sm:px-6 sm:py-10">
      <BackToOverview projectId={projectId} />
      <ProjectTabs projectId={projectId} />

      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">Code changes</h1>
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-zinc-400">
          Each row is a Code Agent run for one finding. In{" "}
          <code className="kbd">AUDIT_ONLY</code> / <code className="kbd">SUGGEST_ONLY</code> the
          diff is a preview only and is never written to disk; sandbox validation is skipped and
          the Reviewer Agent inspects the preview. In{" "}
          <code className="kbd">APPLY_LOCALLY</code> and above, the patch is snapshotted, applied,
          sandbox-validated, and independently reviewed.
        </p>
      </div>

      {error && (
        <p role="alert" className="note-error mb-6">
          {error}
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-[300px_1fr]">
        {/* Change list */}
        <ul className="card h-fit divide-y divide-zinc-800/70 overflow-hidden">
          {changes.map((row) => (
            <li key={row.agent_run_id}>
              <button
                type="button"
                onClick={() => setSelected(row.finding_id)}
                aria-pressed={selected === row.finding_id}
                className={`block w-full px-4 py-3 text-left transition-colors hover:bg-zinc-800/40 ${
                  selected === row.finding_id ? "bg-brand-500/10" : ""
                }`}
              >
                <p className="mb-1.5 truncate font-mono text-xs font-medium text-zinc-200">
                  {row.finding_id}
                </p>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={`badge ${RUN_STATUS_STYLES[row.status]}`}>{row.status}</span>
                  {row.dry_run && (
                    <span className="badge bg-zinc-500/15 text-zinc-400">preview only</span>
                  )}
                  {row.approved === true && (
                    <span className="badge bg-emerald-500/15 text-emerald-300">approved</span>
                  )}
                  {row.approved === false && (
                    <span className="badge bg-red-500/15 text-red-300">rejected</span>
                  )}
                </div>
              </button>
            </li>
          ))}
          {changes.length === 0 && (
            <li className="px-4 py-10 text-center text-sm italic text-zinc-500">
              No changes yet. Open an agent run and send a suggested finding to the change
              planner.
            </li>
          )}
        </ul>

        {/* Detail */}
        <div>
          {loadingDetail && <p className="text-sm text-zinc-500">Loading…</p>}
          {!loadingDetail && selected && detail && <ChangeDetailView detail={detail} />}
          {!loadingDetail && selected && !detail && !error && (
            <p className="text-sm text-zinc-500">No detail available.</p>
          )}
          {!selected && (
            <p className="text-sm italic text-zinc-500">
              Select a change on the left to review it.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function asList<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

function syntheticSkippedResult(
  id: number,
  checkType: ValidationCheckType,
  detail: string,
  status: ValidationCheckStatus = "skipped"
): ValidationResult {
  return { id, check_type: checkType, status, detail, duration_ms: null };
}

function displayValidation(detail: ChangeDetail): {
  status: ValidationRunStatus;
  results: ValidationResult[];
  gaps: string[];
} | null {
  if (detail.validation_run) {
    return {
      status: detail.validation_run.status,
      results: asList(detail.validation_run.results),
      gaps: asList(detail.validation_run.gaps_json),
    };
  }
  if (!detail.violation && !detail.validation_skipped) {
    return null;
  }
  const skipReason = detail.violation
    ? `stopped before sandbox: ${detail.violation.reason}`
    : "preview mode does not write to disk or execute generated code";
  const results: ValidationResult[] = [];
  let nextId = -1;
  if (detail.violation) {
    const gateType: ValidationCheckType =
      detail.violation.reason === "content_change_violation" ? "content" : "scope";
    results.push(syntheticSkippedResult(nextId--, gateType, detail.violation.detail, "failed"));
  }
  const plan = detail.validation_plan;
  const skipTypes: ValidationCheckType[] = ["build", "lint", "unit_test", "browser", "seo", "regression"];
  if (plan?.aeo_checks && plan.aeo_checks.length > 0) skipTypes.push("aeo");
  if (plan?.geo_checks && plan.geo_checks.length > 0) skipTypes.push("geo");
  for (const checkType of skipTypes) {
    if (plan && checkType === "build" && !plan.build) continue;
    if (plan && checkType === "lint" && !plan.lint) continue;
    if (plan && checkType === "unit_test" && asList(plan.tests).length === 0) continue;
    results.push(syntheticSkippedResult(nextId--, checkType, skipReason));
  }
  return {
    status: detail.violation ? "failed" : "partial",
    results,
    gaps: [skipReason],
  };
}

function ChangeDetailView({ detail }: { detail: ChangeDetail }) {
  const finding = detail.finding;
  const diffFiles = asList(detail.diff_preview?.files);
  const evidence = asList(finding?.evidence);
  const targetFiles = asList(detail.change_plan?.target_files);
  const executionSteps = asList(detail.execution_plan?.steps);
  const reviewerReasons = asList(detail.reviewer_verdict?.reasons);
  const regressions = asList(detail.reviewer_verdict?.regressions_detected);
  const validation = displayValidation(detail);
  const reviewerSkipped =
    Boolean(detail.reviewer_skipped) ||
    (Boolean(detail.violation) && !detail.reviewer_verdict);
  return (
    <div className="space-y-4">
      <div className="card p-5">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-zinc-100">
            {getFindingCopy(detail.finding_id.split(":")[0]).title}
          </h2>
          <span className={`badge ${RUN_STATUS_STYLES[detail.status]}`}>{detail.status}</span>
          <span className="badge bg-zinc-500/15 text-zinc-400">finding: {detail.finding_status}</span>
          {detail.dry_run && (
            <span className="badge bg-zinc-500/15 text-zinc-400">preview only — not applied</span>
          )}
        </div>

        {detail.error && (
          <p role="alert" className="note-error mb-3 text-xs">
            {detail.error}
          </p>
        )}

        {detail.violation && (
          <div className="note-warning mb-3 text-xs">
            <p className="font-medium">Stopped: {detail.violation.reason}</p>
            <p>{detail.violation.detail}</p>
          </div>
        )}

        {(detail.dry_run || detail.validation_skipped) && (
          <p className="note-info mb-3 text-xs">
            Sandbox validation skipped — preview mode does not write to disk or execute generated
            code.
            {detail.reviewer_verdict ? " The Reviewer Agent inspected this preview." : ""}
          </p>
        )}
        {detail.violation && (
          <p className="mb-3 rounded border border-zinc-200 bg-zinc-50 p-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
            Sandbox build, lint, tests, and browser checks did not run. The Code Agent stopped
            before writing the patch, so those steps were skipped. The Reviewer Agent did not run.
          </p>
        )}

        {finding && (
          <div className="mb-3">
            <AuditFindingCard
              ruleId={detail.finding_id.split(":")[0]}
              observation={finding.observation}
              affectedResource={finding.affected_resource}
              evidence={evidence}
              variant="compact"
              findingId={detail.finding_id}
              recommendedAction={finding.recommended_action}
            />
          </div>
        )}

        {detail.platform_notes?.map((note) => (
          <p key={note} className="note-warning mb-3 text-xs">
            {note}
          </p>
        ))}

        {Boolean(detail.repair_attempts) && (
          <p className="note-info mb-3 text-xs">
            The first patch failed the build, so the Code Agent was given the build error and
            produced a corrected patch ({detail.repair_attempts}{" "}
            {detail.repair_attempts === 1 ? "repair round" : "repair rounds"}).
          </p>
        )}

        {detail.workspace_files && detail.workspace_files.length > 0 && (
          <p className="mb-3 text-xs text-zinc-400">
            <span className="text-zinc-500">Workspace:</span>{" "}
            {detail.workspace_files.map((file) => `${file.path} (${file.status})`).join(", ")}
          </p>
        )}

        {detail.change_plan && (
          <div className="mb-3 border-t border-zinc-800/70 pt-3">
            <p className="eyebrow mb-1.5">Change Plan</p>
            <p className="text-xs leading-relaxed text-zinc-300">
              <span className="text-zinc-500">Files:</span>{" "}
              {targetFiles.length > 0 ? targetFiles.join(", ") : "(none named)"}
            </p>
            <p className="text-xs leading-relaxed text-zinc-300">
              <span className="text-zinc-500">Reuse:</span> {detail.change_plan.reuse_notes}
            </p>
            <p className="text-xs leading-relaxed text-zinc-300">
              <span className="text-zinc-500">Expected diff:</span>{" "}
              {detail.change_plan.expected_diff_summary}
            </p>
            {asList(detail.change_plan.required_tests).length > 0 && (
              <p className="text-xs leading-relaxed text-zinc-300">
                <span className="text-zinc-500">Tests:</span>{" "}
                {detail.change_plan.required_tests.join(", ")}
              </p>
            )}
            {asList(detail.change_plan.required_validation).length > 0 && (
              <p className="text-xs leading-relaxed text-zinc-300">
                <span className="text-zinc-500">Validation:</span>{" "}
                {detail.change_plan.required_validation.join(", ")}
              </p>
            )}
          </div>
        )}

        {detail.execution_plan && (
          <div className="mb-3 border-t border-zinc-800/70 pt-3">
            <p className="eyebrow mb-1.5">Execution Plan</p>
            {executionSteps.length > 0 ? (
              <ol className="list-inside list-decimal text-xs leading-relaxed text-zinc-300">
                {executionSteps.map((step, index) => (
                  <li key={`${step.order}-${index}`}>
                    {step.action}: {step.detail}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-xs italic text-zinc-500">No execution steps stored.</p>
            )}
            {asList(detail.execution_plan.sandbox_operations).length > 0 && (
              <p className="mt-1 text-xs leading-relaxed text-zinc-300">
                <span className="text-zinc-500">Sandbox:</span>{" "}
                {detail.execution_plan.sandbox_operations.join(", ")}
              </p>
            )}
          </div>
        )}

        {detail.validation_plan && (
          <div className="border-t border-zinc-800/70 pt-3">
            <p className="eyebrow mb-1.5">Validation Plan</p>
            <p className="text-xs leading-relaxed text-zinc-300">
              <span className="text-zinc-500">Build:</span>{" "}
              {detail.validation_plan.build ? "yes" : "no"} ·{" "}
              <span className="text-zinc-500">Lint:</span>{" "}
              {detail.validation_plan.lint ? "yes" : "no"}
            </p>
            {asList(detail.validation_plan.tests).length > 0 && (
              <p className="text-xs leading-relaxed text-zinc-300">
                <span className="text-zinc-500">Tests:</span>{" "}
                {detail.validation_plan.tests.join(", ")}
              </p>
            )}
            {asList(detail.validation_plan.seo_checks).length > 0 && (
              <p className="text-xs leading-relaxed text-zinc-300">
                <span className="text-zinc-500">SEO checks:</span>{" "}
                {detail.validation_plan.seo_checks.join("; ")}
              </p>
            )}
            {asList(detail.validation_plan.aeo_checks).length > 0 && (
              <p className="text-xs leading-relaxed text-zinc-300">
                <span className="text-zinc-500">AEO checks:</span>{" "}
                {detail.validation_plan.aeo_checks.join("; ")}
              </p>
            )}
            {asList(detail.validation_plan.geo_checks).length > 0 && (
              <p className="text-xs leading-relaxed text-zinc-300">
                <span className="text-zinc-500">GEO checks:</span>{" "}
                {detail.validation_plan.geo_checks.join("; ")}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Diff */}
      {diffFiles.length > 0 ? (
        <div className="card p-5">
          <h3 className="mb-3 text-sm font-semibold text-zinc-100">Diff</h3>
          {detail.diff_preview?.notes && (
            <p className="mb-3 text-xs text-zinc-500">{detail.diff_preview.notes}</p>
          )}
          <div className="space-y-3">
            {diffFiles.map((file, index) => (
              <div
                key={file.file_path || `file-${index}`}
                className="overflow-hidden rounded-xl border border-zinc-800"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 bg-zinc-900/70 px-3 py-2 text-xs">
                  <span className="font-mono text-zinc-300">{file.file_path}</span>
                  <span className="text-zinc-500">
                    {file.file_status ? `${file.file_status} · ` : ""}
                    <span className="text-emerald-400">+{file.lines_added}</span>{" "}
                    <span className="text-red-400">-{file.lines_removed}</span>
                  </span>
                </div>
                <p className="px-3 py-2 text-xs text-zinc-400">{file.change_summary}</p>
                {(file.before !== undefined || file.after !== undefined) && (
                  <div className="grid grid-cols-1 gap-2 border-t border-zinc-800 p-3 text-xs md:grid-cols-2">
                    <div>
                      <p className="eyebrow mb-1">Before</p>
                      <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950/70 p-2 text-zinc-400">
                        {file.before || "(empty / missing)"}
                      </pre>
                    </div>
                    <div>
                      <p className="eyebrow mb-1">After</p>
                      <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950/70 p-2 text-zinc-400">
                        {file.after || "(empty)"}
                      </pre>
                    </div>
                  </div>
                )}
                <div className="border-t border-zinc-800 px-3 py-2.5">
                  <p className="eyebrow mb-1.5">Changes</p>
                  {file.unified_diff ? (
                    <UnifiedDiffView diff={file.unified_diff} />
                  ) : file.before !== undefined || file.after !== undefined ? (
                    <DiffView before={file.before || ""} after={file.after || ""} />
                  ) : (
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs text-zinc-400">
                      {file.unified_diff || "(no textual diff)"}
                    </pre>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : detail.status !== "running" && (detail.change_plan || detail.violation) ? (
        <div className="card p-5 text-xs text-zinc-500">
          <h3 className="mb-2 text-sm font-semibold text-zinc-200">Diff</h3>
          {detail.violation
            ? "No patch was written — the Code Agent stopped before producing a diff. The change planner output is above."
            : "The change planner finished, but this run has no stored file diff. The plan itself is still shown above."}
        </div>
      ) : null}

      {/* Validation run */}
      {detail.validation_run && (
        <div className="card p-5">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Validation</h3>
            <span
              className={`badge ${
                detail.validation_run.status === "passed"
                  ? CHECK_STATUS_STYLES.passed
                  : detail.validation_run.status === "failed"
                    ? CHECK_STATUS_STYLES.failed
                    : CHECK_STATUS_STYLES.skipped
              }`}
            >
              {detail.validation_run.status}
            </span>
          </div>
          {detail.validation_run.affected_urls_json &&
            detail.validation_run.affected_urls_json.length > 0 && (
              <p className="mb-2 break-all text-xs text-zinc-400">
                Targeted URLs: {detail.validation_run.affected_urls_json.join(", ")}
              </p>
            )}
          {detail.validation_run.gaps_json && detail.validation_run.gaps_json.length > 0 && (
            <p className="mb-2 text-xs text-amber-400">
              Gaps: {detail.validation_run.gaps_json.join("; ")}
            </p>
          )}
          <ul className="space-y-1.5">
            {asList(detail.validation_run.results).map((result) => (
              <li key={result.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-mono text-zinc-400">{result.check_type}</span>
                <span className="min-w-0 flex-1 truncate text-zinc-500">{result.detail}</span>
                <span className={`badge shrink-0 ${CHECK_STATUS_STYLES[result.status]}`}>
                  {result.status}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Reviewer verdict */}
      {detail.reviewer_verdict && (
        <div className="card p-5">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Reviewer Agent</h3>
            <span
              className={`badge ${
                detail.reviewer_verdict.approved
                  ? CHECK_STATUS_STYLES.passed
                  : CHECK_STATUS_STYLES.failed
              }`}
            >
              {reviewerSkipped
                ? "skipped"
                : detail.reviewer_verdict?.approved
                  ? "approved"
                  : "rejected"}
            </span>
          </div>
          <ul className="list-inside list-disc text-xs leading-relaxed text-zinc-400">
            {reviewerReasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
          {regressions.length > 0 && (
            <p className="mt-2 text-xs text-red-400">
              Regressions: {regressions.join("; ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
