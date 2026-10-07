"use client";

import type { JobEvent } from "@/lib/types";
import { JOB_STATUS_STYLES } from "./badges";

/** Live progress card for a running job. Every value comes from the real JobEvent stream. */
export function JobProgress({
  title,
  event,
  stages,
  matchedStageIndex,
  onDismiss,
  dismissing = false,
}: {
  title: string;
  event: JobEvent | null;
  stages?: readonly { key: string; label: string }[];
  matchedStageIndex?: number;
  /** Clears the panel once the job has reached a terminal state (failed/cancelled). */
  onDismiss?: () => void;
  dismissing?: boolean;
}) {
  if (!event) return null;

  const terminal =
    event.job_status === "failed" || event.job_status === "cancelled";

  return (
    <div className="card-section border-b-brand-500/20 bg-brand-500/[0.06] p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-brand-200">{title}</h3>
        <span className={`badge ${JOB_STATUS_STYLES[event.job_status]}`}>{event.job_status}</span>
      </div>

      {stages && matchedStageIndex !== undefined && (
        <ol className="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
          {stages.map((stage, index) => {
            const done =
              event.job_status === "succeeded" ||
              (matchedStageIndex > index && event.job_status !== "failed");
            const active = matchedStageIndex === index && event.job_status !== "failed";
            return (
              <li
                key={stage.key}
                aria-current={active ? "step" : undefined}
                className={`flex items-center gap-2 text-sm ${
                  active
                    ? "font-medium text-brand-300"
                    : done
                      ? "text-zinc-300"
                      : "text-zinc-500"
                }`}
              >
                <span
                  className={`flex size-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                    done
                      ? "bg-emerald-500 text-white"
                      : active
                        ? "animate-pulse-soft bg-brand-500 text-white"
                        : "border border-zinc-700"
                  }`}
                >
                  {done ? "✓" : ""}
                </span>
                {stage.label}
              </li>
            );
          })}
        </ol>
      )}

      {event.percent != null && (
        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
          <div
            className="progress-shimmer h-full rounded-full bg-gradient-to-r from-brand-600 to-accent-500 transition-all duration-500"
            style={{ width: `${event.percent}%` }}
          />
        </div>
      )}

      {event.message && (
        <p className="mt-2 text-xs text-zinc-400">{event.message}</p>
      )}

      {(event.job_status === "running" || event.job_status === "queued") && (
        <p className="note-info mt-3 flex items-start gap-2 text-xs">
          <span
            aria-hidden="true"
            className="animate-pulse-soft mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-sky-500/20 text-[9px] font-bold text-sky-400"
          >
            ⏳
          </span>
          <span>
            It usually takes about <strong>5 minutes</strong>. Your website is crawled page by
            page and every audit rule is evaluated, so please keep this page open. Scores,
            findings, and recommendations appear here automatically when it finishes — no need
            to click anything.
          </span>
        </p>
      )}

      {event.job_status === "queued" && (
        <p className="note-warning mt-3 text-xs">
          Job accepted and queued. It starts automatically as soon as the worker is free — no
          need to click anything. If it stays queued, check that a worker is running:{" "}
          <code className="kbd">cd backend; uv run python -m app.worker</code>
        </p>
      )}

      {event.job_status === "failed" && (
        <p role="alert" className="note-error mt-3">
          <span className="font-semibold">The job failed.</span>{" "}
          {event.error ?? "Check the input and try again."}
        </p>
      )}

      {terminal && onDismiss && (
        <div className="mt-3 flex items-center justify-end">
          <button
            type="button"
            onClick={onDismiss}
            disabled={dismissing}
            className="btn-secondary px-3 py-1.5 text-xs"
          >
            {dismissing ? "Dismissing…" : "Dismiss"}
          </button>
        </div>
      )}
    </div>
  );
}
