import type {
  AgentRunStatus,
  FindingStatus,
  JobStatus,
  RuleCategory,
  RuleConfidence,
  RuleSeverity,
} from "@/lib/types";

/* Severity ---------------------------------------------------------------- */
export const SEVERITY_STYLES: Record<RuleSeverity, string> = {
  critical: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  high: "bg-orange-500/15 text-orange-300 ring-1 ring-inset ring-orange-500/30",
  medium: "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30",
  low: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
};

export const SEVERITY_OPTIONS: { value: RuleSeverity | ""; label: string }[] = [
  { value: "", label: "All severities" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

/* Category ---------------------------------------------------------------- */
export const CATEGORY_LABELS: Record<RuleCategory, string> = {
  technical_seo: "Technical SEO",
  content_seo: "Content SEO",
  aeo: "AEO",
  geo: "GEO",
  agent_accessibility: "Agent Accessibility",
};

export const CATEGORY_STYLES: Record<RuleCategory, string> = {
  technical_seo: "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/30",
  content_seo: "bg-violet-500/15 text-violet-300 ring-1 ring-inset ring-violet-500/30",
  aeo: "bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30",
  geo: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  agent_accessibility: "bg-pink-500/15 text-pink-300 ring-1 ring-inset ring-pink-500/30",
};

/* Confidence -------------------------------------------------------------- */
export const CONFIDENCE_OPTIONS: { value: RuleConfidence | ""; label: string }[] = [
  { value: "", label: "All confidence" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

/* Job / run / finding status ---------------------------------------------- */
export const JOB_STATUS_STYLES: Record<JobStatus, string> = {
  queued: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
  running: "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/30",
  succeeded: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  failed: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  cancelled: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
};

export const RUN_STATUS_STYLES: Record<AgentRunStatus, string> = {
  running: JOB_STATUS_STYLES.running,
  succeeded: JOB_STATUS_STYLES.succeeded,
  partial: JOB_STATUS_STYLES.queued,
  failed: JOB_STATUS_STYLES.failed,
  cancelled: JOB_STATUS_STYLES.cancelled,
};

export const FINDING_STATUS_STYLES: Record<FindingStatus, string> = {
  OPEN: "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30",
  PLANNED: "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/30",
  IN_PROGRESS: "bg-brand-500/15 text-brand-300 ring-1 ring-inset ring-brand-500/30",
  VALIDATED: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  REJECTED: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
  FIXED: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  ROLLED_BACK: "bg-zinc-500/15 text-zinc-400 ring-1 ring-inset ring-zinc-500/30",
};

export const FINDING_STATUS_OPTIONS: { value: FindingStatus | ""; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "OPEN", label: "Open" },
  { value: "PLANNED", label: "Planned" },
  { value: "IN_PROGRESS", label: "In progress" },
  { value: "VALIDATED", label: "Validated" },
  { value: "REJECTED", label: "Rejected" },
  { value: "FIXED", label: "Fixed" },
  { value: "ROLLED_BACK", label: "Rolled back" },
];

/** Tiny labeled status dot — communicates status without relying on color alone. */
export function StatusDot({ tone, label }: { tone: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-zinc-400">
      <span aria-hidden="true" className={`size-1.5 rounded-full ${tone}`} />
      {label}
    </span>
  );
}
