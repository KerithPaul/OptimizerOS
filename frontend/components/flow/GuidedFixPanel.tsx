"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  applyChange,
  getChangeDetail,
  getFileBeforeAfter,
  getFileChangesSummary,
  inspectChangeCode,
  snapshotOriginalFile,
} from "@/lib/api";
import type {
  ChangeDetail,
  CodeInspection,
  FileBeforeAfter,
  FileChangesSummary,
  FileOriginalSnapshot,
} from "@/lib/types";
import { DiffView } from "./DiffView";

/**
 * Guided fix workflow, rendered inside the existing Agent section:
 *
 *   Recommendation -> [Inspect Code] -> Current Implementation panel
 *   -> [Apply Fix] -> Fix Completed (Before/After/Diff + validation)
 *
 * Inspection and comparison are read-only against the real workspace and
 * the immutable file snapshot. Applying a fix goes through the existing
 * changes pipeline (POST /changes/apply) unchanged; this component only
 * captures the pre-modification original first (STEP 3) so the exact
 * before/after can always be shown.
 */

export function GuidedFixPanel({
  projectId,
  findingId,
  sourceAgentRunId,
}: {
  projectId: number;
  findingId: string;
  sourceAgentRunId: number;
}) {
  const [inspection, setInspection] = useState<CodeInspection | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);
  const [inspectError, setInspectError] = useState<string | null>(null);

  const [snapshots, setSnapshots] = useState<FileOriginalSnapshot[]>([]);
  const [snapshotting, setSnapshotting] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);

  const [detail, setDetail] = useState<ChangeDetail | null>(null);
  const [comparison, setComparison] = useState<FileBeforeAfter | null>(null);
  const [summary, setSummary] = useState<FileChangesSummary | null>(null);

  const runInspection = useCallback(async () => {
    setInspectLoading(true);
    setInspectError(null);
    try {
      const result = await inspectChangeCode(projectId, findingId);
      setInspection(result);
    } catch (err) {
      setInspectError(err instanceof ApiError ? err.message : "Failed to inspect the code.");
    } finally {
      setInspectLoading(false);
    }
  }, [projectId, findingId]);

  const applyFix = useCallback(async () => {
    setApplyError(null);
    if (!inspection) return;
    const present = inspection.files.filter((file) => file.status === "present");
    const target = present.length > 0 ? present : inspection.files.slice(0, 1);
    if (target.length === 0) {
      setApplyError("No file to snapshot — the affected file could not be located.");
      return;
    }
    setSnapshotting(true);
    try {
      const captured: FileOriginalSnapshot[] = [];
      for (const file of target) {
        captured.push(await snapshotOriginalFile(projectId, findingId, file.path));
      }
      setSnapshots(captured);
    } catch (err) {
      setApplyError(err instanceof ApiError ? err.message : "Failed to save the original file.");
      setSnapshotting(false);
      return;
    }
    setSnapshotting(false);

    setApplying(true);
    try {
      await applyChange(projectId, findingId, sourceAgentRunId);
      const row = await getChangeDetail(projectId, findingId);
      setDetail(row);
    } catch (err) {
      setApplyError(err instanceof ApiError ? err.message : "Failed to start the fix.");
    } finally {
      setApplying(false);
    }
  }, [projectId, findingId, sourceAgentRunId, inspection]);

  // While the fix pipeline runs, poll the existing change detail endpoint
  // (same contract the changes page uses) until a terminal status.
  useEffect(() => {
    if (!detail || detail.status !== "running") return;
    const interval = setInterval(() => {
      void (async () => {
        try {
          const row = await getChangeDetail(projectId, findingId);
          setDetail(row);
          if (row.status !== "running") {
            const path = comparisonFile(row);
            if (path) {
              const comp = await getFileBeforeAfter(projectId, findingId, path).catch(() => null);
              if (comp) setComparison(comp);
            }
            const sum = await getFileChangesSummary(projectId, findingId).catch(() => null);
            if (sum) setSummary(sum);
          }
        } catch {
          // transient poll error; keep polling
        }
      })();
    }, 3000);
    return () => clearInterval(interval);
  }, [detail, projectId, findingId]);

  // Once the run finishes, fetch the real before/after from the snapshot.
  useEffect(() => {
    if (!detail || detail.status === "running") return;
    if (comparison || summary) return;
    const path = comparisonFile(detail);
    if (!path) return;
    void (async () => {
      const comp = await getFileBeforeAfter(projectId, findingId, path).catch(() => null);
      if (comp) setComparison(comp);
      const sum = await getFileChangesSummary(projectId, findingId).catch(() => null);
      if (sum) setSummary(sum);
    })();
  }, [detail, comparison, summary, projectId, findingId]);

  return (
    <div className="mt-3 space-y-3 border-t border-zinc-800/70 pt-3">
      {/* STEP 1/2 — inspect */}
      <button
        type="button"
        onClick={() => void runInspection()}
        disabled={inspectLoading || inspection !== null}
        className="btn-secondary px-3 py-1.5 text-xs"
      >
        {inspectLoading ? "Inspecting…" : "Inspect Code"}
      </button>

      {inspectError && (
        <p role="alert" className="note-error text-xs">
          {inspectError}
        </p>
      )}

      {inspection && <InspectionView inspection={inspection} />}

      {/* STEP 3/4 — snapshot + apply */}
      {inspection && inspection.applies && (
        <div>
          {inspection.actionability &&
            inspection.actionability !== "code_change" &&
            inspection.actionability !== "code_or_platform_change" && (
              <p className="note-warning mb-2">
                The current implementation was inspected, but this recommendation is a content/
                CMS action (actionability={inspection.actionability}), not a code change — it is
                applied through the platform flow, not by patching source files.
              </p>
            )}
          {inspection.actionability === "code_change" ||
          inspection.actionability === "code_or_platform_change" ? (
            <button
              type="button"
              onClick={() => void applyFix()}
              disabled={snapshotting || applying}
              className="btn-primary px-3 py-1.5 text-xs"
            >
              {snapshotting ? "Saving original…" : applying ? "Applying fix…" : "Apply Fix"}
            </button>
          ) : null}
          {snapshots.length > 0 && (
            <p className="mt-2 text-xs text-zinc-500">
              Original saved immutably (snapshot{" "}
              {snapshots.map((s) => `#${s.snapshot_id}`).join(", ")}) before any modification.
            </p>
          )}
        </div>
      )}
      {applyError && (
        <p role="alert" className="note-error text-xs">
          {applyError}
        </p>
      )}

      {/* STEP 5-8 — fix completed */}
      {detail && detail.status !== "running" && (
        <FixCompletedView
          detail={detail}
          comparison={comparison}
          summary={summary}
        />
      )}
      {detail && detail.status === "running" && (
        <p className="note-info text-xs">
          Fix pipeline is running — the change planner, patch, validation, and reviewer run in the
          background. This panel updates automatically.
        </p>
      )}
    </div>
  );
}

/** The first workspace file the fix pipeline will touch, for the comparison fetch. */
function comparisonFile(detail: ChangeDetail): string | null {
  const files = detail.diff_preview?.files;
  if (Array.isArray(files) && files.length > 0) {
    const first = files[0] as { file_path?: string };
    if (typeof first?.file_path === "string" && first.file_path) return first.file_path;
  }
  const planned = detail.change_plan?.target_files;
  if (Array.isArray(planned) && planned.length > 0 && typeof planned[0] === "string") {
    return planned[0];
  }
  return null;
}

function InspectionView({ inspection }: { inspection: CodeInspection }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3 text-xs">
      <p className="eyebrow mb-2">Current Implementation</p>
      {inspection.files.length === 0 ? (
        <p className="italic text-zinc-500">{inspection.applies_reason}</p>
      ) : (
        <div className="space-y-3">
          {inspection.files.map((file) => (
            <div key={file.path}>
              <p className="font-mono text-zinc-300">
                {file.path}{" "}
                <span
                  className={`badge ml-1 ${
                    file.status === "present"
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-zinc-500/15 text-zinc-400"
                  }`}
                >
                  {file.status}
                </span>
              </p>
              {file.status === "present" && (
                <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950/70 p-2 leading-relaxed text-zinc-400">
                  {file.content}
                </pre>
              )}
              {file.locator_hits.length > 0 && (
                <p className="mt-1 text-zinc-500">
                  Evidence located here: {file.locator_hits.slice(0, 3).join(", ")}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="mt-3 space-y-1.5 border-t border-zinc-800/70 pt-2.5">
        <p>
          <span className="text-zinc-500">Detected issue:</span> {inspection.detected_issue}
        </p>
        <p>
          <span className="text-zinc-500">Why the recommendation applies:</span>{" "}
          {inspection.applies_reason}
        </p>
        <p>
          <span className="text-zinc-500">Planned fix:</span> {inspection.recommended_action}
        </p>
      </div>
      {!inspection.applies && (
        <p className="mt-2.5 note-warning">
          Unable to safely apply this recommendation. No files will be modified.
        </p>
      )}
    </div>
  );
}

function FixCompletedView({
  detail,
  comparison,
  summary,
}: {
  detail: ChangeDetail;
  comparison: FileBeforeAfter | null;
  summary: FileChangesSummary | null;
}) {
  const [tab, setTab] = useState<"before" | "after" | "diff">("diff");
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3 text-xs">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="eyebrow">Fix Completed</p>
        <span
          className={`badge ${
            detail.status === "succeeded"
              ? "bg-emerald-500/15 text-emerald-300"
              : detail.status === "failed"
                ? "bg-red-500/15 text-red-300"
                : "bg-zinc-500/15 text-zinc-400"
          }`}
        >
          {detail.status}
          {detail.approved === true ? " · approved" : detail.approved === false ? " · rejected" : ""}
        </span>
      </div>

      {detail.error && (
        <p role="alert" className="note-error mb-2">
          {detail.error}
        </p>
      )}
      {detail.violation && (
        <p className="note-warning mb-2">
          Stopped: {detail.violation.reason} — {detail.violation.detail}
        </p>
      )}

      {comparison ? (
        <div>
          <p className="mb-1.5 font-mono text-zinc-300">{comparison.file_path}</p>
          <div className="mb-2 flex flex-wrap items-center gap-1.5" role="tablist">
            {(["before", "after", "diff"] as const).map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className={`rounded-lg px-3 py-1.5 transition-colors ${
                  tab === key
                    ? "bg-brand-500/15 font-medium text-brand-300 ring-1 ring-inset ring-brand-500/30"
                    : "bg-zinc-800/60 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                }`}
              >
                {key}
              </button>
            ))}
          </div>
          {tab === "before" && (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950/70 p-2 leading-relaxed text-zinc-400">
              {comparison.before || "(empty)"}
            </pre>
          )}
          {tab === "after" && (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950/70 p-2 leading-relaxed text-zinc-400">
              {comparison.after || "(empty)"}
            </pre>
          )}
          {tab === "diff" && (
            <DiffView before={comparison.before} after={comparison.after} />
          )}
        </div>
      ) : (
        <p className="italic text-zinc-500">
          {detail.status === "succeeded"
            ? "Loading the before/after comparison…"
            : "No file comparison available — no patch was written."}
        </p>
      )}

      {summary && summary.files.length > 0 && (
        <div className="mt-3 border-t border-zinc-800/70 pt-2.5">
          <p className="eyebrow mb-1.5">Modified Files</p>
          <ol className="list-inside list-decimal space-y-1">
            {summary.files.map((file) => (
              <li key={`${file.snapshot_id}-${file.file_path}`}>
                <span className="font-mono text-zinc-300">{file.file_path}</span>{" "}
                {file.changed ? (
                  <span className="text-emerald-400">
                    +{file.lines_added} −{file.lines_removed}
                  </span>
                ) : (
                  <span className="text-zinc-500">unchanged</span>
                )}
                {file.error && <span className="text-red-400"> ({file.error})</span>}
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* STEP 7/8 — validation result and the preserved recommendation */}
      {detail.validation_run && (
        <div className="mt-3 border-t border-zinc-800/70 pt-2.5">
          <p className="eyebrow mb-1.5">Validation</p>
          <ul className="space-y-1">
            {Array.isArray(detail.validation_run.results) &&
              detail.validation_run.results.map((result) => (
                <li key={result.id} className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-zinc-400">{result.check_type}</span>
                  <span
                    className={
                      result.status === "passed"
                        ? "text-emerald-400"
                        : result.status === "failed"
                          ? "text-red-400"
                          : "text-zinc-500"
                    }
                  >
                    {result.status === "passed" ? "✓" : result.status === "failed" ? "⚠" : "–"}{" "}
                    {result.status}
                  </span>
                  {result.detail && (
                    <span className="min-w-0 flex-1 truncate text-zinc-500">{result.detail}</span>
                  )}
                </li>
              ))}
          </ul>
        </div>
      )}
      {detail.finding && (
        <p className="mt-3 border-t border-zinc-800/70 pt-2.5 text-zinc-400">
          <span className="text-zinc-500">Recommendation (unchanged):</span>{" "}
          {detail.finding.recommended_action}
        </p>
      )}
    </div>
  );
}
